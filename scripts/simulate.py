"""
Simulador de consola: probá el bot SIN Telegram (misma lógica, misma base de datos).

    python -m scripts.simulate

Escribí mensajes como si fueras un cliente. Comandos del simulador:
    :admin    → pasás a escribir como administrador
    :cliente  → volvés a escribir como cliente
    :ap ID / :rj ID → simula tocar los botones Aprobar / Rechazar
    :salir
"""
from __future__ import annotations

import re
import sys

from app.config import ConfigError, load_settings
from app.service import BotService, User
from app.storage import build_repository

ADMIN = User(id=1, name="Admin")
CLIENT = User(id=2, name="Cliente Demo")


def strip_html(text: str) -> str:
    return re.sub(r"</?(b|i|code)>", "", text).replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")


def show(out):
    for m in out:
        who = "admin" if m.chat_id == ADMIN.id else "cliente" if m.chat_id == CLIENT.id else m.chat_id
        print(f"\n  ── bot → {who} ──")
        print("  " + strip_html(m.text).replace("\n", "\n  "))
        if m.buttons:
            print("  [" + "] [".join(f"{t} = :{d.split(':')[0]} {d.split(':')[1]}" for t, d in m.buttons) + "]")


def main() -> None:
    try:
        s = load_settings(require_telegram=False)
    except ConfigError as exc:
        sys.exit(f"Error de configuración: {exc}")
    s.admin_chat_ids = {ADMIN.id}
    svc = BotService(build_repository(s), s)
    me = CLIENT
    print(__doc__)
    while True:
        try:
            text = input(f"\n{'ADMIN' if me is ADMIN else 'CLIENTE'}> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if text == ":salir":
            break
        if text == ":admin":
            me = ADMIN; continue
        if text == ":cliente":
            me = CLIENT; continue
        if text.startswith((":ap ", ":rj ")):
            show(svc.handle_callback(ADMIN, f"{text[1:3]}:{text[4:].strip()}")); continue
        show(svc.handle_message(me, text))


if __name__ == "__main__":
    main()
