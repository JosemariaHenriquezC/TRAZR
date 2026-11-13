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
import time
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
        self.failures = 0  # cierres seguidos (si se cierra enseguida 3 veces, es la configuración)
        self._ngrok = ""
        self._started_at = 0.0

    @property
    def webhook_url(self) -> str:
        return f"https://{self.domain}/whatsapp/webhook"

    def start(self) -> bool:
        ngrok = find_ngrok()
        if ngrok is None:
            log.warning("No encuentro ngrok: instalalo con «winget install ngrok.ngrok» (y reabrí la terminal). "
                        "Sigo sin túnel: WhatsApp no va a recibir mensajes.")
            return False
        self._ngrok = ngrok
        return self._spawn()

    def _spawn(self) -> bool:
        try:
            self._proc = subprocess.Popen(
                command(self._ngrok, self.domain, self.port), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
                env={**os.environ})
        except OSError as exc:
            log.error("No pude arrancar ngrok: %s", exc)
            return False
        self._started_at = time.monotonic()
        threading.Thread(target=self._read, args=(self._proc,), name="ngrok-log", daemon=True).start()
        return True

    def _read(self, proc: subprocess.Popen) -> None:
        """Pasa a nuestro registro solo lo importante de ngrok (arranque, cortes y errores).
        Si ngrok se cierra solo (se cortó internet, la PC se suspendió), lo vuelve a abrir."""
        assert proc.stdout
        dropped, config_error = False, False
        for line in proc.stdout:
            line = line.strip()
            level, msg = _field(line, "lvl"), _field(line, "msg") or line
            err = _field(line, "err") or msg
            if "started tunnel" in msg or ("session" in msg and "start" in msg and dropped):
                log.info("Túnel %s: Meta tiene que avisar a %s", "reconectado" if dropped else "abierto",
                         self.webhook_url)
                dropped, self.failures = False, 0
            elif "heartbeat" in err or "session closed" in err or "reconnect" in msg:
                if not dropped:
                    log.warning("Se cortó la conexión con ngrok (¿se fue internet o la PC se suspendió?). "
                                "Reconectando solo…")
                dropped = True
            elif level in ("eror", "crit") or "ERR_NGROK" in line:
                log.error("ngrok: %s", err)
                config_error = config_error or "ERR_NGROK" in line
        code = proc.wait()
        # Con Ctrl+C, ngrok recibe la señal junto con el servidor y se cierra un instante antes
        # de que lo apaguemos nosotros: eso no es un error. Se espera un poco antes de avisar.
        if self._stopped.wait(timeout=self.grace):
            return
        quick = time.monotonic() - self._started_at < 10
        self.failures = self.failures + 1 if quick else 1
        if quick and (self.failures >= 3 or config_error):
            log.error("ngrok se cerró (código %s). Revisá el authtoken (ngrok config add-authtoken …) y que "
                      "NGROK_DOMAIN sea el dominio de tu cuenta. La web y Telegram siguen funcionando.", code)
            return
        wait = min(60, 5 * self.failures)
        log.warning("ngrok se cerró (código %s): lo vuelvo a abrir en %s s.", code, wait)
        if not self._stopped.wait(timeout=wait):
            self._spawn()

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
