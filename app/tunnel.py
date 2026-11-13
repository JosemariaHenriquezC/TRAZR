"""
Túnel de ngrok para que Meta (WhatsApp) llegue a la web que corre en la PC.

Si el .env tiene NGROK_DOMAIN (el dominio fijo gratis de tu cuenta de ngrok), `python -m
app.server` abre el túnel solo, en el mismo proceso: un comando levanta web + bot + túnel,
y Ctrl+C apaga todo. Sin NGROK_DOMAIN no se abre ningún túnel.

ngrok se busca en el PATH (instalado con `winget install ngrok.ngrok`) o en ~/ngrok/.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import threading
from pathlib import Path

log = logging.getLogger("ngrok")


def find_ngrok() -> str | None:
    found = shutil.which("ngrok")
    if found:
        return found
    for candidate in (Path.home() / "ngrok" / "ngrok.exe", Path.home() / "ngrok" / "ngrok"):
        if candidate.is_file():
            return str(candidate)
    return None


def clean_domain(domain: str) -> str:
    """Acepta el dominio con o sin https:// y sin barra final."""
    domain = re.sub(r"^https?://", "", (domain or "").strip())
    return domain.split("/")[0]


def command(ngrok: str, domain: str, port: int) -> list[str]:
    return [ngrok, "http", f"--url={domain}", str(port), "--log=stdout", "--log-format=logfmt"]


def _field(line: str, key: str) -> str:
    m = re.search(rf'\b{key}=("([^"]*)"|\S+)', line)
    if not m:
        return ""
    return m.group(2) if m.group(2) is not None else m.group(1)


class NgrokTunnel:
    def __init__(self, domain: str, port: int) -> None:
        self.domain = clean_domain(domain)
        self.port = port
        self._proc: subprocess.Popen | None = None
        self._stopped = threading.Event()
        self.grace = 3.0   # segundos que se espera antes de avisar que ngrok se cerró

    @property
    def webhook_url(self) -> str:
        return f"https://{self.domain}/whatsapp/webhook"

    def start(self) -> bool:
        ngrok = find_ngrok()
        if ngrok is None:
            log.warning("No encuentro ngrok: instalalo con «winget install ngrok.ngrok» (y reabrí la terminal). "
                        "Sigo sin túnel: WhatsApp no va a recibir mensajes.")
            return False
        try:
            self._proc = subprocess.Popen(
                command(ngrok, self.domain, self.port), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
                env={**os.environ})
        except OSError as exc:
            log.error("No pude arrancar ngrok: %s", exc)
            return False
        threading.Thread(target=self._read, name="ngrok-log", daemon=True).start()
        return True

    def _read(self) -> None:
        """Pasa a nuestro registro solo lo importante de ngrok (arranque y errores)."""
        proc = self._proc
        assert proc and proc.stdout
        for line in proc.stdout:
            line = line.strip()
            level, msg = _field(line, "lvl"), _field(line, "msg") or line
            if "started tunnel" in msg:
                log.info("Túnel abierto: Meta tiene que avisar a %s", self.webhook_url)
            elif level in ("eror", "crit") or "ERR_NGROK" in line:
                err = _field(line, "err") or msg
                log.error("ngrok: %s", err)
        code = proc.wait()
        # Con Ctrl+C, ngrok recibe la señal junto con el servidor y se cierra un instante antes
        # de que lo apaguemos nosotros: eso no es un error. Se espera un poco antes de avisar.
        if self._stopped.wait(timeout=self.grace):
            return
        if code not in (0, None):
            log.error("ngrok se cerró (código %s). Revisá el authtoken (ngrok config add-authtoken …) y que "
                      "NGROK_DOMAIN sea el dominio de tu cuenta. La web y Telegram siguen funcionando.", code)

    def stop(self) -> None:
        self._stopped.set()
        proc, self._proc = self._proc, None
        if proc is None or proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
