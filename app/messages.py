"""Mensaje de salida genérico (sirve para Telegram y, más adelante, WhatsApp)."""
from __future__ import annotations

import html
import re
import time
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
    # Con más de 3 opciones, WhatsApp las muestra como lista detrás de un botón: su texto.
    menu_label: str = ""


def too_late(sent_at: float, minutes: int, now: float | None = None) -> bool:
    """¿El mensaje (hora de envío en segundos epoch) llegó con más de `minutes` de atraso?
    Sin hora conocida (0) o con el filtro apagado (0 minutos), nunca es tarde."""
    if not sent_at or minutes <= 0:
        return False
    return (time.time() if now is None else now) - sent_at > minutes * 60


def esc(value) -> str:
    return html.escape(str(value), quote=False)


def to_plain(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", text))


# Atención por una persona del equipo (el bot se calla con ese cliente hasta que lo devuelven).
HANDOFF_MSG = "👤 Te paso con una persona del equipo. En un rato te escriben por acá 🙌"
RELEASE_MSG = "🤖 Listo, sigo yo. Si necesitás algo más, escribime cuando quieras."
