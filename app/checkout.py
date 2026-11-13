"""
Fase 3: borrador del pedido, confirmación, dirección y modificación.

Flujo de un pedido nuevo (_next_step decide el paso siguiente):
  mensaje → se interpreta → BORRADOR (no se registra nada ni se toca el stock)
    1. ¿Alcanza el stock? Si no, se le dice al cliente cuánto queda de cada cosa y
       NO puede confirmar: se le ofrece [🔧 Ajustar a lo que hay] o pedir otra cosa
       (esperando "stock"). Un producto sin stock no se puede pedir.
    2. ¿Tiene dirección? Si no, se le pide (esperando "direccion").
    3. Resumen con precios, total y dirección + botones
         [✅ Confirmar] [📍 Cambiar dirección] [❌ Cancelar]
  El cliente confirma (botón, «sí») → se vuelve a mirar el stock (otro
  cliente pudo llevárselo) y recién ahí se evalúa la regla y se registra
  CONFIRMADO o EN REVISION (revisión: solo si supera el total máximo; el admin la ve en la web).

Modificar un pedido ya hecho (productos y/o dirección): mientras no esté
rechazado y dentro de Rules.modify_minutes desde que se hizo. Se arma un
borrador a partir del pedido, el cliente lo cambia y confirma; entonces se
devuelve el stock del pedido viejo, se evalúa el nuevo y se descuenta lo
que corresponde, todo en un paso.

Dirección guardada: la primera que da el cliente se guarda sola. Si después un
pedido (o una modificación) va a otra dirección, NO se pisa la guardada: una vez
registrado el pedido, el bot pregunta cuál dejar para la próxima
[🏠 La de siempre] [📍 La nueva].

Todo esto es independiente del canal: los botones son (texto, dato) genéricos
y siempre hay una alternativa escrita («sí», «cancelar»). Los comandos con barra siguen
andando (Telegram los muestra en el menú), pero los textos no los nombran: en WhatsApp
se escriben palabras («catálogo», «cancelar»).
"""
from __future__ import annotations

import json
import logging
import secrets
import time
import unicodedata
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timedelta

from . import parser as rules_parser
from .ai_parser import conversar, items_a_parsed, parse_order
from .models import (COSTO_ENVIO, ESTADO_CONFIRMADO, ESTADO_REVISION, PAGO_PENDIENTE,
                     Customer, Order, ParsedItem, Product)
from .rules import evaluate
from .messages import Outgoing, esc
from .storage.base import StorageError

log = logging.getLogger(__name__)

WAIT_CONFIRM = "confirmacion"
WAIT_ADDRESS = "direccion"
WAIT_PAYMENT = "pago"             # confirmó el resumen: falta elegir el medio de pago
WAIT_STOCK = "stock"              # no alcanza el stock: se espera que ajuste o cambie el pedido
WAIT_SAVE = "guardar_direccion"   # pedido hecho a otra dirección: ¿cuál queda guardada?
PENDING_TTL = timedelta(hours=24)
MIN_ADDRESS_LEN = 8
MAX_ADDRESS_LEN = 200

YES = {"si", "sii", "sip", "dale", "ok", "okey", "oka", "confirmo", "confirmar", "confirma",
       "listo", "de una", "perfecto", "va", "bueno", "si dale", "dale si", "si confirmo",
       "si por favor", "si porfa", "correcto", "esta bien", "todo bien"}
NO = {"no", "nop", "cancelar", "cancela", "cancelo", "no gracias", "deja", "dejalo", "nada"}
# Palabras que, en cualquier parte del mensaje, piden descartar el pedido en curso.
CANCEL_WORDS = {"cancelar", "cancela", "cancelalo", "cancelo", "anula", "anular", "anulalo",
                "dejalo", "olvidate", "olvidalo", "descarta", "descartalo"}
PAYMENT_WORDS = {
    "efectivo": {"efectivo", "cash", "contado", "billete", "billetes"},
    "transferencia": {"transferencia", "transferencias", "transfiero", "transferir", "transfer",
                      "alias", "cbu", "banco", "mp", "mercadopago"},
}
# Una dirección casi siempre tiene número; si no, alguna de estas palabras.
STREET_WORDS = {"calle", "av", "avenida", "pasaje", "pje", "ruta", "barrio", "bo", "manzana", "mza",
                "mz", "lote", "km", "sn", "piso", "dpto", "depto", "local", "esquina", "esq"}
# Un mensaje que empieza así es un cambio en el pedido, no una dirección.
ORDER_VERBS = {"agrega", "agregale", "agregame", "agrego", "suma", "sumale", "sumame", "saca", "sacale",
               "quita", "quitale", "cambia", "cambiale", "mejor", "espera", "pone", "ponele", "poneme",
               "quiero", "mandame", "traeme", "falta", "faltan", "tambien", "ademas", "y"}

NO_ORDER_MSG = ("No encontré un pedido en tu mensaje 🤔. Escribí productos y cantidades, "
                "por ejemplo: <i>2 coca, 3 agua sin gas</i>. Si querés ver qué hay, escribí «catálogo».")
WAIT_AI_MSG = ("Dame un momento que estoy procesando tu mensaje 🙏 "
               "Hay mucha demanda y tardo un poquito más de lo normal.")
AI_DOWN_MSG = ("Uy, ahora no puedo procesar tu mensaje 😕. "
               "Escribime de nuevo en unos minutos, por favor.")
NOT_AVAILABLE_NOTE = "Del resto que pediste no tengo ahora."
NOTHING_AVAILABLE = "No tengo eso ahora 😕. Escribí «catálogo» para ver lo que hay hoy."
ASK_ADDRESS = ("📍 ¿A qué dirección te lo enviamos? <b>El envío está incluido, sin costo.</b>\n"
               "Respondé con calle, número y localidad, por ejemplo:\n"
               "<i>Av. Mate de Luna 1234, San Miguel de Tucumán</i>\n"
               "(o escribí «cancelar» para descartar el pedido)")
ADDRESS_INCOMPLETE = ("Para mandártelo necesito la dirección con <b>calle y número</b> (y la localidad), "
                      "por ejemplo:\n<i>Av. Mate de Luna 1234, San Miguel de Tucumán</i>\n"
                      "(o escribí «cancelar» para descartar el pedido)")


