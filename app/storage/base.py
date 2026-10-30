"""
Interfaz común de almacenamiento + utilidades para convertir filas <-> objetos.

Hay dos implementaciones:
  - GoogleSheetsRepository (producción): dos hojas de cálculo de Google separadas.
  - ExcelRepository (pruebas locales sin credenciales): dos archivos .xlsx.
Ambas usan exactamente las mismas columnas, así el resto del bot no sabe
(ni le importa) dónde están guardados los datos.
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from datetime import date, datetime, timedelta

from ..models import ORDER_COLUMNS, STOCK_COLUMNS, Order, OrderLine, Product


class StorageError(Exception):
    """No se pudo leer o escribir una hoja (red, permisos, hoja mal armada...)."""


class Repository(ABC):
    @abstractmethod
    def list_products(self) -> list[Product]: ...

    @abstractmethod
    def set_stock(self, sku: str, new_stock: float, stamp: str) -> Product: ...

    @abstractmethod
    def apply_stock_deltas(self, deltas: dict[str, float], stamp: str) -> None:
        """Suma (o resta, si es negativo) cantidades al stock de varios SKU en una sola operación."""

    @abstractmethod
    def set_price(self, sku: str, price: float, valid_until: date | None, stamp: str) -> Product: ...

    @abstractmethod
    def add_order(self, order: Order) -> None: ...

    @abstractmethod
    def list_orders(self) -> list[Order]: ...

    @abstractmethod
    def update_order_status(self, order_id: str, status: str, reason: str,
                            reviewed_by: str, stamp: str,
                            lines: list[OrderLine] | None = None) -> Order:
        """Cambia el estado. Si se pasan `lines`, reescribe también Detalle, Items_JSON y Total
        (se usa cuando el admin aprueba con precios actualizados)."""

    # Helpers que usan los métodos anteriores
    def get_product(self, sku: str) -> Product | None:
        sku = sku.strip().upper()
        return next((p for p in self.list_products() if p.sku.upper() == sku), None)

    def get_order(self, order_id: str) -> Order | None:
        order_id = order_id.strip().upper()
        return next((o for o in self.list_orders() if o.order_id.upper() == order_id), None)


# ---------------------------------------------------------------------------
# Conversión de valores de celda. Las hojas pueden devolver números como
# texto ("1.234,50"), fechas como texto o como fecha, booleanos como "SI", etc.
# ---------------------------------------------------------------------------

def to_number(value) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace("$", "").replace(" ", "")
    if not text:
        return None
    # "1.234,50" (formato es-AR) → 1234.50 ; "1234.5" → 1234.5 ; "1,5" → 1.5
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".")
    elif "," in text:
        text = text.replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def to_date(value) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)):
        # Número de serie de fecha de hojas de cálculo (días desde 1899-12-30)
        return date(1899, 12, 30) + timedelta(days=int(value))
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"fecha no reconocida: {text!r}")


def to_bool(value, default: bool = True) -> bool:
    if value is None or str(value).strip() == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"si", "sí", "s", "true", "1", "yes", "y", "x", "verdadero"}


def check_header(header: list, expected: list[str], sheet_name: str) -> dict[str, int]:
    """Valida que estén todas las columnas y devuelve {columna: índice}."""
    normalized = [str(h).strip() for h in header]
    missing = [c for c in expected if c not in normalized]
    if missing:
        raise StorageError(
            f"La hoja '{sheet_name}' no tiene las columnas: {', '.join(missing)}. "
            f"Encabezado esperado (fila 1): {' | '.join(expected)}"
        )
    return {c: normalized.index(c) for c in expected}


def row_to_product(values: list, idx: dict[str, int], row_number: int) -> Product | None:
    def cell(col):
        i = idx[col]
        return values[i] if i < len(values) else ""

    sku = str(cell("SKU")).strip()
    name = str(cell("Producto")).strip()
    if not sku or not name:
        return None  # fila vacía
    try:
        valid_until = to_date(cell("Vigente_hasta"))
    except ValueError:
        valid_until = date.min  # fecha ilegible → se trata como precio NO vigente
    aliases = [a.strip() for a in str(cell("Alias") or "").split(",") if a.strip()]
    return Product(
        sku=sku,
        name=name,
        aliases=aliases,
        stock=to_number(cell("Stock")) or 0.0,
        price=to_number(cell("Precio")) or 0.0,
        valid_until=valid_until,
        active=to_bool(cell("Activo"), default=True),
        row=row_number,
    )


def order_to_row(order: Order) -> list:
    detail = "; ".join(f"{l.qty} x {l.name} ({l.sku}) @ {l.unit_price:.2f} = {l.subtotal:.2f}"
                       for l in order.lines)
    items_json = json.dumps([l.to_dict() for l in order.lines], ensure_ascii=False)
    values = {
        "Pedido_ID": order.order_id,
        "Fecha": order.created_at,
        "Cliente_ID": str(order.customer_id),
        "Cliente_nombre": order.customer_name,
        "Mensaje_original": order.original_text,
        "Detalle": detail,
        "Items_JSON": items_json,
        "Total": order.total,
        "Estado": order.status,
        "Motivo": order.reason,
        "Actualizado": order.updated_at or order.created_at,
        "Revisado_por": order.reviewed_by,
    }
    return [values[c] for c in ORDER_COLUMNS]


def row_to_order(values: list, idx: dict[str, int], row_number: int) -> Order | None:
    def cell(col):
        i = idx[col]
        return values[i] if i < len(values) else ""

    order_id = str(cell("Pedido_ID")).strip()
    if not order_id:
        return None
    try:
        lines = [OrderLine.from_dict(d) for d in json.loads(str(cell("Items_JSON")) or "[]")]
    except (ValueError, KeyError, TypeError):
        lines = []
    try:
        customer_id = int(float(str(cell("Cliente_ID")).strip() or 0))
    except ValueError:
        customer_id = 0
    return Order(
        order_id=order_id,
        created_at=str(cell("Fecha")),
        customer_id=customer_id,
        customer_name=str(cell("Cliente_nombre")),
        original_text=str(cell("Mensaje_original")),
        lines=lines,
        status=str(cell("Estado")).strip().upper(),
        reason=str(cell("Motivo")),
        updated_at=str(cell("Actualizado")),
        reviewed_by=str(cell("Revisado_por")),
        row=row_number,
    )


__all__ = [
    "Repository", "StorageError", "STOCK_COLUMNS", "ORDER_COLUMNS",
    "to_number", "to_date", "to_bool", "check_header",
    "row_to_product", "order_to_row", "row_to_order",
]
