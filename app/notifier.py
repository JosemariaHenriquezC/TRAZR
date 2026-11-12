"""
Envía mensajes a los clientes por su canal (Telegram ahora, WhatsApp en la fase 5).

La lógica (BotService) solo arma `Outgoing` con su `channel`; quién los manda es
el Notifier, con una función de envío registrada por canal. Así la web puede
avisarle al cliente que su pedido fue aprobado sin saber nada de Telegram.
"""
from __future__ import annotations

import logging
from typing import Awaitable, Callable

from .messages import Outgoing

log = logging.getLogger(__name__)

Sender = Callable[[Outgoing], Awaitable[None]]


class Notifier:
    def __init__(self) -> None:
        self._senders: dict[str, Sender] = {}

    def register(self, channel: str, sender: Sender) -> None:
        self._senders[channel] = sender

    async def send(self, messages: list[Outgoing]) -> int:
        """Manda cada mensaje por su canal. Devuelve cuántos se pudieron mandar."""
        sent = 0
        for m in messages:
            sender = self._senders.get(m.channel)
            if sender is None:
                log.warning("No hay cómo mandar mensajes por «%s»; no se avisó a %s.", m.channel, m.chat_id)
                continue
            try:
                await sender(m)
                sent += 1
            except Exception:  # un cliente que bloqueó al bot no frena al resto
                log.exception("No se pudo mandar el mensaje a %s por %s", m.chat_id, m.channel)
        return sent