def _promises_follow_up(text: str) -> bool:
    """¿La respuesta promete consultar con el equipo? («lo consulto con el equipo y te aviso»)"""
    t = _norm(text)
    return "consult" in t and "equipo" in t


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
    payment: str = ""                               # medio de pago elegido (efectivo | transferencia)
    # Dudas sin resolver: lo entendido ya está en items, esto falta aclarar. Mientras haya
    # alguna, no se puede confirmar (salvo «Seguir sin eso»). Cada una: {raw, tipo, sku,
    # cantidad, candidatos, pregunta}; tipo = bulto | sin_cantidad | ambiguo.
    pending: list[dict] = field(default_factory=list)
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


class _SoldOut(Exception):
    """Interno: al confirmar una modificación ya no alcanza el stock."""


class CheckoutMixin:
    """Métodos de BotService para borradores, confirmación y modificación."""

    # ----------------------------------------------------------- borrador
    def _key(self, user):
        return user.channel, str(user.id)

    def _get_draft(self, user, include_save_question: bool = False) -> Draft | None:
        """Borrador del cliente. La pregunta «¿qué dirección guardo?» no es un pedido:
        solo se devuelve si se pide explícitamente."""
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
        draft = Draft.decode(raw)
        if draft.waiting == WAIT_SAVE and not include_save_question:
            return None
        return draft

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
        wait = {WAIT_ADDRESS: "esperando la dirección",
                WAIT_PAYMENT: "confirmado: falta que elija cómo paga (efectivo o transferencia)",
                WAIT_STOCK: "no alcanza el stock: se le ofreció ajustar a lo que hay (si dice que sí, "
                            "es 'confirmar'; si pide otra cosa, es 'pedido' con la lista completa)",
                }.get(draft.waiting, "esperando confirmación")
        doubts = "; falta aclarar: " + " | ".join(p["raw"] for p in draft.pending) if draft.pending else ""
        return f"{kind}; {items or '(sin productos)'}; dirección: {draft.address or '(falta)'}; {wait}{doubts}"

    def _resolve(self, draft: Draft, products: dict[str, Product]) -> tuple[list[tuple[Product, int]], list[str]]:
        pairs, unknown = [], list(draft.unknown)
        for sku, qty in draft.items or []:
            p = products.get(str(sku).upper())
            if p is None or not p.active:
                unknown.append(p.name if p else str(sku))
            else:
                pairs.append((p, int(qty)))
        return pairs, unknown

    @staticmethod
    def _merge(items: list[ParsedItem]) -> list[list]:
        """[[sku, cantidad]] sumando si el mismo producto aparece dos veces."""
        out: dict[str, list] = {}
        for it in items:
            if it.product and it.qty:
                key = it.product.sku.upper()
                if key in out:
                    out[key][1] += it.qty
                else:
                    out[key] = [it.product.sku, it.qty]
        return list(out.values())

    def _products_for(self, draft: Draft) -> dict[str, Product]:
        """Catálogo actual. Si se modifica un pedido ya confirmado, lo que ese pedido
        tiene reservado cuenta como disponible (se le devuelve al modificarlo)."""
        products = {p.sku.upper(): p for p in self.repo.list_products()}
        if draft.modifies:
            order = self.repo.get_order(draft.modifies)
            if order is not None and order.status == ESTADO_CONFIRMADO:
                held: dict[str, int] = {}
                for l in order.lines:
                    held[l.sku.upper()] = held.get(l.sku.upper(), 0) + l.qty
                products = {k: replace(p, stock=p.stock + held.get(k, 0)) for k, p in products.items()}
        return products

    def _sellable(self) -> list[Product]:
        """Lo que se le puede ofrecer al cliente: activo y con stock. Lo agotado ni se nombra."""
        return [p for p in self.repo.list_products() if p.active and p.stock > 0]

    def _is_new_customer(self, user, exclude: str = "") -> bool:
        """Cliente sin ningún pedido pagado todavía (para la revisión del primer pedido)."""
        return not any(o.is_paid for o in self.repo.list_orders()
                       if o.channel == user.channel and o.customer_id == user.id and o.order_id != exclude)

    def _shortages(self, pairs: list[tuple[Product, int]]) -> list[tuple[Product, int, int]]:
        """[(producto, pedido, disponible)] de lo que no alcanza."""
        if not self.s.rules.require_stock:
            return []
        return [(p, q, max(int(p.stock), 0)) for p, q in pairs if q > p.stock]

    def _next_step(self, user, draft: Draft, intro: str = "", note: str = "") -> list:
        """Decide qué sigue con el borrador: avisar falta de stock, pedir dirección o resumen."""
        pairs, _ = self._resolve(draft, self._products_for(draft))
        short = self._shortages(pairs)
        if short:
            return self._stock_message(user, draft, pairs, short, intro, note)
        if not draft.address:
            draft.waiting = WAIT_ADDRESS
            self._save_draft(user, draft)
            pre = "".join(f"{x}\n\n" for x in (esc(intro) if intro else "", note) if x)
            return [Outgoing(user.id, pre + ASK_ADDRESS)]
        draft.waiting = WAIT_CONFIRM
        self._save_draft(user, draft)
        return self._summary(user, draft, intro, note)

    def _stock_message(self, user, draft: Draft, pairs, short, intro: str = "", note: str = "") -> list:
        """Le cuenta al cliente qué no alcanza (con lo que queda) y ofrece ajustar."""
        rows = ([esc(intro), ""] if intro else []) + ([note, ""] if note else [])
        missing = {p.sku for p, _, _ in short}
        adjusted = [(p, q if p.sku not in missing else min(q, max(int(p.stock), 0))) for p, q in pairs]
        adjusted = [(p, q) for p, q in adjusted if q > 0]
        if not adjusted:
            self._clear_draft(user)
            return [Outgoing(user.id, "\n".join(rows + [NOTHING_AVAILABLE]))]
        # Solo se nombra lo que todavía tiene stock; lo agotado no se menciona.
        partial = [(p, asked, avail) for p, asked, avail in short if avail > 0]
        if partial:
            rows += ["⚠️ <b>No me alcanza el stock para todo:</b>"]
            rows += [f"• {esc(p.name)}: pediste {asked}, me quedan {avail}." for p, asked, avail in partial]
        if len(partial) < len(short):
            rows += [NOT_AVAILABLE_NOTE]
        draft.waiting = WAIT_STOCK
        self._save_draft(user, draft)
        rows += ["", "¿Querés que lo ajuste a lo que hay? Quedaría:"]
        rows += [f"• {q} × {esc(p.name)}" for p, q in adjusted]
        rows += ["", "Respondé «sí» para ajustarlo, o mandame la lista como la querés."]
        t = draft.token
        return [Outgoing(user.id, "\n".join(rows),
                         [("🔧 Ajustar a lo que hay", f"aj:{t}"), ("❌ Cancelar", f"cx:{t}")])]

    def adjust_to_stock(self, user, token: str | None = None) -> list:
        """Baja cada cantidad a lo que hay en stock (y saca lo que no hay)."""
        try:
            draft = self._get_draft(user)
            if draft is None or (token and token != draft.token):
                return self._stale(user)
            if draft.waiting != WAIT_STOCK:
                return self._next_step(user, draft)
            products = self._products_for(draft)
            new_items = []
            for sku, qty in draft.items or []:
                p = products.get(str(sku).upper())
                n = min(int(qty), max(int(p.stock), 0)) if p is not None else 0
                if n > 0:
                    new_items.append([p.sku, n])
            if not new_items:
                self._clear_draft(user)
                return [Outgoing(user.id, "No me queda nada de lo que pediste 😕. Escribí «catálogo» para ver lo que hay, o pedime otra cosa.")]
            draft.items = new_items
            return self._next_step(user, draft, note="🔧 Listo, lo ajusté a lo que hay.")
        except StorageError as exc:
            return self._storage_failure(user, exc, "ajustar el pedido")

    def _summary(self, user, draft: Draft, intro: str = "", note: str = "") -> list:
        """Resumen del borrador con precios y total de la base, y los botones."""
        self._event(user, "resumen", detail="modificacion" if draft.modifies else "")
        products = self._products_for(draft)
        pairs, unknown = self._resolve(draft, products)
        ev = evaluate(pairs, self.s.rules, self.today(), unknown, self._is_new_customer(user))
        if draft.modifies:
            head = f"✏️ <b>Así quedaría tu pedido</b> <code>{esc(draft.modifies)}</code>"
        else:
            head = "🧾 <b>Revisá tu pedido</b>"
        rows = ([esc(intro), ""] if intro else []) + ([note, ""] if note else []) + [head, ""]
        for l in ev.lines:
            p = products.get(l.sku.upper())
            packs = p.pack_label(l.qty) if p is not None else ""
            name = esc(l.name) + (f" ({esc(packs)})" if packs else "")
            if p is not None and not p.has_price():
                rows.append(f"• {l.qty} × {name} — precio a confirmar")
            else:
                rows.append(f"• {l.qty} × {name} — {self.money(l.unit_price)} c/u = "
                            f"<b>{self.money(l.subtotal)}</b>")
        rows += ["", "<b>Envío:</b> incluido, sin costo",
                 f"<b>Dirección:</b> {esc(draft.address)}",
                 f"<b>Total: {self.money(ev.total + COSTO_ENVIO)}</b>"]
        if ev.status == ESTADO_REVISION:
            rows += ["", "⚠️ Si lo confirmás, queda <b>en revisión</b> porque:"]
            rows += [f"• {esc(r)}" for r in ev.reasons]
        t = draft.token
        if draft.pending:
            rows += ["", "❓ <b>Me falta saber:</b>"] + [f"• {p['pregunta']}" for p in draft.pending]
            rows += ["", "Respondeme eso y te lo dejo listo para confirmar, o tocá «Seguir sin eso»."]
            return [Outgoing(user.id, "\n".join(rows),
                             [("Seguir sin eso", f"sp:{t}"), ("📍 Cambiar dirección", f"cd:{t}"),
                              ("❌ Cancelar", f"cx:{t}")])]
        if draft.payment and not draft.modifies:
            label = {"efectivo": "efectivo al recibir", "transferencia": "transferencia"}.get(draft.payment, draft.payment)
            rows += [f"<b>Pago:</b> {label} (como la última vez)", "",
                     "¿Lo confirmo? Respondé «sí» y queda hecho. Si va a otra dirección, mandámela; "
                     "si querés cambiar algo, decime."]
            return [Outgoing(user.id, "\n".join(rows),
                             [("✅ Confirmar", f"cf:{t}"), ("💳 Cambiar pago", f"cp:{t}"),
                              ("❌ Cancelar", f"cx:{t}")])]
        rows += ["", "¿Lo confirmo? Respondé «sí», o decime qué querés cambiar."]
        return [Outgoing(user.id, "\n".join(rows),
                         [("✅ Confirmar", f"cf:{t}"), ("📍 Cambiar dirección", f"cd:{t}"),
                          ("❌ Cancelar", f"cx:{t}")])]

    # ----------------------------------------------------------- medio de pago habitual
    def _usual_payment(self, user) -> str:
        """El medio de pago del último pedido del cliente ("" si es su primer pedido)."""
        try:
            mine = [o for o in self.repo.list_orders()
                    if o.customer_id == user.id and o.channel == user.channel and o.payment_method]
        except StorageError:
            return ""
        return mine[-1].payment_method if mine else ""

    def change_payment(self, user, token: str | None = None) -> list:
        """Botón «Cambiar pago» del resumen: se pregunta el medio y, al elegirlo, queda hecho."""
        try:
            draft = self._get_draft(user)
            if draft is None or (token and token != draft.token):
                return self._stale(user)
            draft.payment = ""
            self._save_draft(user, draft)
        except StorageError as exc:
            return self._storage_failure(user, exc, "cambiar el medio de pago")
        return self.confirm_draft(user)

    def _quick_answer(self, user, text: str, draft: Draft) -> list | None:
        """Respuestas al resumen que no necesitan a Gemini: «sí», «no», un medio de pago
        («por transferencia» confirma con ese medio) o una dirección nueva. None = otra cosa."""
        if self._wants_confirm(text):
            return self.confirm_draft(user)
        if self._wants_cancel(text):
            return self.cancel_draft(user)
        if draft.waiting != WAIT_CONFIRM or draft.modifies or "?" in text:
            return None
        method = self._payment_choice(text)
        if method and len(_norm(text).split()) <= 4:
            draft.payment = method
            self._save_draft(user, draft)
            return self.confirm_draft(user)
        # Sin Gemini, una dirección suelta cambia la del pedido. Con Gemini, la saca él del mensaje
        # («mandalo a lo de mi vieja, Belgrano 300») y no se toma la frase entera como dirección.
        if self.ai_client is None and not self.s.ai.usable and \
                self._looks_like_address(text) and not self._address_problem(text):
            return self._set_address(user, draft, text.strip())
        return None

    # ----------------------------------------------------------- pendientes
    @staticmethod
    def _can_wait(it: ParsedItem) -> bool:
        """¿Esta duda puede quedar pendiente mientras se anota el resto del pedido?"""
        return (it.error in ("bulto", "sin_cantidad") and it.product is not None) or \
               (it.error == "ambiguo" and bool(it.candidates))

    def _pending_from(self, it: ParsedItem) -> dict:
        qty = it.qty
        if it.error == "ambiguo" and qty is None:
            words = rules_parser.normalize(it.raw).split()
            found = rules_parser._extract_qty(words)
            qty = int(found[0]) if found[0] is not None and found[1] else None
        return {"raw": it.raw, "tipo": it.error, "sku": it.product.sku if it.product else None,
                "cantidad": qty, "candidatos": [c.sku for c in it.candidates[:5]],
                "pregunta": self._clarifications([it])[0]}

    def resolve_pending(self, user, text: str, draft: Draft) -> list | None:
        """Respuesta corta a una duda pendiente, sin pasar por Gemini: un número («24») completa
        la cantidad que falta; un nombre («sin gas») elige entre los productos parecidos.
        Devuelve None si el mensaje no es eso (sigue el camino normal)."""
        if len(draft.pending) != 1:
            return None
        pend = draft.pending[0]
        sku, qty = None, None
        words = rules_parser.normalize(text).split()
        if pend["tipo"] in ("bulto", "sin_cantidad") and pend["sku"]:
            found = rules_parser._extract_qty([w for w in words if w not in rules_parser.FILLER])
            rest = [w for w in found[2] if w not in ("x",)]
            if found[0] is None or not found[1] or rest or found[0] > self.s.rules.max_qty_per_item:
                return None
            sku, qty = pend["sku"], int(found[0])
        elif pend["tipo"] == "ambiguo" and pend["cantidad"]:
            try:
                candidates = [p for p in self._sellable() if p.sku in pend["candidatos"]]
            except StorageError as exc:
                return self._storage_failure(user, exc, "leer el catálogo para un pedido")
            query = " ".join(w for w in words if w not in rules_parser.FILLER)
            if not query:
                return None
            product, _ = rules_parser.match_product(query, candidates, 0.5)
            if product is None:
                return None
            sku, qty = product.sku, int(pend["cantidad"])
        else:
            return None
        draft.items = [[s, q] for s, q in self._merge_pairs((draft.items or []) + [[sku, qty]])]
        draft.pending = []
        try:
            return self._next_step(user, draft)
        except StorageError as exc:
            return self._storage_failure(user, exc, "armar el pedido")

    @staticmethod
    def _merge_pairs(pairs) -> list[tuple[str, int]]:
        out: dict[str, int] = {}
        for sku, qty in pairs:
            out[sku] = out.get(sku, 0) + int(qty)
        return list(out.items())

    def skip_pending(self, user, token: str | None = None) -> list:
        """Botón «Seguir sin eso»: se descartan las dudas y se sigue con lo anotado."""
        try:
            draft = self._get_draft(user)
            if draft is None or (token and token != draft.token):
                return self._stale(user)
            draft.pending = []
            return self._next_step(user, draft)
        except StorageError as exc:
            return self._storage_failure(user, exc, "armar el pedido")

    def _pending_question(self, user, draft: Draft, note: str = "") -> list:
        """Repite lo que falta aclarar, con la salida «Seguir sin eso»."""
        rows = ([note, ""] if note else []) + ["❓ <b>Antes de confirmarlo me falta saber:</b>"]
        rows += [f"• {p['pregunta']}" for p in draft.pending]
        rows += ["", "Respondeme eso, o tocá «Seguir sin eso» para confirmar solo lo demás."]
        t = draft.token
        return [Outgoing(user.id, "\n".join(rows), [("Seguir sin eso", f"sp:{t}"), ("❌ Cancelar", f"cx:{t}")])]

    # ----------------------------------------------------- armar el borrador
    def start_order(self, user, text: str, items: list[ParsedItem] | None, intro: str = "") -> list:
        """Arma (o reemplaza) el borrador con lo que pidió el cliente y muestra el resumen."""
        if not items:
            return [Outgoing(user.id, esc(intro) if intro else NO_ORDER_MSG)]
        ask_mode = self.s.rules.on_unknown_product == "ask"
        # Lo que no existe no es una duda: en modo "ask" no se nombra, en "review" va a revisión.
        doubtful = [it for it in items if it.error and it.error != "no_existe"]
        pending: list[dict] = []
        if doubtful:
            kinds = sorted({it.error for it in items if it.error})
            self._event(user, "aclaracion", detail=",".join(kinds))
            good = [it for it in items if not it.error]
            try:
                previous = self._get_draft(user)
            except StorageError:
                previous = None
            modifying = previous is not None and bool(previous.modifies)
            if good and not modifying and all(self._can_wait(it) for it in doubtful):
                # Se anota lo que se entendió y se pregunta solo lo dudoso.
                pending = [self._pending_from(it) for it in doubtful]
                items = good + [it for it in items if it.error == "no_existe"]
            else:
                problems = self._clarifications(doubtful)
                return [Outgoing(user.id, "No pude armar el pedido todavía:\n• " + "\n• ".join(problems)
                                 + "\n\nMandámelo de nuevo corregido, completo. 🙏")]
        pairs = self._merge(items)
        note = ""
        if ask_mode:
            # Lo que no tenemos (o está agotado) no se nombra: se sigue con lo que sí hay.
            unknown = []
            if any(it.error == "no_existe" for it in items):
                if not pairs:
                    return [Outgoing(user.id, (f"{esc(intro)}\n\n" if intro else "") + NOTHING_AVAILABLE)]
                note = NOT_AVAILABLE_NOTE
        else:
            unknown = [it.unknown_name for it in items if it.error == "no_existe"]  # modo "review"
        try:
            previous = self._get_draft(user)
            draft = Draft(text=text, items=pairs, unknown=unknown, pending=pending)
            if previous is not None and previous.modifies:   # sigue siendo una modificación
                draft.modifies, draft.address = previous.modifies, previous.address
            else:
                customer = self.repo.get_customer(*self._key(user))
                draft.address = (previous.address if previous and previous.address
                                 else customer.address if customer else "")
                # El medio de pago de siempre ya va en el resumen (se puede cambiar).
                draft.payment = (previous.payment if previous and previous.payment
                                 else self._usual_payment(user))
            return self._next_step(user, draft, intro, note)
        except StorageError as exc:
            return self._storage_failure(user, exc, "armar el pedido")

    # ------------------------------------------------------------- dirección
    @staticmethod
    def _address_problem(address: str) -> str | None:
        if "?" in address:  # es una pregunta, no una dirección
            return ("Para seguir primero necesito la dirección de envío: calle, número y localidad. "
                    "Después te respondo lo que quieras 🙌\n(o escribí «cancelar» para descartar el pedido)")
        if len(address) < MIN_ADDRESS_LEN or not any(ch.isalpha() for ch in address):
            return ("Esa dirección parece incompleta 🤔. Mandame calle, número y localidad, por ejemplo:\n"
                    "<i>Av. Mate de Luna 1234, San Miguel de Tucumán</i>\n(o escribí «cancelar» para descartar el pedido)")
        if len(address) > MAX_ADDRESS_LEN:
            return f"La dirección es muy larga (máximo {MAX_ADDRESS_LEN} caracteres)."
        return None

    def _save_address(self, user, address: str) -> Customer:
        return self.repo.save_customer(Customer(user.channel, str(user.id), user.name, address.strip()),
                                       self.stamp())

    @staticmethod
    def _wants_cancel(text: str) -> bool:
        """«no», «cancelá», «no, dejá», «no tengo plata, cancelá», «mejor no»."""
        t = _norm(text)
        words = t.split()
        if t in NO or CANCEL_WORDS & set(words) or t.startswith(("no quiero", "mejor no")):
            return True
        return len(words) > 1 and words[0] == "no" and all(w in NO | {"gracias", "che", "mejor"} for w in words)

    @staticmethod
    def _wants_confirm(text: str) -> bool:
        """«sí», «dale», «perfecto, confirmalo» (sin ningún «no» de por medio)."""
        t = _norm(text)
        words = set(t.split())
        return t in YES or (bool(words & {"confirmalo", "confirma", "confirmo", "confirmar", "confirmame"})
                            and "no" not in words)

    def _looks_like_address(self, text: str) -> bool:
        """¿Este mensaje es una dirección? Se pide número de calle (o una palabra de dirección)
        y que no sea un cambio en el pedido («esperá, agregale 2 fantas»)."""
        words = _norm(text).split()
        if not words or "?" in text or words[0] in ORDER_VERBS:
            return False
        street = bool(STREET_WORDS & set(words)) or "s/n" in text.lower()
        if not (any(ch.isdigit() for ch in text) or street):
            return False
        if street:
            return True
        try:   # «2 fantas» tiene número, pero es un producto del catálogo
            items = rules_parser.parse_order(text, self._sellable(), self.s.rules.match_threshold,
                                             self.s.rules.max_qty_per_item)
        except StorageError:
            return True
        return not any(it.product is not None for it in items)

    def receive_address(self, user, address: str, draft: Draft) -> list:
        """El cliente respondió a «¿a qué dirección te lo enviamos?». Solo se toma como
        dirección si lo parece; si no (un cambio en el pedido, una pregunta), se atiende como
        cualquier mensaje y se le vuelve a pedir la dirección."""
        address = address.strip()
        if self._wants_cancel(address):
            return self.cancel_draft(user)
        if self._looks_like_address(address):
            problem = self._address_problem(address)
            if problem:
                return [Outgoing(user.id, problem)]
            return self._set_address(user, draft, address)
        if self.ai_client is not None or self.s.ai.usable:
            return self.converse(user, address, draft)   # Gemini contesta y vuelve a pedir la dirección
        if "?" in address:
            return [Outgoing(user.id, self._address_problem(address))]
        try:   # sin Gemini: si nombra productos, es un cambio en el pedido
            items = self._interpret(address, use_ai=False)
        except StorageError as exc:
            return self._storage_failure(user, exc, "leer el catálogo para un pedido")
        if any(it.product is not None for it in items):
            return self.start_order(user, address, items)
        return [Outgoing(user.id, ADDRESS_INCOMPLETE)]

    def _save_address_if_first(self, user, address: str) -> None:
        """La primera dirección del cliente se guarda sola; después, solo si él lo elige."""
        customer = self.repo.get_customer(*self._key(user))
        if customer is None or not customer.address:
            self._save_address(user, address)

    def _set_address(self, user, draft: Draft, address: str) -> list:
        """Pone la dirección en el borrador y muestra el resumen."""
        try:
            self._save_address_if_first(user, address)
            draft.address, draft.waiting = address, WAIT_CONFIRM
            if draft.items is None:  # borrador viejo (solo texto): se interpreta ahora
                self._clear_draft(user)
                return self.start_order(user, draft.text, self._interpret(draft.text))
            note = f"📍 Listo, este pedido va a <b>{esc(address)}</b>."
            return self._next_step(user, draft, note=note)
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
                                  "(o escribí «cancelar» para descartar)")]

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
        if draft.pending:   # con una duda abierta no se confirma: a una empresa le falta un renglón
            return self._pending_question(user, draft)
        if draft.waiting == WAIT_STOCK:  # «sí» a «¿lo ajusto a lo que hay?»
            return self.adjust_to_stock(user)
        if draft.modifies:
            return self._apply_modification(user, draft)
        if draft.waiting == WAIT_PAYMENT:
            return self._ask_payment(user, draft)
        try:
            # Antes de preguntar cómo paga, se vuelve a mirar el stock (otro cliente pudo llevárselo).
            pairs, _ = self._resolve(draft, self._products_for(draft))
            if self._shortages(pairs):
                return self._next_step(user, draft, note="Uy, justo se vendió parte del stock 😕")
            if draft.payment:   # el medio ya está en el resumen: confirmar = pedido hecho
                return self._place_order(user, draft)
            draft.waiting = WAIT_PAYMENT
            self._save_draft(user, draft)
        except StorageError as exc:
            return self._storage_failure(user, exc, "confirmar el pedido")
        return self._ask_payment(user, draft)

    # ------------------------------------------------------------- pago
    def _ask_payment(self, user, draft: Draft) -> list:
        t = draft.token
        return [Outgoing(user.id, "💳 ¿Cómo lo vas a pagar?\n"
                                  "• <b>Efectivo</b> cuando te llega el pedido.\n"
                                  "• <b>Transferencia</b>: te paso el alias y lo confirmamos cuando se acredite.",
                         [("💵 Efectivo al recibir", f"pe:{t}"), ("🏦 Transferencia", f"pt:{t}"),
                          ("❌ Cancelar", f"cx:{t}")])]

    def choose_payment(self, user, method: str, token: str | None = None) -> list:
        try:
            draft = self._get_draft(user)
        except StorageError as exc:
            return self._storage_failure(user, exc, "elegir el medio de pago")
        if draft is None or (token and token != draft.token) or draft.waiting != WAIT_PAYMENT:
            return self._stale(user)
        draft.payment = method
        return self._place_order(user, draft)

    @staticmethod
    def _payment_choice(text: str) -> str | None:
        """Medio de pago que eligió, solo si es uno y sin dudas. «efectivo no, transferencia»
        → transferencia; «no tengo plata» o «no sé» → ninguno (se vuelve a preguntar)."""
        words = _norm(text).split()
        all_keys = set().union(*PAYMENT_WORDS.values())

        def negated(i: int) -> bool:
            if i + 1 < len(words) and words[i + 1] == "no":          # «efectivo no»
                return True
            for j in (i - 1, i - 2):                                 # «no en efectivo», «sin efectivo»
                if j < 0:
                    break
                if words[j] in all_keys:                             # el «no» era de otro medio
                    return False
                if words[j] == "sin" or (words[j] == "no" and not (j > 0 and words[j - 1] in all_keys)):
                    return True
            return False

        chosen = {method for i, w in enumerate(words) for method, keys in PAYMENT_WORDS.items()
                  if w in keys and not negated(i)}
        return chosen.pop() if len(chosen) == 1 else None

    def answer_payment(self, user, text: str, draft: Draft) -> list:
        """Respuesta escrita a «¿cómo lo vas a pagar?». Ante cualquier duda se vuelve a
        preguntar: elegir un medio registra el pedido."""
        if self._wants_cancel(text):
            return self.cancel_draft(user)
        method = self._payment_choice(text)
        if method:
            return self.choose_payment(user, method)
        t = draft.token
        return [Outgoing(user.id, "Para cerrar el pedido decime cómo lo pagás: <b>efectivo</b> al recibir o "
                                  "<b>transferencia</b>. Si no lo querés, escribí «cancelar».",
                         [("💵 Efectivo al recibir", f"pe:{t}"), ("🏦 Transferencia", f"pt:{t}"),
                          ("❌ Cancelar", f"cx:{t}")])]

    def _payment_text(self, order: Order) -> str:
        """Qué tiene que hacer el cliente para pagar (sin inventar nada: sale de la config)."""
        if order.payment_method == "transferencia":
            b = self.s.business
            return (f"\n\n🏦 <b>Transferí {self.money(order.total)}</b> al alias <code>{esc(b.transfer_alias)}</code>"
                    f", a nombre de {esc(b.transfer_holder)}. Cuando se acredite te aviso por acá.\n"
                    f"Si no recibimos el pago en {self.s.rules.payment_hours} h, el pedido se cancela.")
        return "\n\n💵 Lo pagás en efectivo cuando te llega. Cuando lo cobremos te aviso por acá."

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
        self._event(user, "pedido_cancelado", detail="modificacion" if draft.modifies else draft.waiting)
        if draft.modifies:
            return [Outgoing(user.id, f"❌ Listo, tu pedido <code>{draft.modifies}</code> queda como estaba.")]
        return [Outgoing(user.id, "❌ Listo, descarté ese pedido. Cuando quieras, mandame uno nuevo.")]

    # ------------------------------------------------------- registrar el pedido
    def _place_order(self, user, draft: Draft) -> list:
        if draft.pending:   # red de seguridad: nunca se registra con una duda abierta
            return self._pending_question(user, draft)
        with self._lock:
            try:
                products = {p.sku.upper(): p for p in self.repo.list_products()}
            except StorageError as exc:
                return self._storage_failure(user, exc, "leer el stock para un pedido")
            pairs, unknown = self._resolve(draft, products)
            if not pairs and not unknown:
                return [Outgoing(user.id, NO_ORDER_MSG)]
            sold_out = bool(self._shortages(pairs))
            if not sold_out:
                ev = evaluate(pairs, self.s.rules, self.today(), unknown, self._is_new_customer(user))
                now = self.stamp()
                order = Order(order_id=self._new_order_id(), created_at=now, customer_id=user.id,
                              customer_name=user.name, original_text=draft.text, lines=ev.lines,
                              status=ev.status, reason="; ".join(ev.reasons), updated_at=now,
                              address=draft.address, shipping_cost=COSTO_ENVIO, channel=user.channel,
                          payment_method=draft.payment or "efectivo", payment_status=PAGO_PENDIENTE)
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

        if sold_out:  # entre el resumen y la confirmación se vendió parte del stock
            try:
                return self._next_step(user, draft, note="Uy, justo se vendió parte del stock 😕")
            except StorageError as exc:
                return self._storage_failure(user, exc, "armar el pedido")

        self._event(user, "pedido_registrado", detail=order.status.lower())
        tail = f"\n\n✏️ Si necesitás cambiar algo, avisame en los próximos {self.s.rules.modify_minutes} minutos."
        if order.status == ESTADO_CONFIRMADO:
            out = [Outgoing(user.id, self.format_order(order, "✅ <b>¡Pedido completado!</b> Queda pendiente de pago.")
                            + self._payment_text(order) + tail)]
        else:
            out = [Outgoing(user.id, self.format_order(order, "🟡 <b>Recibimos tu pedido</b>")
                            + "\n\nQuedó <b>en revisión</b>; te aviso apenas el área administrativa lo resuelva"
                            " y ahí te digo cómo pagarlo." + tail)]
            self._notify_review(order, f"Pedido {order.order_id} en revisión")
        return out + self._ask_which_address(user, order.address)

    def _notify_review(self, order: Order, title: str) -> None:
        """Aviso en la web: el admin revisa los pedidos ahí, no en el chat."""
        self.notify_admins("revision", title, order.reason, order.order_id)

    # ------------------------------------------------------------ modificar
    def _modifiable_order(self, user) -> Order | None:
        """Último pedido del cliente que todavía se puede modificar."""
        mine = [o for o in self.repo.list_orders()
                if o.customer_id == user.id and o.channel == user.channel]
        if not mine:
            return None
        last = mine[-1]
        if last.status not in (ESTADO_CONFIRMADO, ESTADO_REVISION) or last.is_paid:
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
            if text:
                self.notify_admins("fuera_de_termino", f"{user.name} pide un cambio fuera de término",
                                   f"«{text}»")
            return out
        draft = Draft(text=order.original_text, items=[[l.sku, l.qty] for l in order.lines],
                      address=order.address, modifies=order.order_id)
        if items:
            problems = self._clarifications(items)
            if problems:
                return [Outgoing(user.id, "No pude armar el cambio:\n• " + "\n• ".join(problems))]
            draft.items = self._merge(items)
            draft.unknown = [it.unknown_name for it in items if it.error == "no_existe"]
        if address:
            problem = self._address_problem(address)
            if problem:
                return [Outgoing(user.id, problem)]
        try:
            if address:
                draft.address = address
            return self._next_step(user, draft, intro,
                                   note="" if (items or address) else
                                   "Decime qué querés cambiar (por ejemplo «que sean 3 cocas» o «sacá la fanta»), "
                                   "o tocá 📍 para cambiar la dirección.")
        except StorageError as exc:
            return self._storage_failure(user, exc, "modificar tu pedido")

    def _apply_modification(self, user, draft: Draft) -> list:
        sold_out = False
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
                    if self._shortages(pairs):
                        sold_out = True
                        raise _SoldOut
                    ev = evaluate(pairs, self.s.rules, self.today(), unknown,
                                  self._is_new_customer(user, exclude=order.order_id))
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
            except _SoldOut:
                pass
            except StorageError as exc:
                return self._storage_failure(user, exc, "modificar tu pedido")
        if sold_out:
            try:
                return self._next_step(user, draft, note="Uy, justo se vendió parte del stock 😕")
            except StorageError as exc:
                return self._storage_failure(user, exc, "modificar tu pedido")

        if order.status == ESTADO_CONFIRMADO:
            out = [Outgoing(user.id, self.format_order(order, "✏️ <b>¡Pedido modificado y confirmado!</b>"))]
            items = ", ".join(f"{l.qty} × {l.name}" for l in order.lines)
            self.notify_admins("modificado", f"Pedido {order.order_id} modificado por {user.name}",
                               f"Quedó: {items}. Dirección: {order.address}.", order.order_id)
        else:
            out = [Outgoing(user.id, self.format_order(order, "✏️ <b>Modificamos tu pedido</b>")
                            + "\n\nQuedó <b>en revisión</b>; te aviso apenas el área administrativa lo resuelva.")]
            self._notify_review(order, f"Pedido {order.order_id} modificado: quedó en revisión")
        return out + self._ask_which_address(user, order.address)

    # ------------------------------------------------- ¿qué dirección guardo?
    def _ask_which_address(self, user, used: str) -> list:
        """Si el pedido fue a otra dirección que la guardada, pregunta cuál dejar para la próxima."""
        try:
            customer = self.repo.get_customer(*self._key(user))
            if customer is None or not customer.address:
                self._save_address(user, used)
                return []
            if customer.address.strip().lower() == used.strip().lower():
                return []
            question = Draft(text="", items=[], address=used, waiting=WAIT_SAVE)
            self._save_draft(user, question)
        except StorageError as exc:  # el pedido ya está hecho: esto no puede romperlo
            log.warning("No pude preguntar qué dirección guardar: %s", exc)
            return []
        t = question.token
        return [Outgoing(user.id, "🏠 ¿Qué dirección dejo guardada para tus próximos pedidos?\n"
                                  f"• La de siempre: <b>{esc(customer.address)}</b>\n"
                                  f"• La nueva: <b>{esc(used)}</b>",
                         [("🏠 La de siempre", f"dk:{t}"), ("📍 La nueva", f"dn:{t}")])]

    def choose_address(self, user, keep_old: bool, token: str | None = None) -> list:
        try:
            question = self._get_draft(user, include_save_question=True)
            if question is None or question.waiting != WAIT_SAVE or (token and token != question.token):
                return [Outgoing(user.id, "Esa pregunta ya no está vigente. Si querés cambiar tu dirección "
                                          "guardada, escribime «cambiar dirección» y la nueva.")]
            self._clear_draft(user)
            if keep_old:
                customer = self.repo.get_customer(*self._key(user))
                return [Outgoing(user.id, f"🏠 Dale, sigo usando <b>{esc(customer.address)}</b> para tus "
                                          "próximos pedidos.")]
            self._save_address(user, question.address)
        except StorageError as exc:
            return self._storage_failure(user, exc, "guardar la dirección")
        return [Outgoing(user.id, f"📍 Listo, tus próximos pedidos van a <b>{esc(question.address)}</b>.")]

    def answer_which_address(self, user, text: str, question: Draft) -> list:
        """Respuesta escrita a «¿qué dirección dejo guardada?». Si no es una respuesta
        clara, se deja la de siempre y el mensaje se atiende como cualquier otro."""
        words = set(_norm(text).split())
        if words & {"nueva", "ultima", "esta", "guardala", "cambiala"}:
            return self.choose_address(user, keep_old=False)
        if words & {"siempre", "anterior", "vieja", "misma", "otra"} or _norm(text) in NO:
            return self.choose_address(user, keep_old=True)
        try:
            self._clear_draft(user)
        except StorageError as exc:
            return self._storage_failure(user, exc, "leer tu mensaje")
        return self.converse(user, text, None)

    # ---------------------------------------------------------- conversación
    def _interpret(self, text: str, catalog: list[Product] | None = None, use_ai: bool = True):
        catalog = catalog if catalog is not None else self._sellable()
        r = self.s.rules
        if use_ai:
            return parse_order(text, catalog, r.match_threshold, r.max_qty_per_item,
                               config=self.s.ai, client=self.ai_client)
        return rules_parser.parse_order(text, catalog, r.match_threshold, r.max_qty_per_item)

    def converse(self, user, text: str, draft: Draft | None = None, on_wait=None) -> list:
        """Gemini entiende el mensaje en contexto y redacta; los datos los pone el bot.
        Si Gemini (los dos modelos) no contesta, se le pide al cliente que espere y se
        reintenta; si sigue sin contestar, se le pide que escriba más tarde."""
        if self.ai_client is None and not self.s.ai.usable:
            return self._converse_rules(user, text, draft)
        if draft is not None and draft.waiting != WAIT_ADDRESS:   # «sí», «no», el pago o una dirección
            quick = self._quick_answer(user, text, draft)            # no necesitan a Gemini
            if quick is not None:
                return quick
        try:
            catalog = self._sellable()   # lo agotado no se le muestra a Gemini: no lo puede ofrecer
            history = self._history(user)
            orders = self._orders_summary(user)
            draft_txt = self._draft_for_ai(draft)
        except StorageError as exc:
            return self._storage_failure(user, exc, "leer el catálogo para un pedido")
        r = self.s.rules
        def ask():
            return conversar(text, catalog, historial=history, pedidos=orders, borrador=draft_txt,
                             negocio=self.s.business.as_text(), nombre_negocio=self.s.business.name,
                             max_qty=r.max_qty_per_item, config=self.s.ai, client=self.ai_client)

        def timed_ask():
            t0 = time.monotonic()
            result = ask()
            self._event(user, "ia", int((time.monotonic() - t0) * 1000),
                        result.intencion if result is not None else "sin_respuesta")
            return result

        reply = timed_ask()
        if reply is None and self.s.ai_retry_delays:
            if on_wait is not None:
                try:
                    on_wait(Outgoing(user.id, WAIT_AI_MSG, channel=user.channel))
                except Exception:  # no poder avisar no frena el reintento
                    log.exception("No pude mandar el aviso de espera")
            for delay in self.s.ai_retry_delays:
                time.sleep(delay)
                reply = timed_ask()
                if reply is not None:
                    break
        if reply is None:
            log.error("Gemini no respondió tras %d reintentos", len(self.s.ai_retry_delays))
            self._event(user, "ia_caida")
            self.notify_admins("falla", "Gemini no responde",
                               f"No se pudo atender un mensaje de {user.name}: «{text[:200]}». "
                               "Se le pidió que escriba de nuevo en unos minutos.")
            return [Outgoing(user.id, AI_DOWN_MSG)]

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
            facts = []
            for sku in reply.consulta_skus:
                p = by_sku[sku]
                price = self.money(p.price) if p.has_price() else "precio a confirmar"
                avail = "✅ disponible" if p.stock > 0 else "❌ sin stock por ahora"
                facts.append(f"• {esc(p.name)} — {price} {avail}")
            head = esc(reply.respuesta) if reply.respuesta else "Esto es lo que tengo:"
            msg = head + "\n\n" + "\n".join(facts) + "\n\n🚚 Envío incluido, sin costo."
        elif intent == "consulta":
            msg = esc(reply.respuesta) if reply.respuesta else "Buena pregunta 🤔 Lo consulto con el equipo y te aviso."
        else:  # charla, o confirmar/cancelar sin borrador
            msg = esc(reply.respuesta) if reply.respuesta else NO_ORDER_MSG
        if _promises_follow_up(msg):
            # El bot prometió consultarlo: que alguien del equipo se entere.
            self.notify_admins("consulta", f"Consulta sin responder de {user.name}",
                               f"{user.channel}: «{text[:200]}». El bot le dijo que lo consultaba con el equipo.")
        if draft is not None and draft.waiting == WAIT_ADDRESS:
            msg += "\n\n📍 Para seguir con tu pedido me falta la dirección: calle, número y localidad."
        elif draft is not None and draft.waiting == WAIT_CONFIRM:
            msg += "\n\n🧾 Tu pedido sigue esperando confirmación: respondé «sí» o tocá ✅ en el resumen."
        elif draft is not None and draft.waiting == WAIT_STOCK:
            msg += "\n\n🧾 ¿Te ajusto el pedido a lo que hay? Respondé «sí», o mandame la lista como la querés."
        return [Outgoing(user.id, msg)]

    def _converse_rules(self, user, text: str, draft: Draft | None, catalog=None) -> list:
        """Sin Gemini: «sí»/«no» confirman o cancelan; lo demás se interpreta por reglas."""
        if draft is not None:
            quick = self._quick_answer(user, text, draft)
            if quick is not None:
                return quick
        try:
            items = self._interpret(text, catalog, use_ai=catalog is None)
        except StorageError as exc:
            return self._storage_failure(user, exc, "leer el catálogo para un pedido")
        if draft is not None and not items:
            if draft.waiting == WAIT_ADDRESS:
                return [Outgoing(user.id, ADDRESS_INCOMPLETE)]
            if draft.waiting == WAIT_STOCK:
                return [Outgoing(user.id, "¿Te ajusto el pedido a lo que hay? Respondé «sí» para ajustarlo o "
                                          "«no» para cancelarlo, o mandame la lista completa como la querés.")]
            return [Outgoing(user.id, "¿Confirmo tu pedido? Respondé «sí» para confirmarlo o «no» para "
                                      "cancelarlo, o mandame la lista completa como la querés.")]
        return self.start_order(user, text, items)
