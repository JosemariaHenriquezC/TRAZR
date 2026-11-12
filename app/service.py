"""
Lógica central del bot, independiente de Telegram.

`BotService.handle_message()` recibe (quién escribe, qué escribió) y devuelve
una lista de mensajes a enviar (`Outgoing`): la respuesta al que escribió y,
si corresponde, avisos a los administradores o al cliente. Así la misma lógica
se usa desde Telegram, desde el simulador de consola y desde los tests.

Flujo de un pedido:
  1. El cliente escribe su pedido en lenguaje natural → parser.parse_order()
  2. Se lee el stock y precio vigentes en ese momento de la base de datos.
  3. Dirección obligatoria: si el cliente todavía no tiene una guardada, el
     pedido queda retenido (pedidos_pendientes) y se le pide la dirección.
     Su respuesta se guarda y el pedido retenido se procesa en ese momento.
     El envío está siempre incluido, sin costo.
  4. Se evalúa la regla (rules.evaluate):
       - dentro de los parámetros  → CONFIRMADO + se descuenta stock
       - fuera (faltante, precio)   → EN REVISION + aviso al admin con botones
  5. El cliente recibe la confirmación con el detalle completo
     (o el aviso de que quedó en revisión; y luego el resultado de la revisión).
"""
from __future__ import annotations

import html
import logging
import secrets
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .config import Settings
from .models import (COSTO_ENVIO, ESTADO_CONFIRMADO, ESTADO_RECHAZADO, ESTADO_REVISION,
                     Customer, Order, OrderLine, ParsedItem, Product)
from .parser import parse_order
from .rules import evaluate
from .storage.base import Repository, StorageError, to_date, to_number

log = logging.getLogger(__name__)

# Un pedido retenido esperando la dirección se descarta pasado este tiempo
# (si el cliente vuelve días después, su mensaje es un pedido nuevo, no una dirección).
PENDING_TTL = timedelta(hours=24)
MIN_ADDRESS_LEN = 8
MAX_ADDRESS_LEN = 200


@dataclass
class Outgoing:
    chat_id: int
    text: str                                   # HTML de Telegram (<b>, <i>, <code>)
    buttons: list[tuple[str, str]] = field(default_factory=list)  # (texto, callback_data)


@dataclass
class User:
    id: int
    name: str
    channel: str = "telegram"   # canal por el que escribe (telegram, whatsapp...)


def esc(value) -> str:
    return html.escape(str(value), quote=False)


