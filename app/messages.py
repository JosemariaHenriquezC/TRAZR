"""Mensaje de salida genérico (sirve para Telegram y, más adelante, WhatsApp)."""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field


@dataclass
class Outgoing:
    chat_id: int
    text: str                                   # HTML simple (<b>, <i>, <code>)
    buttons: list[tuple[str, str]] = field(default_factory=list)  # (texto, dato del botón)
    channel: str = "telegram"                   # por dónde se manda: telegram | whatsapp
    # Aviso de respaldo para canales que no dejan escribir libremente fuera de una ventana de
    # tiempo (WhatsApp: 24 h desde el último mensaje del cliente). Es (clave, [valores]); el
    # canal lo traduce a su plantilla aprobada. Ver PLANTILLAS en whatsapp.py.
    template: tuple[str, list[str]] | None = None


def esc(value) -> str:
    return html.escape(str(value), quote=False)


def to_plain(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", text))
