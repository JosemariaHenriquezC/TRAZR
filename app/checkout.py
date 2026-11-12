"""
Fase 3: borrador del pedido, confirmación, dirección y modificación.

Flujo de un pedido nuevo:
  mensaje → se interpreta → BORRADOR (no se registra nada ni se toca el stock)
    ├─ sin dirección guardada → se pide la dirección (esperando "direccion")
    └─ con dirección → resumen con precios, total y dirección + botones
         [✅ Confirmar] [📍 Cambiar dirección] [❌ Cancelar]
  El cliente confirma (botón, «sí», /confirmar) → recién ahí se evalúa la regla
  y se registra CONFIRMADO o EN REVISION.

Modificar un pedido ya hecho (productos y/o dirección): mientras no esté
rechazado y dentro de Rules.modify_minutes desde que se hizo. Se arma un
borrador a partir del pedido, el cliente lo cambia y confirma; entonces se
devuelve el stock del pedido viejo, se evalúa el nuevo y se descuenta lo
que corresponde, todo en un paso.

Todo esto es independiente del canal: los botones son (texto, dato) genéricos
y siempre hay una alternativa escrita («sí», /confirmar, /cancelar).
"""
from __future__ import annotations

import json
import logging
import secrets
import unicodedata
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timedelta

from . import parser as rules_parser
from .ai_parser import conversar, items_a_parsed, parse_order
from .models import (COSTO_ENVIO, ESTADO_CONFIRMADO, ESTADO_REVISION, Customer, Order,
                     ParsedItem, Product)
from .rules import evaluate
from .messages import Outgoing, esc
from .storage.base import StorageError

log = logging.getLogger(__name__)

WAIT_CONFIRM = "confirmacion"
WAIT_ADDRESS = "direccion"
PENDING_TTL = timedelta(hours=24)
MIN_ADDRESS_LEN = 8
MAX_ADDRESS_LEN = 200

YES = {"si", "sii", "sip", "dale", "ok", "okey", "oka", "confirmo", "confirmar", "confirma",
       "listo", "de una", "perfecto", "va", "bueno", "si dale", "dale si", "si confirmo",
       "si por favor", "si porfa", "correcto", "esta bien", "todo bien"}
NO = {"no", "nop", "cancelar", "cancela", "cancelo", "no gracias", "deja", "dejalo", "nada"}

NO_ORDER_MSG = ("No encontré un pedido en tu mensaje 🤔. Escribí productos y cantidades, "
                "por ejemplo: <i>2 coca, 3 agua sin gas</i>. Mirá /catalogo.")
ASK_ADDRESS = ("📍 ¿A qué dirección te lo enviamos? <b>El envío está incluido, sin costo.</b>\n"
               "Respondé con calle, número y localidad, por ejemplo:\n"
               "<i>Av. Mate de Luna 1234, San Miguel de Tucumán</i>\n"
               "(o /cancelar para descartar el pedido)")


def _norm(text: str) -> str:
    t = unicodedata.normalize("NFKD", text.lower())
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    t = "".join(ch if ch.isalnum() or ch == " " else " " for ch in t)
    return " ".join(t.split())


@dataclass
class Draft:
    """Pedido armado que todavía no se registró."""
    text: str                                       # mensaje original del cliente
    items: list[list] | None = field(default_factory=list)   # [[sku, cantidad]]; None = reinterpretar text
    unknown: list[str] = field(default_factory=list)          # productos no reconocidos (modo "review")
    address: str = ""
    modifies: str = ""                              # ID del pedido que se modifica ("" = pedido nuevo)
    waiting: str = WAIT_CONFIRM                     # qué se espera del cliente
    token: str = field(default_factory=lambda: secrets.token_hex(3))  # invalida botones viejos

    def encode(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def decode(cls, raw: str) -> "Draft":
        try:
            data = json.loads(raw)
        except ValueError:
            return cls(text=raw, items=None, waiting=WAIT_ADDRESS)  # formato de fase 2 (solo texto)
        if not isinstance(data, dict):
            return cls(text=raw, items=None, waiting=WAIT_ADDRESS)
        if "texto" in data:  # formato de fase 2: pedido esperando la dirección
            return cls(text=data["texto"], items=data.get("items") or [],
                       unknown=data.get("desconocidos") or [], waiting=WAIT_ADDRESS)
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)


