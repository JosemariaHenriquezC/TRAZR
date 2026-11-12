"""
Canal WhatsApp (Cloud API de Meta).

Solo traduce entre WhatsApp y BotService, igual que bot.py con Telegram: la lógica de
pedidos no vive acá.

- Meta avisa cada mensaje nuevo con un POST a /whatsapp/webhook (el «webhook»).
  Se comprueba la firma (X-Hub-Signature-256, con la clave secreta de la app) para
  saber que viene de Meta, se contesta 200 enseguida y el mensaje se procesa aparte
  (si se tarda, Meta lo reenvía).
- El GET a /whatsapp/webhook es la verificación que hace Meta al configurar el webhook.
- Las respuestas salen por la Graph API (POST /<phone_number_id>/messages).
- Los botones del bot pasan a «botones de respuesta» (hasta 3, títulos de 20 caracteres).
- Meta a veces manda el mismo aviso dos veces: se procesa una sola vez (por id).

Los clientes se identifican con su número (wa_id) en el canal "whatsapp".
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import html
import json
import logging
import re
from collections import OrderedDict
from dataclasses import dataclass
from typing import Awaitable, Callable

import httpx
from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import PlainTextResponse, Response

from .config import WhatsAppConfig
from .messages import Outgoing
from .service import BotService, User

log = logging.getLogger(__name__)

CHANNEL = "whatsapp"
GRAPH_URL = "https://graph.facebook.com"
MAX_BUTTONS = 3            # botones de respuesta por mensaje
MAX_BUTTON_TITLE = 20      # caracteres por botón
MAX_LIST_ROWS = 10         # si hay más de 3 opciones, va como lista
MAX_LIST_TITLE = 24
MAX_INTERACTIVE_BODY = 1024
MAX_TEXT = 4096
# Errores de la Graph API
ERR_NOT_ALLOWED = 131030   # número de prueba: el destinatario no está en la lista de permitidos
ERR_OUT_OF_WINDOW = 131047 # pasaron más de 24 h desde el último mensaje del cliente: hace falta plantilla

OTHER_TYPES_MSG = "Por ahora solo entiendo mensajes de texto. Escribí tu pedido, por ejemplo: 2 coca, 3 agua sin gas."


# ------------------------------------------------------------------ formato
def to_whatsapp_text(text: str) -> str:
    """El bot escribe HTML simple (<b>, <i>, <code>); WhatsApp usa *negrita*, _cursiva_ y `código`."""
    def wrap(mark: str):
        def sub(m: re.Match) -> str:
            inner = m.group(1)
            core = inner.strip()
            if not core:
                return inner
            # Las marcas tienen que ir pegadas al texto: «*Total*», no «* Total *».
            lead = inner[: len(inner) - len(inner.lstrip())]
            trail = inner[len(inner.rstrip()):]
            return f"{lead}{mark}{core}{mark}{trail}"
        return sub

    out = re.sub(r"<b>(.*?)</b>", wrap("*"), text, flags=re.S)
    out = re.sub(r"<i>(.*?)</i>", wrap("_"), out, flags=re.S)
    out = re.sub(r"<code>(.*?)</code>", wrap("`"), out, flags=re.S)
    out = re.sub(r"<[^>]+>", "", out)
    return html.unescape(out)


def _fit(title: str, limit: int) -> str:
    """Achica el texto de un botón al largo que permite WhatsApp (sin el emoji si hace falta)."""
    title = title.strip()
    if len(title) <= limit:
        return title
    first, _, rest = title.partition(" ")
    if rest and not first.isalnum() and len(rest) <= limit:   # «💵 Efectivo al recibir» → «Efectivo al recibir»
        return rest
    return title[: limit - 1].rstrip() + "…"


def build_payloads(m: Outgoing) -> list[dict]:
    """Uno o más mensajes de la Graph API para un Outgoing del bot."""
    to = str(m.chat_id)
    body = to_whatsapp_text(m.text)
    base = {"messaging_product": "whatsapp", "recipient_type": "individual", "to": to}
    if not m.buttons:
        return [{**base, "type": "text", "text": {"body": chunk, "preview_url": False}}
                for chunk in _chunks(body, MAX_TEXT)]

    pre: list[dict] = []
    if len(body) > MAX_INTERACTIVE_BODY:
        # Un mensaje con botones admite poco texto: el resumen va antes, y los botones aparte.
        pre = [{**base, "type": "text", "text": {"body": chunk, "preview_url": False}}
               for chunk in _chunks(body, MAX_TEXT)]
        body = "¿Cómo seguimos?"
    if len(m.buttons) <= MAX_BUTTONS:
        action = {"buttons": [{"type": "reply", "reply": {"id": data[:256], "title": _fit(t, MAX_BUTTON_TITLE)}}
                              for t, data in m.buttons]}
        interactive = {"type": "button", "body": {"text": body}, "action": action}
    else:
        rows = [{"id": data[:200], "title": _fit(t, MAX_LIST_TITLE)} for t, data in m.buttons[:MAX_LIST_ROWS]]
        interactive = {"type": "list", "body": {"text": body},
                       "action": {"button": "Ver opciones", "sections": [{"title": "Opciones", "rows": rows}]}}
    return pre + [{**base, "type": "interactive", "interactive": interactive}]


def _chunks(text: str, size: int) -> list[str]:
    if len(text) <= size:
        return [text]
    parts, current = [], ""
    for line in text.split("\n"):
        while len(line) > size:            # una línea larguísima: se corta igual
            parts.append(line[:size])
            line = line[size:]
        if len(current) + len(line) + 1 > size:
            parts.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current:
        parts.append(current)
    return parts


# ------------------------------------------------------------------ entrada
def verify_signature(app_secret: str, body: bytes, header: str | None) -> bool:
    if not app_secret or not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256="))


@dataclass
class Incoming:
    message_id: str
    wa_id: str
    name: str
    kind: str          # "text" | "button" | "other"
    value: str = ""    # el texto, o el dato del botón


def parse_webhook(payload: dict) -> list[Incoming]:
    """Saca los mensajes de un aviso de Meta. Ignora los «estados» (enviado, leído…)."""
    found: list[Incoming] = []
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            value = change.get("value") or {}
            names = {c.get("wa_id"): (c.get("profile") or {}).get("name", "")
                     for c in value.get("contacts") or []}
            for msg in value.get("messages") or []:
                wa_id = str(msg.get("from") or "")
                if not wa_id.isdigit():
                    continue
                item = Incoming(message_id=str(msg.get("id") or ""), wa_id=wa_id,
                                name=names.get(wa_id) or wa_id, kind="other")
                kind = msg.get("type")
                if kind == "text":
                    item.kind, item.value = "text", (msg.get("text") or {}).get("body", "")
                elif kind == "interactive":
                    inter = msg.get("interactive") or {}
                    reply = inter.get("button_reply") or inter.get("list_reply") or {}
                    if reply.get("id"):
                        item.kind, item.value = "button", reply["id"]
                elif kind == "button":   # respuesta rápida de una plantilla
                    btn = msg.get("button") or {}
                    item.kind, item.value = "text", btn.get("payload") or btn.get("text", "")
                found.append(item)
    return found


class _Seen:
    """Ids de mensajes ya procesados (los últimos N), para no contestar dos veces."""

    def __init__(self, size: int = 2000) -> None:
        self._ids: OrderedDict[str, None] = OrderedDict()
        self._size = size

    def add(self, message_id: str) -> bool:
        """True si es nuevo."""
        if not message_id:
            return True
        if message_id in self._ids:
            return False
        self._ids[message_id] = None
        if len(self._ids) > self._size:
            self._ids.popitem(last=False)
        return True


# ------------------------------------------------------------------ salida
Poster = Callable[[dict], Awaitable[dict]]


class WhatsAppClient:
    """Manda mensajes por la Graph API. `post` se puede reemplazar en los tests."""

    def __init__(self, cfg: WhatsAppConfig, post: Poster | None = None) -> None:
        self.cfg = cfg
        self._post = post or self._http_post
        self._http: httpx.AsyncClient | None = None
        # Argentina: Meta avisa con 549…, pero un número cargado como 54… en la lista de
        # prueba solo recibe sin el 9. Se recuerda qué forma funcionó para cada cliente.
        self._route: dict[str, str] = {}

    async def _http_post(self, payload: dict) -> dict:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=15)
        url = f"{GRAPH_URL}/{self.cfg.api_version}/{self.cfg.phone_number_id}/messages"
        r = await self._http.post(url, json=payload, headers={"Authorization": f"Bearer {self.cfg.token}"})
        try:
            data = r.json()
        except ValueError:
            data = {"error": {"message": r.text[:200], "code": r.status_code}}
        if r.status_code >= 400 and "error" not in data:
            data["error"] = {"message": f"HTTP {r.status_code}", "code": r.status_code}
        return data

    async def close(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    async def send(self, m: Outgoing) -> None:
        """Función de envío para el Notifier (respuestas del bot y avisos de la web)."""
        wa_id = str(m.chat_id)
        for payload in build_payloads(m):
            await self._send_one(wa_id, payload)

    async def _send_one(self, wa_id: str, payload: dict) -> None:
        to = self._route.get(wa_id, wa_id)
        data = await self._post({**payload, "to": to})
        error = data.get("error")
        if error and error.get("code") == ERR_NOT_ALLOWED and to.startswith("549"):
            alt = "54" + to[3:]
            data = await self._post({**payload, "to": alt})
            error = data.get("error")
            if not error:
                self._route[wa_id] = alt
        if not error:
            return
        code = error.get("code")
        if code == ERR_OUT_OF_WINDOW:
            log.warning("No se pudo avisar a %s por WhatsApp: pasaron más de 24 h desde su último mensaje "
                        "(hace falta una plantilla aprobada por Meta).", wa_id)
        elif code == ERR_NOT_ALLOWED:
            log.warning("WhatsApp: %s no está en la lista de números permitidos del número de prueba.", wa_id)
        elif code == 190:
            log.error("WhatsApp: el token de acceso venció o es inválido. Generá uno nuevo en Meta "
                      "y actualizá WHATSAPP_TOKEN en el .env.")
        else:
            log.error("WhatsApp: no se pudo enviar a %s: %s (código %s)", wa_id, error.get("message"), code)


# ------------------------------------------------------------------ webhook
def build_router(svc: BotService, cfg: WhatsAppConfig, client: WhatsAppClient) -> APIRouter:
    router = APIRouter()
    seen = _Seen()

    @router.get("/whatsapp/webhook")
    async def verify(request: Request):
        q = request.query_params
        if q.get("hub.mode") == "subscribe" and cfg.verify_token and \
                hmac.compare_digest(q.get("hub.verify_token", "").encode(), cfg.verify_token.encode()):
            log.info("WhatsApp: webhook verificado por Meta.")
            return PlainTextResponse(q.get("hub.challenge", ""))
        return Response(status_code=403)

    @router.post("/whatsapp/webhook")
    async def receive(request: Request, background: BackgroundTasks):
        body = await request.body()
        if not verify_signature(cfg.app_secret, body, request.headers.get("x-hub-signature-256")):
            log.warning("WhatsApp: aviso con firma inválida, descartado.")
            return Response(status_code=403)
        try:
            payload = json.loads(body)
        except ValueError:
            return Response(status_code=400)
        for item in parse_webhook(payload):
            if seen.add(item.message_id):
                background.add_task(handle, item)
        return Response(status_code=200)

    async def handle(item: Incoming) -> None:
        user = User(id=int(item.wa_id), name=item.name, channel=CHANNEL)
        try:
            if item.kind == "button":
                out = await asyncio.to_thread(svc.handle_callback, user, item.value)
            elif item.kind == "text":
                loop = asyncio.get_running_loop()

                def on_wait(m: Outgoing) -> None:
                    # Se llama desde el hilo de trabajo: el envío corre en el bucle de la web.
                    m.channel = CHANNEL
                    asyncio.run_coroutine_threadsafe(client.send(m), loop).result(timeout=20)

                out = await asyncio.to_thread(svc.handle_message, user, item.value, on_wait)
            else:
                out = [Outgoing(user.id, OTHER_TYPES_MSG, channel=CHANNEL)]
        except Exception:
            log.exception("WhatsApp: error al procesar el mensaje de %s", item.wa_id)
            out = [Outgoing(user.id, "Ups, algo falló de mi lado. Probá de nuevo en un momento.", channel=CHANNEL)]
        for m in out:
            if m.chat_id == user.id:      # las respuestas van a quien escribió, por WhatsApp
                m.channel = CHANNEL
            if m.channel == CHANNEL:
                await client.send(m)

    return router
