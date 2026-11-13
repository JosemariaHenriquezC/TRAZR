"""
Lógica central del bot, independiente del canal (Telegram, WhatsApp).

`BotService.handle_message()` recibe (quién escribe, qué escribió) y devuelve
una lista de mensajes a enviar (`Outgoing`) al cliente. Así la misma lógica se
usa desde Telegram, desde el simulador de consola y desde los tests.

El chat es SOLO para clientes. Lo del administrador (revisar, aprobar, rechazar,
stock, precios, avisos) está en admin.py y se usa desde la web.

Flujo de un pedido (detalle en checkout.py):
  1. El cliente escribe en lenguaje natural. Gemini entiende el mensaje en el
     contexto de la charla (si falla o no hay clave, interpreta el parser por reglas).
  2. Se arma un BORRADOR: nada se registra ni se descuenta todavía.
  3. Dirección obligatoria: si el cliente no tiene una guardada, se le pide.
  4. Se le muestra un resumen (precios, total, dirección; envío incluido sin costo)
     con botones Confirmar / Cambiar dirección / Cancelar.
  5. Al confirmar se evalúa la regla (rules.evaluate) con el stock y precio de ese momento:
       - dentro de los parámetros  → CONFIRMADO + se descuenta stock
       - fuera (precio vencido, total máximo) → EN REVISION + aviso en la web
  6. Durante un rato (MODIFICACION_MINUTOS) el cliente puede modificar el pedido.
"""
from __future__ import annotations

import logging
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .admin import AdminMixin
from .checkout import CheckoutMixin, _norm
from .config import Settings
from typing import Callable

from .messages import Outgoing, esc, to_plain
from .models import Order, ParsedItem
from .storage.base import Repository, StorageError

log = logging.getLogger(__name__)

# Cuánta conversación previa ve Gemini. Vive en memoria (RAM): NO se guarda en la base,
# y se pierde al reiniciar el bot.
HISTORY_WINDOW = timedelta(hours=12)
HISTORY_MESSAGES = 10
GREETINGS = {"hola", "holaa", "buenas", "buen dia", "buenos dias", "buenas tardes", "buenas noches",
             "que tal", "hola que tal", "hola buenas", "hola buen dia", "hey", "holis"}


@dataclass
class User:
    id: int
    name: str
    channel: str = "telegram"   # canal por el que escribe (telegram, whatsapp...)



