"""
Acciones del administrador (las usa la web; el chat es solo para clientes).

Cada acción devuelve un `AdminResult`: si salió bien, un mensaje en texto plano para
mostrar en la web y, si corresponde, los avisos para el cliente (`notify`), que el
Notifier manda por el canal de ese cliente (Telegram o WhatsApp).
Las reglas son las mismas de siempre: no se duplican en las rutas de la web.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import timedelta

from .auth import MIN_PASSWORD_LEN, hash_password, verify_password
from .messages import Outgoing, esc
from datetime import datetime

from .models import (ESTADO_CANCELADO, ESTADO_CONFIRMADO, ESTADO_RECHAZADO, ESTADO_REVISION,
                     PAGO_PAGADO, Admin, Order, OrderLine, Product)
from .storage.base import StorageError

log = logging.getLogger(__name__)

LOW_STOCK = 10   # menos de esto = «poco stock» en el tablero y en productos


@dataclass
class AdminResult:
    ok: bool
    message: str                                   # texto plano para la web
    order: Order | None = None
    product: Product | None = None
    notify: list[Outgoing] = field(default_factory=list)   # mensajes para clientes


class AdminMixin:
    """Métodos de BotService para la web de administración."""

    # ------------------------------------------------------------- avisos
    def notify_admins(self, kind: str, title: str, detail: str = "", order_id: str = "") -> None:
        """Deja un aviso para la web. Nunca rompe la operación que lo genera."""
        try:
            self.repo.add_notice(kind, title, detail, order_id, self.stamp())
        except StorageError as exc:
            log.error("No pude guardar el aviso «%s»: %s", title, exc)

    # ------------------------------------------------------------- admins
    def authenticate(self, username: str, password: str) -> Admin | None:
        admin = self.repo.get_admin((username or "").strip().lower())
        if admin is None or not admin.active or not verify_password(password or "", admin.password_hash):
            return None
        return admin

    def create_admin(self, username: str, name: str, password: str) -> AdminResult:
        username = (username or "").strip().lower()
        if not username.replace(".", "").replace("_", "").replace("-", "").isalnum() or len(username) > 40:
            return AdminResult(False, "El usuario solo puede tener letras, números, punto, guion y guion bajo.")
        if not (name or "").strip():
            return AdminResult(False, "Falta el nombre.")
        if len(password or "") < MIN_PASSWORD_LEN:
            return AdminResult(False, f"La contraseña debe tener al menos {MIN_PASSWORD_LEN} caracteres.")
        self.repo.save_admin(Admin(username, name.strip(), hash_password(password)), self.stamp())
        return AdminResult(True, f"Admin «{username}» guardado.")

    # ------------------------------------------------------------- pedidos
    def approve(self, order_id: str, admin_name: str) -> AdminResult:
        """Aprueba un pedido EN REVISION: verifica stock y precio en ese momento (toma el
        precio actual del catálogo), descuenta el stock y avisa al cliente."""
        with self._lock:
            try:
                order = self.repo.get_order(order_id)
                if order is None:
                    return AdminResult(False, f"No encontré el pedido {order_id}.")
                if order.status != ESTADO_REVISION:
                    return AdminResult(False, f"El pedido {order.order_id} ya está {order.status.lower()}; "
                                              "no se puede aprobar.", order)
                if not order.lines:
                    return AdminResult(False, "Ese pedido no tiene productos reconocidos: rechazalo con un motivo.",
                                       order)
                products = {p.sku.upper(): p for p in self.repo.list_products()}
                blockers, new_lines = [], []
                for l in order.lines:
                    p = products.get(l.sku.upper())
                    if p is None:
                        blockers.append(f"{l.name}: ya no existe en el catálogo.")
                        continue
                    if p.stock < l.qty:
                        blockers.append(f"{p.name}: hay {self.qty(p.stock)}, se piden {l.qty}. Sumá stock en Productos.")
                    if not p.has_price():
                        blockers.append(f"{p.name}: no tiene precio cargado. Cargalo en Productos.")
                    new_lines.append(OrderLine(p.sku, p.name, l.qty, float(p.price)))
                if blockers:
                    return AdminResult(False, "No se puede aprobar todavía: " + " ".join(blockers), order)
                stamp = self.stamp()
                self.repo.apply_stock_deltas({l.sku: -l.qty for l in new_lines}, stamp)
                try:
                    note = (f"Aprobado manualmente. Motivo original: {order.reason}" if order.reason
                            else "Aprobado manualmente")
                    order = self.repo.update_order_status(order.order_id, ESTADO_CONFIRMADO, note,
                                                          admin_name, stamp, new_lines)
                except StorageError:
                    self.repo.apply_stock_deltas({l.sku: l.qty for l in new_lines}, self.stamp())
                    raise
            except StorageError as exc:
                log.error("Fallo al aprobar %s: %s", order_id, exc)
                return AdminResult(False, f"No pude acceder a la base de datos: {exc}")
        msg = Outgoing(order.customer_id,
                       self.format_order(order, "✅ <b>¡Tu pedido fue aprobado!</b> Queda pendiente de pago.")
                       + self._payment_text(order), channel=order.channel,
                       template=("pedido_aprobado", [order.order_id, self.money(order.total)]))
        return AdminResult(True, f"Pedido {order.order_id} aprobado: se descontó el stock y se avisó al cliente.",
                           order, notify=[msg])

    def reject(self, order_id: str, admin_name: str, reason: str) -> AdminResult:
        reason = (reason or "").strip()
        if not reason:
            return AdminResult(False, "Escribí el motivo del rechazo: se lo mandamos al cliente.")
        with self._lock:
            try:
                order = self.repo.get_order(order_id)
                if order is None:
                    return AdminResult(False, f"No encontré el pedido {order_id}.")
                if order.status != ESTADO_REVISION:
                    return AdminResult(False, f"El pedido {order.order_id} ya está {order.status.lower()}; "
                                              "no se puede rechazar.", order)
                order = self.repo.update_order_status(order.order_id, ESTADO_RECHAZADO, reason,
                                                      admin_name, self.stamp())
            except StorageError as exc:
                log.error("Fallo al rechazar %s: %s", order_id, exc)
                return AdminResult(False, f"No pude acceder a la base de datos: {exc}")
        msg = Outgoing(order.customer_id,
                       f"❌ Tu pedido <code>{esc(order.order_id)}</code> no pudo confirmarse.\n"
                       f"<b>Motivo:</b> {esc(reason)}\n\nSi querés, mandá un pedido nuevo o consultá /catalogo.",
                       channel=order.channel, template=("pedido_no_confirmado", [order.order_id, reason]))
        return AdminResult(True, f"Pedido {order.order_id} rechazado: le avisamos al cliente el motivo.",
                           order, notify=[msg])

    # ------------------------------------------------------------- pagos
    def confirm_payment(self, order_id: str, admin_name: str) -> AdminResult:
        """El admin confirma que recibió el pago (efectivo cobrado o transferencia acreditada)."""
        with self._lock:
            try:
                order = self.repo.get_order(order_id)
                if order is None:
                    return AdminResult(False, f"No encontré el pedido {order_id}.")
                if order.is_paid:
                    return AdminResult(False, f"El pedido {order.order_id} ya figura como pagado.", order)
                if order.status != ESTADO_CONFIRMADO:
                    return AdminResult(False, f"El pedido {order.order_id} está {order.status.lower()}: "
                                              "solo se registra el pago de pedidos confirmados.", order)
                order.payment_status, order.paid_at, order.paid_by = PAGO_PAGADO, self.stamp(), admin_name
                order.updated_at = order.paid_at
                order = self.repo.update_order(order)
            except StorageError as exc:
                return AdminResult(False, f"No pude acceder a la base de datos: {exc}")
        msg = Outgoing(order.customer_id,
                       f"💰 <b>¡Recibimos tu pago!</b>\nTu pedido <code>{esc(order.order_id)}</code> "
                       f"({self.money(order.total)}, {esc(order.payment_label.lower())}) quedó pagado. ¡Gracias!",
                       channel=order.channel, template=("pago_recibido", [order.order_id, self.money(order.total)]))
        return AdminResult(True, f"Pago del pedido {order.order_id} registrado: le avisamos al cliente.",
                           order, notify=[msg])

    def cancel_unpaid(self, order_id: str, admin_name: str, reason: str = "") -> AdminResult:
        """Cancela un pedido confirmado que no se pagó: devuelve el stock y avisa al cliente."""
        reason = (reason or "").strip() or "No recibimos el pago"
        with self._lock:
            try:
                order = self.repo.get_order(order_id)
                if order is None:
                    return AdminResult(False, f"No encontré el pedido {order_id}.")
                if not order.awaiting_payment:
                    return AdminResult(False, f"El pedido {order.order_id} no está pendiente de pago.", order)
                stamp = self.stamp()
                self.repo.apply_stock_deltas({l.sku: l.qty for l in order.lines}, stamp)
                order.status, order.reason, order.updated_at, order.reviewed_by = (
                    ESTADO_CANCELADO, reason, stamp, admin_name)
                try:
                    order = self.repo.update_order(order)
                except StorageError:
                    self.repo.apply_stock_deltas({l.sku: -l.qty for l in order.lines}, self.stamp())
                    raise
            except StorageError as exc:
                return AdminResult(False, f"No pude acceder a la base de datos: {exc}")
        msg = Outgoing(order.customer_id,
                       f"❌ Cancelamos tu pedido <code>{esc(order.order_id)}</code>.\n<b>Motivo:</b> {esc(reason)}"
                       "\n\nSi todavía lo querés, mandame el pedido de nuevo 🙏", channel=order.channel,
                       template=("pedido_cancelado", [order.order_id, reason]))
        return AdminResult(True, f"Pedido {order.order_id} cancelado: volvió el stock y le avisamos al cliente.",
                           order, notify=[msg])

    def expire_unpaid(self) -> list[Outgoing]:
        """Cancela las transferencias sin pagar pasadas PAGO_HORAS (el efectivo se cobra al
        entregar: no vence). Devuelve los avisos para los clientes. La llama el servidor
        cada unos minutos."""
        limit = timedelta(hours=self.s.rules.payment_hours)
        out: list[Outgoing] = []
        for o in self.repo.list_orders():
            if not o.awaiting_payment or o.payment_method != "transferencia":
                continue
            try:
                since = datetime.strptime(o.updated_at or o.created_at, "%Y-%m-%d %H:%M:%S").replace(tzinfo=self.tz)
            except ValueError:
                continue
            if self.now() - since <= limit:
                continue
            r = self.cancel_unpaid(o.order_id, "Automático",
                                   f"No recibimos la transferencia en {self.s.rules.payment_hours} h")
            if r.ok:
                out += r.notify
                self.notify_admins("pago_vencido", f"Pedido {o.order_id} cancelado por falta de pago",
                                   f"{o.customer_name}: transferencia de {self.money(o.total)} sin acreditar.",
                                   o.order_id)
        return out

    # ------------------------------------------------------- stock y precios
    def set_stock(self, sku: str, quantity: int) -> AdminResult:
        if quantity is None or quantity < 0 or int(quantity) != quantity:
            return AdminResult(False, "El stock debe ser un número entero mayor o igual a 0.")
        with self._lock:
            try:
                p = self.repo.set_stock(sku, int(quantity), self.stamp())
            except KeyError:
                return AdminResult(False, f"No existe el producto {sku}.")
            except StorageError as exc:
                return AdminResult(False, f"No pude acceder a la base de datos: {exc}")
        return AdminResult(True, f"Stock de {p.name}: {self.qty(p.stock)} unidades.", product=p)

    def add_stock(self, sku: str, delta: int) -> AdminResult:
        if delta is None or int(delta) != delta or delta == 0:
            return AdminResult(False, "La cantidad debe ser un número entero distinto de 0.")
        with self._lock:
            try:
                p = self.repo.get_product(sku)
                if p is None:
                    return AdminResult(False, f"No existe el producto {sku}.")
                if p.stock + delta < 0:
                    return AdminResult(False, f"No puedo dejar el stock de {p.name} negativo (hay {self.qty(p.stock)}).")
                p = self.repo.set_stock(p.sku, int(p.stock + delta), self.stamp())
            except StorageError as exc:
                return AdminResult(False, f"No pude acceder a la base de datos: {exc}")
        sign = "+" if delta > 0 else ""
        return AdminResult(True, f"Stock de {p.name}: {self.qty(p.stock)} unidades ({sign}{int(delta)}).", product=p)

    def set_price(self, sku: str, price: float) -> AdminResult:
        if price is None or price <= 0:
            return AdminResult(False, "El precio debe ser mayor a 0.")
        with self._lock:
            try:
                p = self.repo.set_price(sku, round(price, 2), self.stamp())
            except KeyError:
                return AdminResult(False, f"No existe el producto {sku}.")
            except StorageError as exc:
                return AdminResult(False, f"No pude acceder a la base de datos: {exc}")
        return AdminResult(True, f"Precio de {p.name}: {self.money(p.price)}.", product=p)

    def save_product(self, product: Product, is_new: bool) -> AdminResult:
        """Alta o edición de un producto, con las mismas validaciones que la carga por CSV."""
        sku = (product.sku or "").strip().upper()
        if not sku or len(sku) > 20 or not sku.replace("-", "").replace("_", "").isalnum():
            return AdminResult(False, "El SKU es obligatorio: letras y números, sin espacios (máx. 20).")
        if not (product.name or "").strip():
            return AdminResult(False, "El nombre es obligatorio.")
        if product.stock is None or product.stock < 0 or int(product.stock) != product.stock:
            return AdminResult(False, "El stock debe ser un número entero mayor o igual a 0.")
        if product.price is None or product.price < 0:
            return AdminResult(False, "El precio no puede ser negativo.")
        with self._lock:
            try:
                exists = self.repo.get_product(sku) is not None
                if is_new and exists:
                    return AdminResult(False, f"Ya existe un producto con el SKU {sku}.")
                if not is_new and not exists:
                    return AdminResult(False, f"No existe el producto {sku}.")
                product.sku, product.name = sku, product.name.strip()
                product.aliases = [a.strip() for a in product.aliases if a.strip()]
                self.repo.upsert_product(product, self.stamp())
                saved = self.repo.get_product(sku)
            except StorageError as exc:
                return AdminResult(False, f"No pude acceder a la base de datos: {exc}")
        return AdminResult(True, f"{'Producto creado' if is_new else 'Cambios guardados'}: {saved.name}.",
                           product=saved)

    # ------------------------------------------------------------- tablero
    def dashboard(self) -> dict:
        """Números del tablero, calculados de la base (para la web)."""
        orders = self.repo.list_orders()
        products = [p for p in self.repo.list_products() if p.active]
        b = self.s.business
        today = self.today().isoformat()
        yesterday = (self.today() - timedelta(days=1)).isoformat()

        def sales(day: str) -> float:
            return round(sum(o.total for o in orders
                             if o.created_at.startswith(day) and o.status == ESTADO_CONFIRMADO), 2)

        todays = [o for o in orders if o.created_at.startswith(today)]
        unpaid = [o for o in orders if o.awaiting_payment]
        confirmed = [o for o in todays if o.status == ESTADO_CONFIRMADO]
        pending = [o for o in orders if o.status == ESTADO_REVISION]

        def hour(o: Order) -> int:
            try:
                return int(o.created_at[11:13])
            except ValueError:
                return 0

        # El día en reparto: pedidos por hora (sin los rechazados) y el corte de entrega.
        first, last = min(b.open_hour, b.close_hour - 1), max(b.open_hour, b.close_hour - 1)
        live = [o for o in todays if o.status != ESTADO_RECHAZADO]
        hours = [{"hour": h, "count": sum(1 for o in live if hour(o) == h),
                  "is_now": h == self.now().hour} for h in range(first, last + 1)]
        top = max((x["count"] for x in hours), default=0) or 1
        for x in hours:
            x["pct"] = round(100 * x["count"] / top)
        ship_today = [o for o in confirmed if hour(o) < b.cutoff_hour]
        ship_next = [o for o in confirmed if hour(o) >= b.cutoff_hour]
        return {
            "sales_today": sales(today),
            "sales_yesterday": sales(yesterday),
            "orders_today": len(todays),
            "auto_confirmed_today": sum(1 for o in confirmed if not o.reviewed_by),
            "avg_ticket": round(sales(today) / len(confirmed), 2) if confirmed else 0.0,
            "pending": pending,
            "unpaid": unpaid,
            "unpaid_total": round(sum(o.total for o in unpaid), 2),
            "low_stock": sorted((p for p in products if p.stock < LOW_STOCK), key=lambda p: p.stock),
            "unread_notices": len(self.repo.list_notices(limit=500, unread_only=True)),
            "hours": hours,
            "cutoff_hour": b.cutoff_hour,
            "ship_today": {"count": len(ship_today), "total": round(sum(o.total for o in ship_today), 2)},
            "ship_next": {"count": len(ship_next), "total": round(sum(o.total for o in ship_next), 2)},
            "past_cutoff": self.now().hour >= b.cutoff_hour,
        }

    def version(self) -> str:
        """Firma corta del estado (pedidos, stock, avisos): la web la consulta para refrescarse sola."""
        parts = [f"{o.order_id}:{o.status}:{o.updated_at}" for o in self.repo.list_orders()]
        parts += [f"{p.sku}:{p.stock}:{p.price}:{p.active}" for p in self.repo.list_products()]
        parts.append(str(len(self.repo.list_notices(limit=500, unread_only=True))))
        return hashlib.sha1("|".join(parts).encode()).hexdigest()[:16]
