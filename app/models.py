"""Estructuras de datos compartidas por todo el bot."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

# Estados posibles de un pedido en la hoja de pedidos.
ESTADO_CONFIRMADO = "CONFIRMADO"
ESTADO_REVISION = "EN REVISION"
ESTADO_RECHAZADO = "RECHAZADO"

# Columnas de la hoja de stock (en este orden; la primera fila es el encabezado).
STOCK_COLUMNS = ["SKU", "Producto", "Alias", "Stock", "Precio", "Vigente_hasta", "Activo", "Actualizado"]

# Columnas de la hoja de pedidos.
ORDER_COLUMNS = [
    "Pedido_ID", "Fecha", "Cliente_ID", "Cliente_nombre", "Mensaje_original",
    "Detalle", "Items_JSON", "Total", "Estado", "Motivo", "Actualizado", "Revisado_por",
]


@dataclass
class Product:
    sku: str
    name: str
    aliases: list[str]
    stock: float
    price: float
    valid_until: date | None
    active: bool
    row: int = 0  # número de fila en la hoja (para actualizar)

    def price_is_valid(self, today: date) -> bool:
        if self.price is None or self.price <= 0:
            return False
        return self.valid_until is None or self.valid_until >= today


@dataclass
class OrderLine:
    sku: str
    name: str
    qty: int
    unit_price: float

    @property
    def subtotal(self) -> float:
        return round(self.qty * self.unit_price, 2)

    def to_dict(self) -> dict:
        return {"sku": self.sku, "producto": self.name, "cantidad": self.qty,
                "precio_unitario": self.unit_price, "subtotal": self.subtotal}

    @classmethod
    def from_dict(cls, d: dict) -> "OrderLine":
        return cls(sku=str(d["sku"]), name=str(d["producto"]), qty=int(d["cantidad"]),
                   unit_price=float(d["precio_unitario"]))


@dataclass
class Order:
    order_id: str
    created_at: str           # texto "YYYY-MM-DD HH:MM:SS" en la zona horaria configurada
    customer_id: int
    customer_name: str
    original_text: str
    lines: list[OrderLine]
    status: str
    reason: str = ""
    updated_at: str = ""
    reviewed_by: str = ""
    row: int = 0

    @property
    def total(self) -> float:
        return round(sum(l.subtotal for l in self.lines), 2)


@dataclass
class ParsedItem:
    """Un renglón interpretado del mensaje del cliente."""
    raw: str
    qty: int | None = None
    product: Product | None = None
    error: str | None = None             # "sin_cantidad" | "cantidad_invalida" | "no_existe" | "ambiguo"
    candidates: list[Product] = field(default_factory=list)
    unknown_name: str = ""