class BotService:
    def __init__(self, repo: Repository, settings: Settings):
        self.repo = repo
        self.s = settings
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
            pending = self._pending_order(user)
        except StorageError as exc:
            return self._storage_failure(user, exc, "leer un pedido pendiente")
        if pending is not None:
            # Hay un pedido retenido esperando la dirección: este mensaje es la dirección.
            return self.receive_address(user, text, pending)
        return self.process_order(user, text)

    def handle_callback(self, user: User, data: str) -> list[Outgoing]:
        """Botones inline: 'ap:<ID>' aprobar, 'rj:<ID>' rechazar."""
        if not self.is_admin(user.id):
            return [Outgoing(user.id, "Solo el administrador puede usar estos botones.")]
        action, _, order_id = data.partition(":")
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
        msg = (f"¡Hola {esc(user.name)}! 👋\nSoy el bot de pedidos de bebidas. Escribime lo que necesitás, "
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
               "🚚 El envío está siempre incluido, sin costo. La primera vez te pido la dirección.\n\n"
               "/catalogo — bebidas y precios\n"
               "/direccion [nueva dirección] — ver o cambiar tu dirección de envío\n"
               "/cancelar — descartar un pedido que espera tu dirección\n"
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

    # ---- dirección de envío ----
    def cmd_address(self, user, args):
        new = " ".join(args).strip()
        try:
            customer = self.repo.get_customer(user.channel, str(user.id))
            if not new:
                if customer and customer.address:
                    return [Outgoing(user.id, f"📍 Tu dirección de envío es:\n<b>{esc(customer.address)}</b>\n\n"
                                              "Para cambiarla: /direccion nueva dirección")]
                return [Outgoing(user.id, "Todavía no tenés dirección guardada.\n"
                                          "Cargala así: /direccion Av. Mate de Luna 1234, San Miguel de Tucumán")]
            pending = self._pending_order(user)
        except StorageError as exc:
            return self._storage_failure(user, exc, "consultar la dirección")
        if pending is not None:
            return self.receive_address(user, new, pending)
        problem = self._address_problem(new)
        if problem:
            return [Outgoing(user.id, problem)]
        try:
            self._save_address(user, new)
        except StorageError as exc:
            return self._storage_failure(user, exc, "guardar la dirección")
        return [Outgoing(user.id, f"📍 Listo, tus próximos pedidos van a:\n<b>{esc(new)}</b>")]

    def cmd_cancel(self, user, args):
        try:
            pending = self._pending_order(user)
            if pending is None:
                return [Outgoing(user.id, "No tenés ningún pedido esperando dirección.")]
            self.repo.clear_pending_order(user.channel, str(user.id))
        except StorageError as exc:
            return self._storage_failure(user, exc, "cancelar el pedido pendiente")
        return [Outgoing(user.id, "Listo, descarté ese pedido. Cuando quieras, mandame uno nuevo.")]

    def _pending_order(self, user: User) -> str | None:
        """Mensaje del pedido retenido esperando dirección (None si no hay o venció)."""
        found = self.repo.get_pending_order(user.channel, str(user.id))
        if found is None:
            return None
        text, created = found
        try:
            created_at = datetime.strptime(created, "%Y-%m-%d %H:%M:%S").replace(tzinfo=self.tz)
        except ValueError:
            created_at = None
        if created_at is None or self.now() - created_at > PENDING_TTL:
            self.repo.clear_pending_order(user.channel, str(user.id))
            return None
        return text

    @staticmethod
    def _address_problem(address: str) -> str | None:
        if len(address) < MIN_ADDRESS_LEN or not any(ch.isalpha() for ch in address):
            return ("Esa dirección parece incompleta 🤔. Mandame calle, número y localidad, por ejemplo:\n"
                    "<i>Av. Mate de Luna 1234, San Miguel de Tucumán</i>\n(o /cancelar para descartar el pedido)")
        if len(address) > MAX_ADDRESS_LEN:
            return f"La dirección es muy larga (máximo {MAX_ADDRESS_LEN} caracteres)."
        return None

    def _save_address(self, user: User, address: str) -> Customer:
        return self.repo.save_customer(Customer(user.channel, str(user.id), user.name, address.strip()),
                                       self.stamp())

    def receive_address(self, user: User, address: str, pending_text: str) -> list[Outgoing]:
        """El cliente respondió con su dirección: se guarda y se procesa el pedido retenido."""
        address = address.strip()
        problem = self._address_problem(address)
        if problem:
            return [Outgoing(user.id, problem)]
        try:
            self._save_address(user, address)
            self.repo.clear_pending_order(user.channel, str(user.id))
        except StorageError as exc:
            return self._storage_failure(user, exc, "guardar la dirección")
        out = [Outgoing(user.id, f"📍 Guardé tu dirección: <b>{esc(address)}</b>\n"
                                 "(podés cambiarla cuando quieras con /direccion)")]
        return out + self.process_order(user, pending_text)

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

    def process_order(self, user: User, text: str) -> list[Outgoing]:
        with self._lock:
            # Etapa 2: stock y precios en tiempo real (se leen en cada pedido).
            try:
                products = self.repo.list_products()
            except StorageError as exc:
                return self._storage_failure(user, exc, "leer el stock para un pedido")

            active = [p for p in products if p.active]
            # Etapa 1: interpretar el mensaje.
            items = parse_order(text, active, self.s.rules.match_threshold, self.s.rules.max_qty_per_item)
            if not items:
                return [Outgoing(user.id,
                                 "No encontré un pedido en tu mensaje 🤔. Escribí productos y cantidades, "
                                 "por ejemplo: <i>2 coca, 3 agua sin gas</i>. Mirá /catalogo.")]
            problems = self._clarifications(items)
            if problems:
                return [Outgoing(user.id, "No pude registrar el pedido todavía:\n• " + "\n• ".join(problems)
                                 + "\n\nMandámelo de nuevo corregido, completo. 🙏")]

            pairs = [(it.product, it.qty) for it in items if it.product and it.qty]
            unknown = [it.unknown_name for it in items if it.error == "no_existe"]  # solo modo "review"

            # Etapa 3: dirección obligatoria. Sin dirección no se registra nada:
            # el pedido queda retenido y se le pide la dirección al cliente.
            try:
                customer = self.repo.get_customer(user.channel, str(user.id))
                if customer is None or not customer.address:
                    self.repo.set_pending_order(user.channel, str(user.id), text, self.stamp())
                    return [Outgoing(user.id,
                                     "📍 ¡Casi listo! ¿A qué dirección te lo enviamos? "
                                     "<b>El envío está incluido, sin costo.</b>\n"
                                     "Respondé con calle, número y localidad, por ejemplo:\n"
                                     "<i>Av. Mate de Luna 1234, San Miguel de Tucumán</i>\n"
                                     "(o /cancelar para descartar el pedido)")]
                if customer.name != user.name:  # mantener el nombre al día
                    customer.name = user.name
                    self.repo.save_customer(customer, self.stamp())
            except StorageError as exc:
                return self._storage_failure(user, exc, "leer los datos del cliente")

            # Etapa 4: regla de parámetros.
            ev = evaluate(pairs, self.s.rules, self.today(), unknown)
            now = self.stamp()
            order = Order(order_id=self._new_order_id(), created_at=now, customer_id=user.id,
                          customer_name=user.name, original_text=text, lines=ev.lines,
                          status=ev.status, reason="; ".join(ev.reasons), updated_at=now,
                          address=customer.address, shipping_cost=COSTO_ENVIO, channel=user.channel)

            try:
                if order.status == ESTADO_CONFIRMADO:
                    self._confirm_and_record(order)
                else:
                    self.repo.add_order(order)
            except StorageError as exc:
                return self._storage_failure(user, exc, "registrar un pedido")

        # Etapa 5: avisos.
        if order.status == ESTADO_CONFIRMADO:
            return [Outgoing(user.id, self.format_order(order, "✅ <b>¡Pedido confirmado!</b>"))]
        client_msg = self.format_order(order, "🟡 <b>Recibimos tu pedido</b>") + \
            "\n\nQuedó <b>en revisión</b>; te aviso apenas el área administrativa lo resuelva."
        admin_msg = self.format_order(order, f"🔔 <b>Pedido derivado a revisión</b>\nCliente: "
                                             f"<b>{esc(user.name)}</b> (<code>{user.id}</code>)")
        out = [Outgoing(user.id, client_msg)]
        if self.s.admin_chat_ids:
            out += self._admins(admin_msg, [("✅ Aprobar", f"ap:{order.order_id}"),
                                            ("❌ Rechazar", f"rj:{order.order_id}")])
        else:
            log.warning("Pedido %s en revisión pero no hay ADMIN_CHAT_IDS configurados", order.order_id)
        return out

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
