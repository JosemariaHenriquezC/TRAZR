"""
Regla de "pedido dentro de los parámetros" (ver también config.Rules y README).

evaluate() recibe los renglones ya interpretados y devuelve:
  - el estado (CONFIRMADO o EN REVISION),
  - los motivos por los que no se pudo confirmar solo,
  - los renglones con su precio actual.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .config import Rules
from .models import ESTADO_CONFIRMADO, ESTADO_REVISION, OrderLine, Product


@dataclass
class Evaluation:
    status: str
    lines: list[OrderLine]
    reasons: list[str] = field(default_factory=list)
    shortages: dict[str, float] = field(default_factory=dict)  # sku -> unidades faltantes

    @property
    def total(self) -> float:
        return round(sum(l.subtotal for l in self.lines), 2)


def merge_quantities(pairs: list[tuple[Product, int]]) -> list[tuple[Product, int]]:
    """Si el cliente nombra dos veces el mismo producto, se suman las cantidades."""
    merged: dict[str, list] = {}
    for product, qty in pairs:
        key = product.sku.upper()
        if key in merged:
            merged[key][1] += qty
        else:
            merged[key] = [product, qty]
    return [(p, q) for p, q in merged.values()]


def evaluate(pairs: list[tuple[Product, int]], rules: Rules, today: date,
             unknown_names: list[str] | None = None) -> Evaluation:
    reasons: list[str] = []
    shortages: dict[str, float] = {}
    lines: list[OrderLine] = []

    for product, qty in merge_quantities(pairs):
        lines.append(OrderLine(sku=product.sku, name=product.name, qty=qty,
                               unit_price=float(product.price or 0)))
        if not product.has_price():
            reasons.append(f"Precio no cargado: {product.name} ({product.sku})")
        if rules.require_stock and product.stock < qty:
            missing = qty - max(product.stock, 0)
            shortages[product.sku] = missing
            reasons.append(
                f"Stock insuficiente: {product.name} (pedido {qty}, disponible {_fmt_qty(product.stock)})"
            )

    for name in unknown_names or []:
        reasons.append(f"Producto no encontrado: «{name}»")

    total = round(sum(l.subtotal for l in lines), 2)
    if rules.max_order_total and total > rules.max_order_total:
        reasons.append(f"El total ({_ars(total)}) supera el máximo automático ({_ars(rules.max_order_total)})")

    if not lines:
        reasons.append("El pedido no tiene productos reconocidos")

    status = ESTADO_REVISION if reasons else ESTADO_CONFIRMADO
    return Evaluation(status=status, lines=lines, reasons=reasons, shortages=shortages)


def _ars(value: float) -> str:
    """$ 56.400,00 (formato argentino)."""
    return "$\u00a0" + f"{value:,.2f}".replace(",", "§").replace(".", ",").replace("§", ".")


def _fmt_qty(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:g}"
