"""Túnel de ngrok que abre el servidor (con un ngrok simulado: no se conecta a nada)."""
import logging
import sys
import time

import pytest

from app import tunnel
from app.tunnel import NgrokTunnel, _field, clean_domain, command


def test_dominio_con_o_sin_https():
    assert clean_domain("https://abc-123.ngrok-free.dev/") == "abc-123.ngrok-free.dev"
    assert clean_domain(" abc-123.ngrok-free.dev ") == "abc-123.ngrok-free.dev"


def test_comando_de_ngrok():
    assert command("ngrok", "abc.ngrok-free.dev", 8000)[:4] == ["ngrok", "http", "--url=abc.ngrok-free.dev", "8000"]


def test_lee_los_campos_del_registro_de_ngrok():
    line = 't=2026 lvl=eror msg="failed to start tunnel" err="ERR_NGROK_4018: authentication failed"'
    assert _field(line, "lvl") == "eror"
    assert _field(line, "err") == "ERR_NGROK_4018: authentication failed"


def test_sin_ngrok_instalado_sigue_sin_tunel(monkeypatch, caplog):
    monkeypatch.setattr(tunnel, "find_ngrok", lambda: None)
    with caplog.at_level(logging.WARNING, logger="ngrok"):
        assert NgrokTunnel("abc.ngrok-free.dev", 8000).start() is False
    assert "winget install ngrok.ngrok" in caplog.text


@pytest.mark.skipif(sys.platform == "win32", reason="ngrok simulado con un script de shell")
def test_arranca_y_apaga_el_tunel(tmp_path, monkeypatch, caplog):
    fake = tmp_path / "ngrok"
    fake.write_text('#!/bin/sh\necho \'t=1 lvl=info msg="started tunnel" url=https://abc.ngrok-free.dev\'\n'
                    'exec sleep 30\n')
    fake.chmod(0o755)
    monkeypatch.setattr(tunnel, "find_ngrok", lambda: str(fake))
    t = NgrokTunnel("https://abc.ngrok-free.dev", 8000)
    with caplog.at_level(logging.INFO, logger="ngrok"):
        assert t.start() is True
        for _ in range(50):
            if "Túnel abierto" in caplog.text:
                break
            time.sleep(0.05)
        proc = t._proc
        t.stop()
    assert "https://abc.ngrok-free.dev/whatsapp/webhook" in caplog.text
    assert proc.poll() is not None          # ngrok quedó apagado
    assert "se cerró" not in caplog.text    # apagarlo nosotros no es un error


@pytest.mark.skipif(sys.platform == "win32", reason="ngrok simulado con un script de shell")
def test_si_ngrok_falla_lo_avisa(tmp_path, monkeypatch, caplog):
    fake = tmp_path / "ngrok"
    fake.write_text('#!/bin/sh\necho \'t=1 lvl=eror msg="failed" err="ERR_NGROK_4018: authentication failed"\'\n'
                    'exit 1\n')
    fake.chmod(0o755)
    monkeypatch.setattr(tunnel, "find_ngrok", lambda: str(fake))
    with caplog.at_level(logging.INFO, logger="ngrok"):
        t = NgrokTunnel("abc.ngrok-free.dev", 8000)
        t.grace = 0.05
        t.start()
        for _ in range(50):
            if "se cerró" in caplog.text:
                break
            time.sleep(0.05)
    assert "ERR_NGROK_4018" in caplog.text and "authtoken" in caplog.text


@pytest.mark.skipif(sys.platform == "win32", reason="ngrok simulado con un script de shell")
def test_si_se_corta_internet_lo_avisa_y_vuelve_a_abrir_ngrok(tmp_path, monkeypatch, caplog):
    fake = tmp_path / "ngrok"
    fake.write_text('#!/bin/sh\necho \'t=1 lvl=info msg="started tunnel" url=https://abc.ngrok-free.dev\'\n'
                    'echo \'t=2 lvl=eror msg="heartbeat timeout, terminating session" err="heartbeat timeout"\'\n'
                    'exit 1\n')
    fake.chmod(0o755)
    monkeypatch.setattr(tunnel, "find_ngrok", lambda: str(fake))
    t = NgrokTunnel("abc.ngrok-free.dev", 8000)
    t.grace = 0.01
    monkeypatch.setattr(tunnel.time, "monotonic", lambda: 1000.0 + time.time() * 100)  # «hace rato» que arrancó
    with caplog.at_level(logging.INFO, logger="ngrok"):
        t.start()
        for _ in range(100):
            if "lo vuelvo a abrir" in caplog.text:
                break
            time.sleep(0.02)
        t.stop()
    assert "Reconectando solo" in caplog.text and "lo vuelvo a abrir" in caplog.text
    assert "authtoken" not in caplog.text
