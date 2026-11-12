"""
Lógica central del bot, independiente de Telegram.

`BotService.handle_message()` recibe (quién escribe, qué escribió) y devuelve
una lista de mensajes a enviar (`Outgoing`): la respuesta al que escribió y,
si corresponde, avisos a los administradores o al cliente. Así la misma lógica
se usa desde Telegram, desde el simulador de consola y desde los tests.

Flujo de un pedido (detalle en checkout.py):
  1. El cliente escribe en lenguaje natural. Gemini entiende el mensaje en el
     contexto de la charla (si falla o no hay clave, interpreta el parser por reglas).
  2. Se arma un BORRADOR: nada se registra ni se descuenta todavía.
  3. Dirección obligatoria: si el cliente no tiene una guardada, se le pide.
  4. Se le muestra un resumen (precios, total, dirección; envío incluido sin costo)
     con botones Confirmar / Cambiar dirección / Cancelar.
  5. Al confirmar se evalúa la regla (rules.evaluate) con el stock y precio de ese momento:
       - dentro de los parámetros  → CONFIRMADO + se descuenta stock
       - fuera (faltante, precio)   → EN REVISION + aviso al admin con botones
  6. Durante un rato (MODIFICACION_MINUTOS) el cliente puede modificar el pedido.
"""
from __future__ import annotations

import logging
import secrets
import threading
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .config import Settings
from .checkout import CheckoutMixin
from .messages import Outgoing, esc, to_plain
from .models import (ESTADO_CONFIRMADO, ESTADO_RECHAZADO, ESTADO_REVISION, Order, OrderLine,
                     ParsedItem)
from .storage.base import Repository, StorageError, to_date, to_number

log = logging.getLogger(__name__)

# Cuánta conversación previa ve Gemini.
HISTORY_WINDOW = timedelta(hours=12)
HISTORY_MESSAGES = 10


@dataclass
class User:
    id: int
    name: str
    channel: str = "telegram"   # canal por el que escribe (telegram, whatsapp...)



