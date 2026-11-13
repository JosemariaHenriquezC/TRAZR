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
from .categorias import GRUPOS, clave, grupo_de, orden_de
from . import parser as rules_parser
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
WEEKDAYS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
CATALOG_WORDS = {"catalogo", "catalogos", "lista", "listado", "menu", "precios", "carta"}
CATALOG_PHRASES = ("que tienen", "que tenes", "que hay", "que venden", "que vendes", "que ofrecen",
                   "que bebidas", "que productos", "que tienen para", "que me ofreces", "que manejan")
ORDER_WORDS = {"quiero", "mandame", "necesito", "traeme", "anotame", "pasame", "dame"}


def _wants_catalog(t: str, names: set[str]) -> str | None:
    """¿Pide ver el catálogo? «pasame el catálogo», «¿qué tienen?», «la lista de precios» →
    "todo"; «¿qué vinos tienen?», «mostrame los gin» → la categoría o grupo nombrado. Un pedido
    con cantidades («2 vinos») no: eso es un pedido."""
    words = t.split()
    if not words or len(words) > 10 or any(ch.isdigit() for ch in t):
        return None
    asks = bool(CATALOG_WORDS & set(words)) or any(p in t for p in CATALOG_PHRASES) or \
        bool({"mostrame", "muestrame", "mostrar", "ver"} & set(words)) or \
        (words[0] in ("que", "cuales") and bool({"tienen", "tenes", "hay", "venden", "manejan"} & set(words)))
    if not asks:
        return None
    for name in sorted(names, key=len, reverse=True):   # «qué vinos tienen» → Vinos
        if name and (f" {name} " in f" {t} " or f" {name}s " in f" {t} "):
            return name
    if CATALOG_WORDS & set(words) or any(p in t for p in CATALOG_PHRASES):
        return "todo"
    return None


REPEAT_PHRASES = ("lo de siempre", "lo mismo que", "lo mismo de", "mismo pedido", "repeti", "repetir",
                  "repetime", "repetilo", "lo de la otra vez", "lo de la vez pasada", "lo mismo")
REPEAT_STOP = {"pero", "sin", "menos", "mas", "agrega", "agregale", "saca", "sacale", "cambia", "cambiale",
               "sumale", "quita", "quitale"}


def _wants_repeat(t: str) -> bool:
    """«lo de siempre», «lo mismo que la semana pasada», «repetí mi último pedido». Si pide
    cambios («…pero sin fanta», «…y 2 cocas más») no: eso lo arma Gemini con la lista completa."""
    words = t.split()
    if not words or len(words) > 10 or any(ch.isdigit() for ch in t) or REPEAT_STOP & set(words):
        return False
    return any(t.startswith(p) or f" {p}" in f" {t}" for p in REPEAT_PHRASES)