class BotService(CheckoutMixin, AdminMixin):
    def __init__(self, repo: Repository, settings: Settings, ai_client=None):
        self.repo = repo
        self.s = settings
        self.ai_client = ai_client  # solo para tests: cliente de Gemini simulado
        self.tz = ZoneInfo(settings.timezone)
        # Un único candado para que dos pedidos simultáneos no descuenten
        # el mismo stock dos veces (el bot corre como un solo proceso).
        self._lock = threading.Lock()
        # Memoria de la charla para Gemini: (canal, id) -> últimos mensajes [(rol, texto, fecha)]
        self._chats: dict[tuple[str, str], deque] = {}
        self._chats_lock = threading.Lock()

    # ------------------------------------------------------------------ utils
    def now(self) -> datetime:
        return datetime.now(self.tz)

    def stamp(self) -> str:
        return self.now().strftime("%Y-%m-%d %H:%M:%S")

    def today(self) -> date:
        return self.now().date()

    def money(self, value: float) -> str:
        txt = f"{value:,.2f}".replace(",", "§").replace(".", ",").replace("§", ".")
        return f"{self.s.currency_symbol} {txt}"

    @staticmethod
    def qty(value: float) -> str:
        return str(int(value)) if float(value).is_integer() else f"{value:g}"

    def _new_order_id(self) -> str:
        return f"P{self.now():%y%m%d}-{secrets.token_hex(2).upper()}"

    def _storage_failure(self, user: User, exc: StorageError, action: str) -> list[Outgoing]:
        log.error("Fallo de la base de datos al %s: %s", action, exc)
        self.notify_admins("falla", f"Falla de la base de datos al {action}",
                           f"{user.name} ({user.channel} {user.id}): {exc}")
        return [Outgoing(user.id,
                         "⚠️ No pude acceder a la base de datos en este momento, así que no se registró nada. "
                         "Probá de nuevo en unos minutos.")]

    @staticmethod
    def _to_channel(user: User, out: list[Outgoing]) -> list[Outgoing]:
        """Las respuestas salen por el mismo canal por el que escribió el cliente."""
        for m in out:
            if m.chat_id == user.id:
                m.channel = user.channel
        return out

    def format_order(self, order: Order, header: str) -> str:
        lines = [header, f"<b>Pedido:</b> <code>{esc(order.order_id)}</code>",
                 f"<b>Fecha:</b> {esc(order.created_at)}", ""]
        try:
            products = {p.sku.upper(): p for p in self.repo.list_products()}
        except StorageError:
            products = {}
        for l in order.lines:
            p = products.get(l.sku.upper())
            packs = p.pack_label(l.qty) if p is not None else ""
            lines.append(f"• {l.qty} × {esc(l.name)}{f' ({esc(packs)})' if packs else ''} — "
                         f"{self.money(l.unit_price)} c/u = <b>{self.money(l.subtotal)}</b>")
        shipping = ("incluido, sin costo" if not order.shipping_cost
                    else self.money(order.shipping_cost))
        lines += ["", f"<b>Envío:</b> {shipping}"]
        if order.address:
            lines.append(f"<b>Dirección:</b> {esc(order.address)}")
        status = ("Pendiente de pago" if order.awaiting_payment else
                  "Pagado" if order.is_paid else order.status.capitalize())
        lines += [f"<b>Total: {self.money(order.total)}</b>", f"<b>Estado:</b> {esc(status)}"]
        if order.payment_method:
            paid = "pagado ✅" if order.is_paid else "pendiente"
            lines.append(f"<b>Pago:</b> {esc(order.payment_label)} · {paid}")
        if order.reason:
            lines.append(f"<b>Motivo:</b> {esc(order.reason)}")
        return "\n".join(lines)

    # ------------------------------------------------------------ entrada
    def handle_message(self, user: User, text: str,
                       on_wait: Callable[[Outgoing], None] | None = None) -> list[Outgoing]:
        """`on_wait`: el canal lo pasa para mandar en el momento un «esperame» mientras se
        reintenta con Gemini (el resto de las respuestas se devuelven al final)."""
        t0 = time.monotonic()
        out = self._handle_message(user, text, on_wait)
        self._event(user, "mensaje", int((time.monotonic() - t0) * 1000))
        return out

    def _handle_message(self, user: User, text: str, on_wait) -> list[Outgoing]:
        text = (text or "").strip()
        if not text:
            return self._to_channel(user, [Outgoing(user.id, "Mandame tu pedido como texto, por ejemplo: "
                                                             "<i>2 coca, 3 agua sin gas</i>.")])
        if text.startswith("/"):
            return self._to_channel(user, self._handle_command(user, text))
        try:
            draft = self._get_draft(user, include_save_question=True)
        except StorageError as exc:
            return self._to_channel(user, self._storage_failure(user, exc, "leer un pedido pendiente"))
        resolved = (self.resolve_pending(user, text, draft)
                    if draft is not None and draft.pending and draft.waiting in ("direccion", "confirmacion")
                    else None)
        if resolved is not None:
            out = resolved            # «24» o «sin gas» completan la duda pendiente, sin Gemini
        elif draft is not None and draft.waiting == "direccion":
            # Le pedimos la dirección: este mensaje es la dirección.
            out = self.receive_address(user, text, draft)
        elif draft is not None and draft.waiting == "pago":
            out = self.answer_payment(user, text, draft)
        elif draft is not None and draft.waiting == "guardar_direccion":
            out = self.answer_which_address(user, text, draft)
        elif self._keyword(text) is not None:
            out = self._keyword(text)(user, [])
        else:
            out = self.converse(user, text, draft, on_wait=on_wait)
        self._remember(user, text, out)
        return self._to_channel(user, out)

    def _event(self, user: User, kind: str, latency_ms: int | None = None, detail: str = "") -> None:
        """Registra un evento para medir al bot (sin el texto de los mensajes). Si la base
        falla, se sigue igual: medir nunca debe frenar un pedido."""
        try:
            self.repo.add_event(kind, user.channel, str(user.id), self.stamp(), latency_ms, detail)
        except Exception:
            log.debug("No se pudo registrar el evento %s", kind, exc_info=True)

    def _keyword(self, text: str):
        """Palabras que hacen lo mismo que un comando, para canales sin menú de comandos
        (WhatsApp): «catálogo», «mis pedidos», «ayuda», «modificar mi pedido»."""
        t = _norm(text)
        keywords = {
            "catalogo": self.cmd_catalog, "el catalogo": self.cmd_catalog, "ver catalogo": self.cmd_catalog,
            "lista": self.cmd_catalog, "lista de precios": self.cmd_catalog, "precios": self.cmd_catalog,
            "mis pedidos": self.cmd_my_orders, "mi pedido": self.cmd_my_orders, "ayuda": self.cmd_help,
            "menu": self.cmd_help, "modificar": self.cmd_modify, "modificar pedido": self.cmd_modify,
            "modificar mi pedido": self.cmd_modify, "mi direccion": self.cmd_address,
        }
        if t in keywords:
            return keywords[t]
        if not self.s.ai.usable and self.ai_client is None and t in GREETINGS:
            return self.cmd_start   # sin Gemini, un saludo recibe la bienvenida
        return None

    # ------------------------------------------------------- conversación
    def _remember(self, user: User, text: str, out: list[Outgoing]) -> None:
        """Recuerda el mensaje y la respuesta, solo en memoria (contexto para Gemini)."""
        now = self.now()
        reply = "\n\n".join(to_plain(m.text) for m in out if m.chat_id == user.id)
        with self._chats_lock:
            chat = self._chats.setdefault((user.channel, str(user.id)), deque(maxlen=HISTORY_MESSAGES))
            chat.append(("cliente", text[:2000], now))
            if reply:
                chat.append(("bot", reply[:2000], now))

    def _history(self, user: User) -> list[tuple[str, str]]:
        since = self.now() - HISTORY_WINDOW
        with self._chats_lock:
            chat = list(self._chats.get((user.channel, str(user.id)), ()))
        return [(rol, t) for rol, t, when in chat if when >= since]

    def _orders_summary(self, user: User) -> str:
        """Últimos pedidos del cliente para Gemini (sin precios ni totales)."""
        mine = [o for o in self.repo.list_orders()
                if o.customer_id == user.id and o.channel == user.channel][-3:]
        return "\n".join(f"- {o.order_id} ({o.created_at}): {o.status}; "
                         + ", ".join(f"{l.qty} x {l.name}" for l in o.lines) for o in mine)

    def handle_callback(self, user: User, data: str) -> list[Outgoing]:
        """Botones del cliente: 'cf:<token>' confirmar, 'cd:' cambiar dirección, 'cx:' cancelar,
        'aj:' ajustar a lo que hay en stock, 'dk:'/'dn:' dejar guardada la dirección de siempre / la nueva."""
        action, _, arg = data.partition(":")
        self._event(user, "boton", detail=action)
        client = {"cf": (self.confirm_draft, "Confirmar"), "cd": (self.ask_new_address, "Cambiar dirección"),
                  "cx": (self.cancel_draft, "Cancelar"),
                  "sp": (self.skip_pending, "Seguir sin eso"),
                  "aj": (self.adjust_to_stock, "Ajustar a lo que hay"),
                  "pe": (lambda u, t: self.choose_payment(u, "efectivo", t), "Efectivo al recibir"),
                  "pt": (lambda u, t: self.choose_payment(u, "transferencia", t), "Transferencia"),
                  "dk": (lambda u, t: self.choose_address(u, True, t), "La de siempre"),
                  "dn": (lambda u, t: self.choose_address(u, False, t), "La nueva")}
        if action in client:
            fn, label = client[action]
            out = fn(user, arg)
            self._remember(user, f"(tocó el botón «{label}»)", out)
            return self._to_channel(user, out)
        return self._to_channel(user, [Outgoing(user.id, "Ese botón ya no está disponible.")])

    # ------------------------------------------------------------ comandos
    def _handle_command(self, user: User, text: str) -> list[Outgoing]:
        cmd, *args = text.split()
        cmd = cmd.lower().split("@")[0]  # "/stock@MiBot" en grupos
        public = {
            "/start": self.cmd_start, "/ayuda": self.cmd_help, "/help": self.cmd_help,
            "/catalogo": self.cmd_catalog, "/mispedidos": self.cmd_my_orders,
            "/direccion": self.cmd_address, "/cancelar": self.cmd_cancel,
            "/confirmar": self.cmd_confirm, "/modificar": self.cmd_modify,
        }
        if cmd in public:
            return public[cmd](user, args)
        return [Outgoing(user.id, "No conozco ese comando. Escribí /ayuda.")]

    def cmd_start(self, user, args):
        shop = f"de {esc(self.s.business.name)}" if self.s.business.name else "de bebidas"
        msg = (f"¡Hola {esc(user.name)}! 👋\nSoy el asistente de pedidos {shop}. Escribime lo que necesitás, "
               "por ejemplo:\n<i>Quiero 2 coca, 3 agua sin gas y 1 fernet</i>\n\n"
               "🚚 El envío está siempre incluido, sin costo.\n\n"
               "Escribí <b>catálogo</b> para ver bebidas y precios, <b>mis pedidos</b> para ver los "
               "últimos, o <b>ayuda</b> para más opciones.")
        return [Outgoing(user.id, msg)]

    def cmd_help(self, user, args):
        msg = ("<b>Cómo pedir</b>\nEscribí bebidas y cantidades separadas por coma o «y»:\n"
               "<i>2 coca, 3 agua sin gas y 1 fernet</i>  ·  <i>sprite x 4</i>  ·  <i>media docena de cervezas</i>\n\n"
               "Te muestro un resumen con precios, total y dirección, y el pedido se hace "
               "recién cuando lo confirmás.\n🚚 El envío está siempre incluido, sin costo.\n\n"
               "Podés escribirme:\n"
               "• <b>catálogo</b> — bebidas y precios\n"
               "• <b>sí</b> o <b>cancelar</b> — confirmar o descartar el pedido del resumen\n"
               "• <b>modificar</b> — cambiar tu último pedido (productos o dirección)\n"
               "• <b>mi dirección</b> — ver tu dirección de envío (para cambiarla, mandame la nueva)\n"
               "• <b>mis pedidos</b> — tus últimos pedidos")
        return [Outgoing(user.id, msg)]

    def cmd_catalog(self, user, args):
        try:
            products = self._sellable()   # solo lo que hay: lo agotado no se muestra
        except StorageError as exc:
            return self._storage_failure(user, exc, "mostrar el catálogo")
        if not products:
            return [Outgoing(user.id, "Por ahora no tengo nada para ofrecerte 😕. Probá más tarde.")]
        rows = ["<b>Catálogo de bebidas</b>"]
        category = None
        for p in products:
            if p.category != category:
                category = p.category
                rows += ["", f"<b>{esc(category or 'Otras')}</b>"]
            price = self.money(p.price) if p.has_price() else "precio a confirmar"
            rows.append(f"• {esc(p.name)} — {price}")
        rows += ["", "🚚 Envío incluido, sin costo."]
        return [Outgoing(user.id, "\n".join(rows))]

    def cmd_my_orders(self, user, args):
        try:
            orders = [o for o in self.repo.list_orders()
                      if o.customer_id == user.id and o.channel == user.channel]
        except StorageError as exc:
            return self._storage_failure(user, exc, "listar pedidos")
        if not orders:
            return [Outgoing(user.id, "Todavía no tenés pedidos.")]
        rows = ["<b>Tus últimos pedidos</b>"]
        for o in orders[-5:][::-1]:
            rows.append(f"• <code>{esc(o.order_id)}</code> {esc(o.created_at)} — "
                        f"{self.money(o.total)} — {esc(o.status)}")
        return [Outgoing(user.id, "\n".join(rows))]

    # ---- dirección, confirmación y modificación (lógica en checkout.py) ----
    def cmd_address(self, user, args):
        new = " ".join(args).strip()
        try:
            draft = self._get_draft(user)
            if not new:
                customer = self.repo.get_customer(user.channel, str(user.id))
                if customer and customer.address:
                    return [Outgoing(user.id, f"📍 Tu dirección de envío es:\n<b>{esc(customer.address)}</b>\n\n"
                                              "Para cambiarla, escribime «cambiar dirección» y la nueva.")]
                return [Outgoing(user.id, "Todavía no tenés dirección guardada.\n"
                                          "Mandámela cuando hagas tu primer pedido: calle, número y localidad.")]
        except StorageError as exc:
            return self._storage_failure(user, exc, "consultar la dirección")
        if draft is not None:  # hay un pedido sin confirmar: la dirección nueva va para ese pedido
            return self.receive_address(user, new, draft)
        problem = self._address_problem(new)
        if problem:
            return [Outgoing(user.id, problem)]
        try:
            self._save_address(user, new)
        except StorageError as exc:
            return self._storage_failure(user, exc, "guardar la dirección")
        return [Outgoing(user.id, f"📍 Listo, tus próximos pedidos van a:\n<b>{esc(new)}</b>\n"
                                  "(si querés cambiar la de un pedido ya hecho, escribí «modificar»)")]

    def cmd_cancel(self, user, args):
        return self.cancel_draft(user)

    def cmd_confirm(self, user, args):
        return self.confirm_draft(user)

    def cmd_modify(self, user, args):
        return self.start_modification(user)

    # ------------------------------------------------------------ pedidos
    def _clarifications(self, items: list[ParsedItem]) -> list[str]:
        """Mensajes para los renglones que no se pudieron interpretar."""
        msgs = []
        max_q = self.s.rules.max_qty_per_item
        for it in items:
            if it.error == "ambiguo":
                opts = ", ".join(f"<b>{esc(c.name)}</b>" for c in it.candidates[:5])
                msgs.append(f"«{esc(it.raw)}»: ¿cuál de estos? {opts}")
            elif it.error == "no_existe" and self.s.rules.on_unknown_product == "ask":
                msgs.append(f"«{esc(it.unknown_name)}»: no está en nuestro catálogo (escribí «catálogo» para ver lo que hay).")
            elif it.error == "sin_producto":
                msgs.append(f"«{esc(it.raw)}»: no entendí qué producto querés.")
            elif it.error == "bulto":
                name = it.product.name if it.product else it.raw
                msgs.append(f"«{esc(it.raw)}»: ¿cuántas unidades de {esc(name)} son en total? "
                            "Todavía no tengo cargado cuántas trae cada caja o pack.")
            elif it.error == "sin_cantidad":
                name = it.product.name if it.product else it.raw
                msgs.append(f"«{esc(it.raw)}»: ¿cuántas unidades de {esc(name)}?")
            elif it.error == "cantidad_invalida":
                msgs.append(f"«{esc(it.raw)}»: la cantidad debe ser un número entero entre 1 y {max_q}.")
        return msgs

    def _confirm_and_record(self, order: Order) -> None:
        """Descuenta stock y registra el pedido. Si falla el registro, devuelve el stock."""
        deltas = {l.sku: -l.qty for l in order.lines}
        self.repo.apply_stock_deltas(deltas, order.updated_at)
        try:
            self.repo.add_order(order)
        except StorageError:
            try:
                self.repo.apply_stock_deltas({k: -v for k, v in deltas.items()}, self.stamp())
            except StorageError:
                log.critical("¡No pude revertir el stock del pedido %s! Deltas: %s", order.order_id, deltas)
            raise