class CheckoutMixin:
    """Métodos de BotService para borradores, confirmación y modificación."""

    # ----------------------------------------------------------- borrador
    def _key(self, user):
        return user.channel, str(user.id)

    def _get_draft(self, user) -> Draft | None:
        found = self.repo.get_pending_order(*self._key(user))
        if found is None:
            return None
        raw, created = found
        try:
            created_at = datetime.strptime(created, "%Y-%m-%d %H:%M:%S").replace(tzinfo=self.tz)
        except ValueError:
            created_at = None
        if created_at is None or self.now() - created_at > PENDING_TTL:
            self.repo.clear_pending_order(*self._key(user))
            return None
        return Draft.decode(raw)

    def _save_draft(self, user, draft: Draft) -> None:
        draft.token = secrets.token_hex(3)  # cada versión del resumen tiene sus propios botones
        self.repo.set_pending_order(*self._key(user), draft.encode(), self.stamp())

    def _clear_draft(self, user) -> None:
        self.repo.clear_pending_order(*self._key(user))

    def _draft_for_ai(self, draft: Draft | None) -> str:
        if draft is None:
            return ""
        names = {p.sku: p.name for p in self.repo.list_products()}
        items = ", ".join(f"{q} x {names.get(s, s)} ({s})" for s, q in (draft.items or []))
        kind = f"modificación del pedido {draft.modifies}" if draft.modifies else "pedido nuevo"
        wait = "esperando la dirección" if draft.waiting == WAIT_ADDRESS else "esperando confirmación"
        return f"{kind}; {items or '(sin productos)'}; dirección: {draft.address or '(falta)'}; {wait}"

    def _resolve(self, draft: Draft, products: dict[str, Product]) -> tuple[list[tuple[Product, int]], list[str]]:
        pairs, unknown = [], list(draft.unknown)
        for sku, qty in draft.items or []:
            p = products.get(str(sku).upper())
            if p is None or not p.active:
                unknown.append(p.name if p else str(sku))
            else:
                pairs.append((p, int(qty)))
        return pairs, unknown

    def _summary(self, user, draft: Draft, intro: str = "", note: str = "") -> list:
        """Resumen del borrador con precios y total de la base, y los botones."""
        products = {p.sku.upper(): p for p in self.repo.list_products()}
        pairs, unknown = self._resolve(draft, products)
        ev = evaluate(pairs, self.s.rules, self.today(), unknown)
        today = self.today()
        if draft.modifies:
            head = f"✏️ <b>Así quedaría tu pedido</b> <code>{esc(draft.modifies)}</code>"
        else:
            head = "🧾 <b>Revisá tu pedido</b>"
        rows = ([esc(intro), ""] if intro else []) + ([note, ""] if note else []) + [head, ""]
        for l in ev.lines:
            p = products.get(l.sku.upper())
            if p is not None and not p.price_is_valid(today):
                rows.append(f"• {l.qty} × {esc(l.name)} — precio a confirmar")
            else:
                rows.append(f"• {l.qty} × {esc(l.name)} — {self.money(l.unit_price)} c/u = "
                            f"<b>{self.money(l.subtotal)}</b>")
        rows += ["", "<b>Envío:</b> incluido, sin costo",
                 f"<b>Dirección:</b> {esc(draft.address)}",
                 f"<b>Total: {self.money(ev.total + COSTO_ENVIO)}</b>"]
        if ev.status == ESTADO_REVISION:
            rows += ["", "⚠️ Si lo confirmás, queda <b>en revisión</b> porque:"]
            rows += [f"• {esc(r)}" for r in ev.reasons]
        rows += ["", "¿Lo confirmo? Respondé «sí», o decime qué querés cambiar."]
        t = draft.token
        return [Outgoing(user.id, "\n".join(rows),
                         [("✅ Confirmar", f"cf:{t}"), ("📍 Cambiar dirección", f"cd:{t}"),
                          ("❌ Cancelar", f"cx:{t}")])]

    # ----------------------------------------------------- armar el borrador
    def start_order(self, user, text: str, items: list[ParsedItem] | None, intro: str = "") -> list:
        """Arma (o reemplaza) el borrador con lo que pidió el cliente y muestra el resumen."""
        if not items:
            return [Outgoing(user.id, esc(intro) if intro else NO_ORDER_MSG)]
        problems = self._clarifications(items)
        if problems:
            return [Outgoing(user.id, "No pude armar el pedido todavía:\n• " + "\n• ".join(problems)
                             + "\n\nMandámelo de nuevo corregido, completo. 🙏")]
        pairs = [[it.product.sku, it.qty] for it in items if it.product and it.qty]
        unknown = [it.unknown_name for it in items if it.error == "no_existe"]  # solo modo "review"
        try:
            previous = self._get_draft(user)
            draft = Draft(text=text, items=pairs, unknown=unknown)
            if previous is not None and previous.modifies:   # sigue siendo una modificación
                draft.modifies, draft.address = previous.modifies, previous.address
            else:
                customer = self.repo.get_customer(*self._key(user))
                draft.address = (previous.address if previous and previous.address
                                 else customer.address if customer else "")
            if not draft.address:
                draft.waiting = WAIT_ADDRESS
                self._save_draft(user, draft)
                return [Outgoing(user.id, (f"{esc(intro)}\n\n" if intro else "") + ASK_ADDRESS)]
            self._save_draft(user, draft)
            return self._summary(user, draft, intro)
        except StorageError as exc:
            return self._storage_failure(user, exc, "armar el pedido")

    # ------------------------------------------------------------- dirección
    @staticmethod
    def _address_problem(address: str) -> str | None:
        if "?" in address:  # es una pregunta, no una dirección
            return ("Para seguir primero necesito la dirección de envío: calle, número y localidad. "
                    "Después te respondo lo que quieras 🙌\n(o /cancelar para descartar el pedido)")
        if len(address) < MIN_ADDRESS_LEN or not any(ch.isalpha() for ch in address):
            return ("Esa dirección parece incompleta 🤔. Mandame calle, número y localidad, por ejemplo:\n"
                    "<i>Av. Mate de Luna 1234, San Miguel de Tucumán</i>\n(o /cancelar para descartar el pedido)")
        if len(address) > MAX_ADDRESS_LEN:
            return f"La dirección es muy larga (máximo {MAX_ADDRESS_LEN} caracteres)."
        return None

    def _save_address(self, user, address: str) -> Customer:
        return self.repo.save_customer(Customer(user.channel, str(user.id), user.name, address.strip()),
                                       self.stamp())

    def receive_address(self, user, address: str, draft: Draft) -> list:
        """El cliente mandó la dirección que le pedimos."""
        address = address.strip()
        if _norm(address) in NO:
            return self.cancel_draft(user)
        problem = self._address_problem(address)
        if problem:
            return [Outgoing(user.id, problem)]
        return self._set_address(user, draft, address)

    def _set_address(self, user, draft: Draft, address: str) -> list:
        """Guarda la dirección (también como la habitual del cliente) y muestra el resumen."""
        try:
            self._save_address(user, address)
            draft.address, draft.waiting = address, WAIT_CONFIRM
            if draft.items is None:  # borrador viejo (solo texto): se interpreta ahora
                self._clear_draft(user)
                return self.start_order(user, draft.text, self._interpret(draft.text))
            self._save_draft(user, draft)
            note = f"📍 Listo, va a <b>{esc(address)}</b> (la guardé para tus próximos pedidos)."
            return self._summary(user, draft, note=note)
        except StorageError as exc:
            return self._storage_failure(user, exc, "guardar la dirección")

    def ask_new_address(self, user, token: str | None = None) -> list:
        try:
            draft = self._get_draft(user)
            if draft is None or (token and token != draft.token):
                return self._stale(user)
            draft.waiting = WAIT_ADDRESS
            self._save_draft(user, draft)
        except StorageError as exc:
            return self._storage_failure(user, exc, "cambiar la dirección")
        return [Outgoing(user.id, "📍 Dale, mandame la nueva dirección: calle, número y localidad.\n"
                                  "(o /cancelar para descartar)")]

    # ---------------------------------------------------- confirmar / cancelar
    def _stale(self, user) -> list:
        return [Outgoing(user.id, "Ese resumen ya no está vigente (cambió o venció). "
                                  "Usá el último que te mandé, o mandame tu pedido de nuevo 🙏")]

    def confirm_draft(self, user, token: str | None = None) -> list:
        try:
            draft = self._get_draft(user)
        except StorageError as exc:
            return self._storage_failure(user, exc, "confirmar el pedido")
        if draft is None:
            if token:
                return self._stale(user)
            return [Outgoing(user.id, "No tengo ningún pedido para confirmar. Mandame lo que necesitás 🙂")]
        if token and token != draft.token:
            return self._stale(user)
        if draft.waiting == WAIT_ADDRESS:
            return [Outgoing(user.id, ASK_ADDRESS)]
        if draft.modifies:
            return self._apply_modification(user, draft)
        return self._place_order(user, draft)

    def cancel_draft(self, user, token: str | None = None) -> list:
        try:
            draft = self._get_draft(user)
            if draft is None or (token and token != draft.token):
                if token:
                    return self._stale(user)
                return [Outgoing(user.id, "No tenés ningún pedido sin confirmar.")]
            self._clear_draft(user)
        except StorageError as exc:
            return self._storage_failure(user, exc, "cancelar el pedido")
        if draft.modifies:
            return [Outgoing(user.id, f"❌ Listo, tu pedido <code>{draft.modifies}</code> queda como estaba.")]
        return [Outgoing(user.id, "❌ Listo, descarté ese pedido. Cuando quieras, mandame uno nuevo.")]

    # ------------------------------------------------------- registrar el pedido
    def _place_order(self, user, draft: Draft) -> list:
        with self._lock:
            try:
                products = {p.sku.upper(): p for p in self.repo.list_products()}
            except StorageError as exc:
                return self._storage_failure(user, exc, "leer el stock para un pedido")
            pairs, unknown = self._resolve(draft, products)
            if not pairs and not unknown:
                return [Outgoing(user.id, NO_ORDER_MSG)]
            ev = evaluate(pairs, self.s.rules, self.today(), unknown)
            now = self.stamp()
            order = Order(order_id=self._new_order_id(), created_at=now, customer_id=user.id,
                          customer_name=user.name, original_text=draft.text, lines=ev.lines,
                          status=ev.status, reason="; ".join(ev.reasons), updated_at=now,
                          address=draft.address, shipping_cost=COSTO_ENVIO, channel=user.channel)
            try:
                if order.status == ESTADO_CONFIRMADO:
                    self._confirm_and_record(order)
                else:
                    self.repo.add_order(order)
                self._clear_draft(user)
                customer = self.repo.get_customer(*self._key(user))
                if customer is not None and customer.name != user.name:
                    customer.name = user.name
                    self.repo.save_customer(customer, self.stamp())
            except StorageError as exc:
                return self._storage_failure(user, exc, "registrar un pedido")

        tail = f"\n\n✏️ Si necesitás cambiar algo, avisame en los próximos {self.s.rules.modify_minutes} minutos."
        if order.status == ESTADO_CONFIRMADO:
            return [Outgoing(user.id, self.format_order(order, "✅ <b>¡Pedido confirmado!</b>") + tail)]
        out = [Outgoing(user.id, self.format_order(order, "🟡 <b>Recibimos tu pedido</b>")
                        + "\n\nQuedó <b>en revisión</b>; te aviso apenas el área administrativa lo resuelva."
                        + tail)]
        return out + self._notify_review(order, f"🔔 <b>Pedido derivado a revisión</b>\nCliente: "
                                                f"<b>{esc(user.name)}</b> (<code>{user.id}</code>)")

    def _notify_review(self, order: Order, header: str) -> list:
        if not self.s.admin_chat_ids:
            log.warning("Pedido %s en revisión pero no hay ADMIN_CHAT_IDS configurados", order.order_id)
            return []
        return self._admins(self.format_order(order, header),
                            [("✅ Aprobar", f"ap:{order.order_id}"), ("❌ Rechazar", f"rj:{order.order_id}")])

    # ------------------------------------------------------------ modificar
    def _modifiable_order(self, user) -> Order | None:
        """Último pedido del cliente que todavía se puede modificar."""
        mine = [o for o in self.repo.list_orders()
                if o.customer_id == user.id and o.channel == user.channel]
        if not mine:
            return None
        last = mine[-1]
        if last.status not in (ESTADO_CONFIRMADO, ESTADO_REVISION):
            return None
        try:
            created = datetime.strptime(last.created_at, "%Y-%m-%d %H:%M:%S").replace(tzinfo=self.tz)
        except ValueError:
            return None
        if self.now() - created > timedelta(minutes=self.s.rules.modify_minutes):
            return None
        return last

    def start_modification(self, user, items: list[ParsedItem] | None = None, address: str = "",
                           intro: str = "", text: str = "") -> list:
        """Arma un borrador a partir del último pedido, con los cambios que pidió el cliente."""
        try:
            order = self._modifiable_order(user)
        except StorageError as exc:
            return self._storage_failure(user, exc, "buscar tu pedido")
        if order is None:
            out = [Outgoing(user.id, "Tu último pedido ya no se puede modificar desde acá (pasó el tiempo para "
                                     "cambios o ya se resolvió). Le aviso al equipo para que te ayude 🙌")]
            if text and self.s.admin_chat_ids:
                out += self._admins(f"✏️ <b>{esc(user.name)}</b> (<code>{user.id}</code>) pide un cambio "
                                    f"fuera de término:\n«{esc(text)}»")
            return out
        draft = Draft(text=order.original_text, items=[[l.sku, l.qty] for l in order.lines],
                      address=order.address, modifies=order.order_id)
        if items:
            problems = self._clarifications(items)
            if problems:
                return [Outgoing(user.id, "No pude armar el cambio:\n• " + "\n• ".join(problems))]
            draft.items = [[it.product.sku, it.qty] for it in items if it.product and it.qty]
            draft.unknown = [it.unknown_name for it in items if it.error == "no_existe"]
        if address:
            problem = self._address_problem(address)
            if problem:
                return [Outgoing(user.id, problem)]
        try:
            if address:
                draft.address = address
                self._save_address(user, address)
            self._save_draft(user, draft)
            return self._summary(user, draft, intro,
                                 note="" if (items or address) else
                                 "Decime qué querés cambiar (por ejemplo «que sean 3 cocas» o «sacá la fanta»), "
                                 "o tocá 📍 para cambiar la dirección.")
        except StorageError as exc:
            return self._storage_failure(user, exc, "modificar tu pedido")

    def _apply_modification(self, user, draft: Draft) -> list:
        with self._lock:
            try:
                order = self.repo.get_order(draft.modifies)
                if (order is None or order.customer_id != user.id
                        or order.status not in (ESTADO_CONFIRMADO, ESTADO_REVISION)):
                    self._clear_draft(user)
                    return [Outgoing(user.id, "Ese pedido ya no se puede modificar (puede que ya se haya "
                                              "resuelto). Si querés, mandame uno nuevo 🙏")]
                products = {p.sku.upper(): p for p in self.repo.list_products()}
                old_items = sorted((l.sku.upper(), l.qty) for l in order.lines)
                new_items = sorted((str(s).upper(), int(q)) for s, q in draft.items or [])
                stamp = self.stamp()
                if old_items == new_items and not draft.unknown:
                    if draft.address == order.address:
                        self._clear_draft(user)
                        return [Outgoing(user.id, "No hay cambios: tu pedido queda igual 👍")]
                    order.address, order.updated_at = draft.address, stamp
                    order = self.repo.update_order(order)
                else:
                    # Se devuelve lo que el pedido viejo había descontado y se evalúa el nuevo.
                    returned = ({l.sku.upper(): l.qty for l in order.lines}
                                if order.status == ESTADO_CONFIRMADO else {})
                    adjusted = {k: replace(p, stock=p.stock + returned.get(k, 0)) for k, p in products.items()}
                    pairs, unknown = self._resolve(draft, adjusted)
                    if not pairs and not unknown:
                        return [Outgoing(user.id, "El pedido quedaría vacío. Si querés anularlo, "
                                                  "avisale al equipo o rechazá los cambios con ❌.")]
                    ev = evaluate(pairs, self.s.rules, self.today(), unknown)
                    taken = {l.sku.upper(): l.qty for l in ev.lines} if ev.status == ESTADO_CONFIRMADO else {}
                    net = {k: returned.get(k, 0) - taken.get(k, 0) for k in set(returned) | set(taken)}
                    net = {k: v for k, v in net.items() if v}
                    sku_case = {k: p.sku for k, p in products.items()}
                    deltas = {sku_case.get(k, k): v for k, v in net.items()}
                    if deltas:
                        self.repo.apply_stock_deltas(deltas, stamp)
                    order.lines, order.status, order.address = ev.lines, ev.status, draft.address
                    order.reason = "; ".join(["Modificado por el cliente"] + ev.reasons)
                    order.updated_at, order.reviewed_by = stamp, ""
                    try:
                        order = self.repo.update_order(order)
                    except StorageError:
                        if deltas:
                            self.repo.apply_stock_deltas({k: -v for k, v in deltas.items()}, self.stamp())
                        raise
                self._clear_draft(user)
            except StorageError as exc:
                return self._storage_failure(user, exc, "modificar tu pedido")

        who = f"<b>{esc(user.name)}</b> (<code>{user.id}</code>)"
        if order.status == ESTADO_CONFIRMADO:
            out = [Outgoing(user.id, self.format_order(order, "✏️ <b>¡Pedido modificado y confirmado!</b>"))]
            if self.s.admin_chat_ids:
                out += self._admins(self.format_order(order, f"✏️ <b>Pedido modificado por el cliente</b>\n{who}"))
            return out
        out = [Outgoing(user.id, self.format_order(order, "✏️ <b>Modificamos tu pedido</b>")
                        + "\n\nQuedó <b>en revisión</b>; te aviso apenas el área administrativa lo resuelva.")]
        return out + self._notify_review(order, f"✏️🔔 <b>Pedido modificado, en revisión</b>\n{who}")

    # ---------------------------------------------------------- conversación
    def _interpret(self, text: str, catalog: list[Product] | None = None, use_ai: bool = True):
        catalog = catalog if catalog is not None else [p for p in self.repo.list_products() if p.active]
        r = self.s.rules
        if use_ai:
            return parse_order(text, catalog, r.match_threshold, r.max_qty_per_item,
                               config=self.s.ai, client=self.ai_client)
        return rules_parser.parse_order(text, catalog, r.match_threshold, r.max_qty_per_item)

    def converse(self, user, text: str, draft: Draft | None = None) -> list:
        """Gemini entiende el mensaje en contexto y redacta; los datos los pone el bot."""
        if self.ai_client is None and not self.s.ai.usable:
            return self._converse_rules(user, text, draft)
        try:
            catalog = [p for p in self.repo.list_products() if p.active]
            history = self._history(user)
            orders = self._orders_summary(user)
            draft_txt = self._draft_for_ai(draft)
        except StorageError as exc:
            return self._storage_failure(user, exc, "leer el catálogo para un pedido")
        r = self.s.rules
        reply = conversar(text, catalog, historial=history, pedidos=orders, borrador=draft_txt,
                          negocio=self.s.business.as_text(), nombre_negocio=self.s.business.name,
                          max_qty=r.max_qty_per_item, config=self.s.ai, client=self.ai_client)
        if reply is None:  # Gemini no respondió: reglas, sin volver a llamarlo
            return self._converse_rules(user, text, draft, catalog)

        def parsed():
            items = items_a_parsed(reply.items, catalog, r.match_threshold, r.max_qty_per_item)
            return items or self._interpret(text, catalog, use_ai=False)

        intent = reply.intencion
        if draft is not None and intent == "confirmar":
            return self.confirm_draft(user)
        if draft is not None and intent == "cancelar":
            return self.cancel_draft(user)
        if intent == "cambiar_direccion" or (intent == "cambio_pedido" and draft is not None and not reply.items):
            if draft is not None:
                if reply.direccion and not self._address_problem(reply.direccion):
                    return self._set_address(user, draft, reply.direccion)
                return self.ask_new_address(user)
            if reply.direccion:
                return self.start_modification(user, address=reply.direccion, intro=reply.respuesta, text=text)
            return [Outgoing(user.id, esc(reply.respuesta) if reply.respuesta else
                             "Dale, decime la nueva dirección (calle, número y localidad) 📍")]
        if intent == "pedido" or (intent == "cambio_pedido" and draft is not None):
            return self.start_order(user, text, parsed(), intro=reply.respuesta)
        if intent == "cambio_pedido":
            return self.start_modification(user, items=parsed() if reply.items else None,
                                           address=reply.direccion, intro=reply.respuesta, text=text)
        if intent == "consulta" and reply.consulta_skus:
            by_sku = {p.sku: p for p in catalog}
            today = self.today()
            facts = []
            for sku in reply.consulta_skus:
                p = by_sku[sku]
                price = self.money(p.price) if p.price_is_valid(today) else "precio a confirmar"
                avail = "✅ disponible" if p.stock > 0 else "❌ sin stock por ahora"
                facts.append(f"• {esc(p.name)} — {price} {avail}")
            head = esc(reply.respuesta) if reply.respuesta else "Esto es lo que tengo:"
            msg = head + "\n\n" + "\n".join(facts) + "\n\n🚚 Envío incluido, sin costo."
        elif intent == "consulta":
            msg = esc(reply.respuesta) if reply.respuesta else "Buena pregunta 🤔 Lo consulto con el equipo y te aviso."
        else:  # charla, o confirmar/cancelar sin borrador
            msg = esc(reply.respuesta) if reply.respuesta else NO_ORDER_MSG
        if draft is not None and draft.waiting == WAIT_CONFIRM:
            msg += "\n\n🧾 Tu pedido sigue esperando confirmación: respondé «sí» o tocá ✅ en el resumen."
        return [Outgoing(user.id, msg)]

    def _converse_rules(self, user, text: str, draft: Draft | None, catalog=None) -> list:
        """Sin Gemini: «sí»/«no» confirman o cancelan; lo demás se interpreta por reglas."""
        if draft is not None:
            if _norm(text) in YES:
                return self.confirm_draft(user)
            if _norm(text) in NO:
                return self.cancel_draft(user)
        try:
            items = self._interpret(text, catalog, use_ai=catalog is None)
        except StorageError as exc:
            return self._storage_failure(user, exc, "leer el catálogo para un pedido")
        if draft is not None and not items:
            return [Outgoing(user.id, "¿Confirmo tu pedido? Respondé «sí» para confirmarlo o «no» para "
                                      "cancelarlo, o mandame la lista completa como la querés.")]
        return self.start_order(user, text, items)