CATALOG_INLINE = 15    # hasta esta cantidad de productos, el catálogo va entero en un mensaje
MAX_CATEGORIES = 10    # una lista de WhatsApp tiene hasta 10 opciones
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
        """$ 3.200 (sin «,00» si es redondo) · $ 3.200,50."""
        decimals = 0 if round(value, 2) == round(value) else 2
        txt = f"{value:,.{decimals}f}".replace(",", "§").replace(".", ",").replace("§", ".")
        return f"{self.s.currency_symbol} {txt}"

    def ship_day(self, when: datetime | None = None) -> str:
        """En qué turno sale un pedido confirmado en `when` (ahora si no se dice). Se reparte en
        dos turnos (a la siesta no): lo confirmado antes del corte de la mañana (11) sale a la
        mañana; antes del corte de la tarde (19), a la tarde; si no, el próximo día de reparto a
        la mañana. «hoy a la tarde (17 a 21)», «mañana a la mañana (9 a 13)», «el lunes 16/11 …»."""
        b = self.s.business
        now = when or self.now()
        days = set(b.delivery_days) or {0, 1, 2, 3, 4, 5}
        morning = f"a la mañana ({b.morning_shift})" if b.morning_cutoff else f"a la tarde ({b.evening_shift})"
        if now.weekday() in days:
            if b.morning_cutoff and now.hour < b.morning_cutoff:
                return f"hoy a la mañana ({b.morning_shift})"
            if now.hour < b.cutoff_hour:
                return f"hoy a la tarde ({b.evening_shift})"
        day = now.date() + timedelta(days=1)
        while day.weekday() not in days:
            day += timedelta(days=1)
        if day == now.date() + timedelta(days=1):
            return f"mañana {morning}"
        return f"el {WEEKDAYS[day.weekday()]} {day:%d/%m} {morning}"

    @staticmethod
    def when(stamp: str) -> str:
        """«2026-11-13 10:22:01» → «13/11 10:22» (como lo lee una persona)."""
        try:
            return datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S").strftime("%d/%m %H:%M")
        except (ValueError, TypeError):
            return stamp or ""

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
        lines = [header, f"<b>Pedido:</b> <code>{esc(order.order_id)}</code> · {esc(self.when(order.created_at))}", ""]
        try:
            products = {p.sku.upper(): p for p in self.repo.list_products()}
        except StorageError:
            products = {}
        for l in order.lines:
            p = products.get(l.sku.upper())
            packs = p.pack_label(l.qty) if p is not None else ""
            lines.append(f"• {l.qty} × {esc(l.name)}{f' ({esc(packs)})' if packs else ''} — "
                         f"{self.money(l.unit_price)} c/u = <b>{self.money(l.subtotal)}</b>")
        shipping = ("envío incluido, sin costo" if not order.shipping_cost
                    else f"envío {self.money(order.shipping_cost)}")
        lines.append("")
        if order.address:
            lines.append(f"<b>Dirección:</b> {esc(order.address)}")
        if order.payment_method:   # el estado del pago ya dice todo: pendiente o pagado
            paid = "pagado ✅" if order.is_paid else "pendiente"
            lines.append(f"<b>Pago:</b> {esc(order.payment_label)} · {paid}")
        else:
            lines.append(f"<b>Estado:</b> {esc(order.status.capitalize())}")
        lines.append(f"<b>Total: {self.money(order.total)}</b> · {shipping}")
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
        elif (keyword := self._keyword(text)) is not None and not (draft is not None and keyword == self.cmd_start):
            out = keyword(user, [])      # (un «hola» con un pedido a medio armar lo atiende la charla)
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

    def _change_address_word(self, user, args):
        return self.ask_new_address(user) if self._get_draft(user) else self.cmd_address(user, args)

    def _change_payment_word(self, user, args):
        if self._get_draft(user) is None:
            return [Outgoing(user.id, "No tenés ningún pedido sin confirmar. El medio de pago se elige al hacer el pedido.")]
        return self.change_payment(user)

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
            "cambiar direccion": self._change_address_word, "otra direccion": self._change_address_word,
            "cambiar la direccion": self._change_address_word,
            "cambiar pago": self._change_payment_word, "cambiar el pago": self._change_payment_word,
        }
        if t in keywords:
            return keywords[t]
        try:
            sellable = self._sellable()
            names = {clave(grupo_de(p.category)) for p in sellable} | {clave(p.category) for p in sellable if p.category}
            if t and clave(t) in names:
                return lambda user, args: self.catalog_category(user, clave(t)[:40])
            wants = _wants_catalog(t, names)
            if wants == "todo":
                return self.cmd_catalog
            if wants:
                return lambda user, args: self.catalog_category(user, wants[:40])
        except StorageError:
            pass
        if t in GREETINGS:
            return self.cmd_start   # un saludo: bienvenida con el catálogo a mano
        if _wants_repeat(t):
            return lambda user, args: self.repeat_last_order(user)
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
                  "pk": (self.pick_pending, "Elegir opción"),
                  "cp": (self.change_payment, "Cambiar pago"),
                  "ct": (lambda u, t: self.cmd_catalog(u, []), "Ver catálogo"),
                  "cc": (self.catalog_category, "Categoría"),
                  "rp": (lambda u, t: self.repeat_last_order(u), "Lo de siempre"),
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
        """Saludo: al que ya compró, por su nombre y con «lo de siempre» a mano; al nuevo, una
        bienvenida corta. A los dos se les ofrece ver el catálogo."""
        name = esc((user.name or "").strip()[:40])
        try:
            returning = any(o.customer_id == user.id and o.channel == user.channel
                            for o in self.repo.list_orders())
        except StorageError:
            returning = False
        if returning:
            msg = (f"¡Hola, {name}! 👋 ¿Qué te mando hoy?\n"
                   "Escribime el pedido, o tocá un botón para ver el catálogo o repetir el último.")
            return [Outgoing(user.id, msg, [("📋 Ver catálogo", "ct:"), ("🔁 Lo de siempre", "rp:")])]
        shop = f"de {esc(self.s.business.name)}" if self.s.business.name else "de bebidas"
        msg = (f"¡Hola, {name}! 👋 Soy el asistente de pedidos {shop}.\n"
               "Escribime lo que necesitás, por ejemplo: <i>2 packs de coca y 6 aguas sin gas</i>.\n"
               "🚚 El envío está siempre incluido, sin costo.")
        return [Outgoing(user.id, msg, [("📋 Ver catálogo", "ct:")])]

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
        groups = self._groups(products)
        if len(products) <= CATALOG_INLINE or len(groups) <= 1:
            return [Outgoing(user.id, self._catalog_text(products, "Catálogo de bebidas"))]
        # Catálogo grande: primero el grupo (en WhatsApp, una lista que se abre con un botón).
        rows = ["📋 <b>Catálogo de bebidas</b>", "¿Qué querés ver?", ""]
        rows += [f"• {esc(name)} ({n})" for name, n in groups[:MAX_CATEGORIES]]
        rows += ["", "Tocá una opción o escribime qué buscás (ej. «vinos», «fernet»). 🚚 Envío incluido, sin costo."]
        buttons = [(name, f"cc:{clave(name)[:40]}") for name, _ in groups[:MAX_CATEGORIES]]
        return [Outgoing(user.id, "\n".join(rows), buttons, menu_label="Ver categorías")]

    @staticmethod
    def _groups(products) -> list[tuple[str, int]]:
        """Grupos del chat con productos, en el orden de categorias.GRUPOS (y después los demás)."""
        counts: dict[str, int] = {}
        for p in products:
            g = grupo_de(p.category)
            counts[g] = counts.get(g, 0) + 1
        order = [name for name, _ in GRUPOS]
        return sorted(counts.items(), key=lambda kv: (order.index(kv[0]) if kv[0] in order else len(order), kv[0]))

    def _catalog_text(self, products, title: str) -> str:
        rows = [f"<b>{esc(title)}</b>"]
        category = None
        for p in sorted(products, key=lambda p: (orden_de(p.category), p.name)):
            if (p.category or "Otras") != category:
                category = p.category or "Otras"
                if clave(category) != clave(title):
                    rows += ["", f"<b>{esc(category)}</b>"]
            price = self.money(p.price) if p.has_price() else "precio a confirmar"
            pack = f" · {esc(p.pack_name)} × {p.pack_size}" if p.pack_size else ""
            rows.append(f"• {esc(p.name)} — {price}{pack}")
        rows += ["", "🚚 Envío incluido, sin costo."]
        return "\n".join(rows)

    def catalog_category(self, user, key: str) -> list:
        """Productos (con stock) de un grupo del chat o de una categoría: botón de la lista o
        su nombre escrito («vinos», «fernet»)."""
        try:
            sellable = self._sellable()
        except StorageError as exc:
            return self._storage_failure(user, exc, "mostrar el catálogo")
        products = [p for p in sellable if clave(grupo_de(p.category))[:40] == key]
        title = grupo_de(products[0].category) if products else ""
        if not products:   # una categoría suelta («fernet», «gin»)
            products = [p for p in sellable if clave(p.category or "Otras")[:40] == key]
            title = products[0].category if products else ""
        if not products:
            return [Outgoing(user.id, "En esa categoría no tengo nada ahora 😕.", [("📋 Ver catálogo", "ct:")])]
        return [Outgoing(user.id, self._catalog_text(products, title) + "\nPedime lo que quieras de acá.",
                         [("📋 Otra categoría", "ct:")])]

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
            if it.error == "ambiguo" and len({rules_parser.family(c) for c in it.candidates}) == 1:
                # Mismo producto en varios tamaños o envases: se pregunta cuál.
                labels = [f"<b>{esc(rules_parser.size_label(c, it.candidates))}</b>" for c in it.candidates[:5]]
                if len(labels) == 1:
                    msgs.append(f"«{esc(it.raw)}»: de ese tamaño no tengo; hay {labels[0]}. ¿Te sirve?")
                else:
                    opts = ", ".join(labels[:-1]) + f" o {labels[-1]}"
                    msgs.append(f"«{esc(it.raw)}»: ¿de qué tamaño? Tengo {opts}.")
            elif it.error == "ambiguo":
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