class BotService(CheckoutMixin):
    def __init__(self, repo: Repository, settings: Settings, ai_client=None):
        self.repo = repo
        self.s = settings
        self.ai_client = ai_client  # solo para tests: cliente de Gemini simulado
        self.tz = ZoneInfo(settings.timezone)
        # Un único candado para que dos pedidos simultáneos no descuenten
        # el mismo stock dos veces (el bot corre como un solo proceso).
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ utils
    def now(self) -> datetime:
        return datetime.now(self.tz)

    def stamp(self) -> str:
        return self.now().strftime("%Y-%m-%d %H:%M:%S")

    def today(self) -> date:
        return self.now().date()

    def is_admin(self, user_id: int) -> bool:
        return user_id in self.s.admin_chat_ids

    def money(self, value: float) -> str:
        txt = f"{value:,.2f}".replace(",", "§").replace(".", ",").replace("§", ".")
        return f"{self.s.currency_symbol} {txt}"

    @staticmethod
    def qty(value: float) -> str:
        return str(int(value)) if float(value).is_integer() else f"{value:g}"

    def _new_order_id(self) -> str:
        return f"P{self.now():%y%m%d}-{secrets.token_hex(2).upper()}"

    def _admins(self, text: str, buttons=None) -> list[Outgoing]:
        return [Outgoing(a, text, buttons or []) for a in sorted(self.s.admin_chat_ids)]

    def _storage_failure(self, user: User, exc: StorageError, action: str) -> list[Outgoing]:
        log.error("Fallo de la base de datos al %s: %s", action, exc)
        out = [Outgoing(user.id,
                        "⚠️ No pude acceder a la base de datos en este momento, así que no se registró nada. "
                        "Probá de nuevo en unos minutos.")]
        if not self.is_admin(user.id):
            out += self._admins(f"🚨 Falla de acceso a la base de datos al {esc(action)} "
                                f"(usuario {esc(user.name)} / {user.id}):\n<code>{esc(exc)}</code>")
        else:
            out[0].text += f"\n<code>{esc(exc)}</code>"
        return out

    def format_order(self, order: Order, header: str) -> str:
        lines = [header, f"<b>Pedido:</b> <code>{esc(order.order_id)}</code>",
                 f"<b>Fecha:</b> {esc(order.created_at)}", ""]
        for l in order.lines:
            lines.append(f"• {l.qty} × {esc(l.name)} — {self.money(l.unit_price)} c/u = "
                         f"<b>{self.money(l.subtotal)}</b>")
        shipping = ("incluido, sin costo" if not order.shipping_cost
                    else self.money(order.shipping_cost))
        lines += ["", f"<b>Envío:</b> {shipping}"]
        if order.address:
            lines.append(f"<b>Dirección:</b> {esc(order.address)}")
        lines += [f"<b>Total: {self.money(order.total)}</b>",
                  f"<b>Estado:</b> {esc(order.status)}"]
        if order.reason:
            lines.append(f"<b>Motivo:</b> {esc(order.reason)}")
        return "\n".join(lines)

    # ------------------------------------------------------------ entrada
    def handle_message(self, user: User, text: str) -> list[Outgoing]:
        text = (text or "").strip()
        if not text:
            return [Outgoing(user.id, "Mandame tu pedido como texto, por ejemplo: <i>2 coca, 3 agua sin gas</i>.")]
        if text.startswith("/"):
            return self._handle_command(user, text)
        try:
            draft = self._get_draft(user, include_save_question=True)
        except StorageError as exc:
            return self._storage_failure(user, exc, "leer un pedido pendiente")
        if draft is not None and draft.waiting == "direccion":
            # Le pedimos la dirección: este mensaje es la dirección.
            out = self.receive_address(user, text, draft)
        elif draft is not None and draft.waiting == "guardar_direccion":
            out = self.answer_which_address(user, text, draft)
        else:
            out = self.converse(user, text, draft)
        self._remember(user, text, out)
        return out

    # ------------------------------------------------------- conversación
    def _remember(self, user: User, text: str, out: list[Outgoing]) -> None:
        """Guarda el mensaje y lo que se le respondió (memoria para Gemini)."""
        try:
            stamp = self.stamp()
            self.repo.add_message(user.channel, str(user.id), "cliente", text, stamp)
            reply = "\n\n".join(to_plain(m.text) for m in out if m.chat_id == user.id)
            if reply:
                self.repo.add_message(user.channel, str(user.id), "bot", reply, stamp)
        except StorageError as exc:
            log.warning("No pude guardar el historial del chat: %s", exc)

    def _history(self, user: User) -> list[tuple[str, str]]:
        since = (self.now() - HISTORY_WINDOW).strftime("%Y-%m-%d %H:%M:%S")
        return [(rol, t) for rol, t, fecha in
                self.repo.recent_messages(user.channel, str(user.id), HISTORY_MESSAGES) if fecha >= since]

    def _orders_summary(self, user: User) -> str:
        """Últimos pedidos del cliente para Gemini (sin precios ni totales)."""
        mine = [o for o in self.repo.list_orders()
                if o.customer_id == user.id and o.channel == user.channel][-3:]
        return "\n".join(f"- {o.order_id} ({o.created_at}): {o.status}; "
                         + ", ".join(f"{l.qty} x {l.name}" for l in o.lines) for o in mine)

    def handle_callback(self, user: User, data: str) -> list[Outgoing]:
        """Botones. Cliente: 'cf:<token>' confirmar, 'cd:' cambiar dirección, 'cx:' cancelar,
        'aj:' ajustar a lo que hay en stock, 'dk:'/'dn:' dejar guardada la dirección de siempre / la nueva.
        Admin: 'ap:<ID>' aprobar, 'rj:<ID>' rechazar."""
        action, _, arg = data.partition(":")
        client = {"cf": (self.confirm_draft, "Confirmar"), "cd": (self.ask_new_address, "Cambiar dirección"),
                  "cx": (self.cancel_draft, "Cancelar"),
                  "aj": (self.adjust_to_stock, "Ajustar a lo que hay"),
                  "dk": (lambda u, t: self.choose_address(u, True, t), "La de siempre"),
                  "dn": (lambda u, t: self.choose_address(u, False, t), "La nueva")}
        if action in client:
            fn, label = client[action]
            out = fn(user, arg)
            self._remember(user, f"(tocó el botón «{label}»)", out)
            return out
        if not self.is_admin(user.id):
            return [Outgoing(user.id, "Solo el administrador puede usar estos botones.")]
        order_id = arg
        if action == "ap":
            return self.approve(user, order_id)
        if action == "rj":
            return self.reject(user, order_id, "Rechazado por el administrador")
        return [Outgoing(user.id, "Acción desconocida.")]

    # ------------------------------------------------------------ comandos
    def _handle_command(self, user: User, text: str) -> list[Outgoing]:
        cmd, *args = text.split()
        cmd = cmd.lower().split("@")[0]  # "/stock@MiBot" en grupos
        public = {
            "/start": self.cmd_start, "/ayuda": self.cmd_help, "/help": self.cmd_help,
            "/catalogo": self.cmd_catalog, "/mispedidos": self.cmd_my_orders, "/miid": self.cmd_my_id,
            "/direccion": self.cmd_address, "/cancelar": self.cmd_cancel,
            "/confirmar": self.cmd_confirm, "/modificar": self.cmd_modify,
        }
        admin = {
            "/stock": self.cmd_stock, "/setstock": self.cmd_setstock, "/sumar": self.cmd_add_stock,
            "/precio": self.cmd_price, "/revision": self.cmd_pending, "/pedido": self.cmd_order,
            "/aprobar": self.cmd_approve, "/rechazar": self.cmd_reject,
        }
        if cmd in public:
            return public[cmd](user, args)
        if cmd in admin:
            if not self.is_admin(user.id):
                return [Outgoing(user.id, "⛔ Ese comando es solo para el administrador.")]
            return admin[cmd](user, args)
        return [Outgoing(user.id, "No conozco ese comando. Escribí /ayuda.")]

    def cmd_start(self, user, args):
        shop = f"de {esc(self.s.business.name)}" if self.s.business.name else "de bebidas"
        msg = (f"¡Hola {esc(user.name)}! 👋\nSoy el asistente de pedidos {shop}. Escribime lo que necesitás, "
               "por ejemplo:\n<i>Quiero 2 coca, 3 agua sin gas y 1 fernet</i>\n\n"
               "🚚 El envío está siempre incluido, sin costo.\n\n"
               "/catalogo — bebidas y precios\n/direccion — ver o cambiar tu dirección de envío\n"
               "/mispedidos — tus últimos pedidos\n/ayuda — más opciones")
        if self.is_admin(user.id):
            msg += "\n\n🔑 Sos administrador. Mirá /ayuda para los comandos de gestión."
        elif not self.s.admin_chat_ids:
            msg += f"\n\n(Configuración: todavía no hay administradores. Tu ID es <code>{user.id}</code>.)"
        return [Outgoing(user.id, msg)]

    def cmd_help(self, user, args):
        msg = ("<b>Cómo pedir</b>\nEscribí bebidas y cantidades separadas por coma o «y»:\n"
               "<i>2 coca, 3 agua sin gas y 1 fernet</i>  ·  <i>sprite x 4</i>  ·  <i>media docena de cervezas</i>\n\n"
               "Te muestro un resumen con precios, total y dirección, y el pedido se hace "
               "recién cuando lo confirmás.\n🚚 El envío está siempre incluido, sin costo.\n\n"
               "/catalogo — bebidas y precios\n"
               "/confirmar — confirmar el pedido del resumen\n"
               "/cancelar — descartar el pedido sin confirmar\n"
               "/modificar — cambiar tu último pedido (productos o dirección)\n"
               "/direccion [nueva dirección] — ver o cambiar tu dirección de envío\n"
               "/mispedidos — tus últimos pedidos\n/miid — tu ID")
        if self.is_admin(user.id):
            msg += ("\n\n<b>Administrador</b>\n"
                    "/stock [texto] — consultar stock (todo o filtrado)\n"
                    "/setstock SKU cantidad — fijar stock\n"
                    "/sumar SKU cantidad — sumar (o restar con negativo) stock\n"
                    "/precio SKU valor [AAAA-MM-DD] — precio y vigencia\n"
                    "/revision — pedidos en revisión\n"
                    "/pedido ID — ver un pedido\n"
                    "/aprobar ID — aprobar pedido en revisión\n"
                    "/rechazar ID [motivo] — rechazar pedido en revisión")
        return [Outgoing(user.id, msg)]

    def cmd_my_id(self, user, args):
        return [Outgoing(user.id, f"Tu ID de Telegram es <code>{user.id}</code>.")]

    def cmd_catalog(self, user, args):
        try:
            products = [p for p in self.repo.list_products() if p.active]
        except StorageError as exc:
            return self._storage_failure(user, exc, "mostrar el catálogo")
        if not products:
            return [Outgoing(user.id, "El catálogo está vacío por ahora.")]
        today = self.today()
        rows = ["<b>Catálogo de bebidas</b>"]
        category = None
        for p in products:
            if p.category != category:
                category = p.category
                rows += ["", f"<b>{esc(category or 'Otras')}</b>"]
            price = self.money(p.price) if p.price_is_valid(today) else "precio a confirmar"
            avail = "✅" if p.stock > 0 else "❌ sin stock"
            rows.append(f"• {esc(p.name)} — {price} {avail}")
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
                                              "Para cambiarla: /direccion nueva dirección")]
                return [Outgoing(user.id, "Todavía no tenés dirección guardada.\n"
                                          "Cargala así: /direccion Av. Mate de Luna 1234, San Miguel de Tucumán")]
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
                                  "(si querés cambiar la de un pedido ya hecho, usá /modificar)")]

    def cmd_cancel(self, user, args):
        return self.cancel_draft(user)

    def cmd_confirm(self, user, args):
        return self.confirm_draft(user)

    def cmd_modify(self, user, args):
        return self.start_modification(user)

    # ---- admin: stock ----
    def cmd_stock(self, user, args):
        try:
            products = self.repo.list_products()
        except StorageError as exc:
            return self._storage_failure(user, exc, "consultar stock")
        query = " ".join(args).strip()
        if query:
            from .parser import _key, match_product
            exact = [p for p in products if p.sku.upper() == query.upper()]
            if exact:
                products = exact
            else:
                found, cands = match_product(query, products, self.s.rules.match_threshold)
                products = [found] if found else (cands or
                            [p for p in products if _key(query) in _key(p.name)])
            if not products:
                return [Outgoing(user.id, f"No encontré productos que coincidan con «{esc(query)}».")]
        today = self.today()
        rows = ["<b>Stock</b>"]
        for p in products:
            vig = ("sin vencimiento" if p.valid_until is None
                   else ("vence " + p.valid_until.isoformat()) if p.valid_until != date.min else "fecha inválida")
            flag = "" if p.price_is_valid(today) else " ⚠️ precio no vigente"
            off = "" if p.active else " (inactivo)"
            rows.append(f"<code>{esc(p.sku)}</code> {esc(p.name)}{off}: <b>{self.qty(p.stock)}</b> u. · "
                        f"{self.money(p.price)} ({vig}){flag}")
        return [Outgoing(user.id, "\n".join(rows))]

    def _parse_sku_number(self, args, usage):
        if len(args) < 2:
            return None, None, usage
        number = to_number(args[1])
        if number is None:
            return None, None, f"«{esc(args[1])}» no es un número.\n{usage}"
        return args[0], number, None

    def cmd_setstock(self, user, args):
        usage = "Uso: /setstock SKU cantidad  (ej: /setstock YER1 50)"
        sku, n, err = self._parse_sku_number(args, usage)
        if err:
            return [Outgoing(user.id, err)]
        if n < 0 or not float(n).is_integer():
            return [Outgoing(user.id, "La cantidad debe ser un entero mayor o igual a 0.")]
        with self._lock:
            try:
                p = self.repo.set_stock(sku, int(n), self.stamp())
            except KeyError:
                return [Outgoing(user.id, f"No existe el SKU «{esc(sku)}». Mirá /stock.")]
            except StorageError as exc:
                return self._storage_failure(user, exc, "actualizar stock")
        return [Outgoing(user.id, f"✅ Stock de {esc(p.name)} (<code>{esc(p.sku)}</code>) actualizado a "
                                  f"<b>{self.qty(p.stock)}</b>.")]

    def cmd_add_stock(self, user, args):
        usage = "Uso: /sumar SKU cantidad  (ej: /sumar YER1 20, o /sumar YER1 -3)"
        sku, n, err = self._parse_sku_number(args, usage)
        if err:
            return [Outgoing(user.id, err)]
        if not float(n).is_integer() or n == 0:
            return [Outgoing(user.id, "La cantidad debe ser un entero distinto de 0.")]
        with self._lock:
            try:
                p = self.repo.get_product(sku)
                if p is None:
                    return [Outgoing(user.id, f"No existe el SKU «{esc(sku)}». Mirá /stock.")]
                if p.stock + n < 0:
                    return [Outgoing(user.id, f"No puedo dejar el stock negativo (hay {self.qty(p.stock)}).")]
                p = self.repo.set_stock(p.sku, p.stock + int(n), self.stamp())
            except StorageError as exc:
                return self._storage_failure(user, exc, "actualizar stock")
        return [Outgoing(user.id, f"✅ Stock de {esc(p.name)} ahora: <b>{self.qty(p.stock)}</b> "
                                  f"({'+' if n > 0 else ''}{int(n)}).")]

    def cmd_price(self, user, args):
        usage = "Uso: /precio SKU valor [AAAA-MM-DD]  (ej: /precio YER1 4500 2026-12-31)"
        sku, price, err = self._parse_sku_number(args, usage)
        if err:
            return [Outgoing(user.id, err)]
        if price <= 0:
            return [Outgoing(user.id, "El precio debe ser mayor a 0.")]
        valid_until = None
        if len(args) >= 3:
            try:
                valid_until = to_date(args[2])
            except ValueError:
                return [Outgoing(user.id, f"Fecha inválida «{esc(args[2])}». Usá AAAA-MM-DD.\n{usage}")]
            if valid_until < self.today():
                return [Outgoing(user.id, "La fecha de vigencia ya pasó.")]
        with self._lock:
            try:
                p = self.repo.set_price(sku, round(price, 2), valid_until, self.stamp())
            except KeyError:
                return [Outgoing(user.id, f"No existe el SKU «{esc(sku)}». Mirá /stock.")]
            except StorageError as exc:
                return self._storage_failure(user, exc, "actualizar precio")
        vig = f" vigente hasta {valid_until.isoformat()}" if valid_until else " (sin vencimiento)"
        return [Outgoing(user.id, f"✅ Precio de {esc(p.name)}: <b>{self.money(p.price)}</b>{vig}.")]

    # ---- admin: pedidos ----
    def cmd_pending(self, user, args):
        try:
            pending = [o for o in self.repo.list_orders() if o.status == ESTADO_REVISION]
        except StorageError as exc:
            return self._storage_failure(user, exc, "listar pedidos en revisión")
        if not pending:
            return [Outgoing(user.id, "No hay pedidos en revisión. 🎉")]
        out = [Outgoing(user.id, f"Hay <b>{len(pending)}</b> pedido(s) en revisión:")]
        for o in pending:
            out.append(Outgoing(user.id, self.format_order(o, f"🟡 <b>{esc(o.customer_name)}</b>"),
                                [("✅ Aprobar", f"ap:{o.order_id}"), ("❌ Rechazar", f"rj:{o.order_id}")]))
        return out

    def cmd_order(self, user, args):
        if not args:
            return [Outgoing(user.id, "Uso: /pedido ID")]
        try:
            o = self.repo.get_order(args[0])
        except StorageError as exc:
            return self._storage_failure(user, exc, "consultar un pedido")
        if o is None:
            return [Outgoing(user.id, f"No encontré el pedido «{esc(args[0])}».")]
        buttons = ([("✅ Aprobar", f"ap:{o.order_id}"), ("❌ Rechazar", f"rj:{o.order_id}")]
                   if o.status == ESTADO_REVISION else [])
        return [Outgoing(user.id, self.format_order(o, f"Cliente: <b>{esc(o.customer_name)}</b> "
                                                       f"(<code>{o.customer_id}</code>)"), buttons)]

    def cmd_approve(self, user, args):
        if not args:
            return [Outgoing(user.id, "Uso: /aprobar ID")]
        return self.approve(user, args[0])

    def cmd_reject(self, user, args):
        if not args:
            return [Outgoing(user.id, "Uso: /rechazar ID [motivo]")]
        reason = " ".join(args[1:]).strip() or "Rechazado por el administrador"
        return self.reject(user, args[0], reason)

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
                msgs.append(f"«{esc(it.unknown_name)}»: no está en nuestro catálogo (mirá /catalogo).")
            elif it.error == "sin_producto":
                msgs.append(f"«{esc(it.raw)}»: no entendí qué producto querés.")
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

    # ---- revisión ----
    def approve(self, admin: User, order_id: str) -> list[Outgoing]:
        with self._lock:
            try:
                order = self.repo.get_order(order_id)
                if order is None:
                    return [Outgoing(admin.id, f"No encontré el pedido «{esc(order_id)}».")]
                if order.status != ESTADO_REVISION:
                    return [Outgoing(admin.id, f"El pedido <code>{esc(order.order_id)}</code> ya está "
                                               f"<b>{esc(order.status)}</b>; no se puede aprobar.")]
                if not order.lines:
                    return [Outgoing(admin.id, "Ese pedido no tiene productos reconocidos; rechazalo "
                                               f"con /rechazar {esc(order.order_id)} motivo.")]
                products = {p.sku.upper(): p for p in self.repo.list_products()}
                today, blockers, new_lines = self.today(), [], []
                for l in order.lines:
                    p = products.get(l.sku.upper())
                    if p is None:
                        blockers.append(f"{esc(l.name)}: ya no existe en el catálogo.")
                        continue
                    if p.stock < l.qty:
                        blockers.append(f"{esc(p.name)}: hay {self.qty(p.stock)}, se piden {l.qty}. "
                                        f"Sumá stock con /sumar {esc(p.sku)} cantidad.")
                    if not p.price_is_valid(today):
                        blockers.append(f"{esc(p.name)}: precio no vigente. "
                                        f"Actualizalo con /precio {esc(p.sku)} valor.")
                    # Se toma el precio vigente actual (el admin puede haberlo actualizado).
                    new_lines.append(OrderLine(p.sku, p.name, l.qty, float(p.price)))
                if blockers:
                    return [Outgoing(admin.id, "No puedo aprobar todavía:\n• " + "\n• ".join(blockers)
                                     + f"\n\nCuando lo resuelvas: /aprobar {esc(order.order_id)}")]
                stamp = self.stamp()
                self.repo.apply_stock_deltas({l.sku: -l.qty for l in new_lines}, stamp)
                try:
                    note = f"Aprobado manualmente. Motivo original: {order.reason}" if order.reason else "Aprobado manualmente"
                    order = self.repo.update_order_status(order.order_id, ESTADO_CONFIRMADO, note,
                                                          f"{admin.name} ({admin.id})", stamp, new_lines)
                except StorageError:
                    self.repo.apply_stock_deltas({l.sku: l.qty for l in new_lines}, self.stamp())
                    raise
            except StorageError as exc:
                return self._storage_failure(admin, exc, "aprobar un pedido")

        out = [Outgoing(admin.id, f"✅ Pedido <code>{esc(order.order_id)}</code> aprobado, stock descontado "
                                  f"y cliente notificado.")]
        if order.customer_id:  # siempre se avisa al cliente (aunque sea el mismo admin probando)
            out.append(Outgoing(order.customer_id,
                                self.format_order(order, "✅ <b>¡Tu pedido fue confirmado!</b>")))
        return out

    def reject(self, admin: User, order_id: str, reason: str) -> list[Outgoing]:
        with self._lock:
            try:
                order = self.repo.get_order(order_id)
                if order is None:
                    return [Outgoing(admin.id, f"No encontré el pedido «{esc(order_id)}».")]
                if order.status != ESTADO_REVISION:
                    return [Outgoing(admin.id, f"El pedido <code>{esc(order.order_id)}</code> ya está "
                                               f"<b>{esc(order.status)}</b>; no se puede rechazar.")]
                order = self.repo.update_order_status(order.order_id, ESTADO_RECHAZADO, reason,
                                                      f"{admin.name} ({admin.id})", self.stamp())
            except StorageError as exc:
                return self._storage_failure(admin, exc, "rechazar un pedido")
        out = [Outgoing(admin.id, f"❌ Pedido <code>{esc(order.order_id)}</code> rechazado. Cliente notificado.")]
        out.append(Outgoing(order.customer_id,
                            f"❌ Tu pedido <code>{esc(order.order_id)}</code> no pudo confirmarse.\n"
                            f"<b>Motivo:</b> {esc(reason)}\n\nSi querés, mandá un pedido nuevo o consultá /catalogo."))
        return out
