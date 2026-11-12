"""
Interfaz común de almacenamiento + utilidades de conversión de valores.

La implementación es SQLiteRepository (app/storage/sqlite.py). El resto del
bot solo conoce esta interfaz, así que el día de mañana se puede cambiar de
motor (por ejemplo a PostgreSQL) sin tocar service.py.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date, datetime, timedelta

from ..models import Admin, Customer, Notice, Order, OrderLine, Product


class StorageError(Exception):
    """No se pudo leer o escribir en la base de datos."""


class Repository(ABC):
    # ---- productos ----
    @abstractmethod
    def list_products(self) -> list[Product]: ...

    @abstractmethod
    def get_product(self, sku: str) -> Product | None: ...

    @abstractmethod
    def upsert_product(self, product: Product, stamp: str) -> None:
        """Da de alta un producto o reemplaza sus datos si el SKU ya existe."""

    @abstractmethod
    def set_stock(self, sku: str, new_stock: float, stamp: str) -> Product: ...

    @abstractmethod
    def apply_stock_deltas(self, deltas: dict[str, float], stamp: str) -> None:
        """Suma (o resta, si es negativo) cantidades al stock de varios SKU en una sola operación."""

    @abstractmethod
    def set_price(self, sku: str, price: float, stamp: str) -> Product: ...

    # ---- clientes ----
    @abstractmethod
    def get_customer(self, channel: str, external_id: str) -> Customer | None: ...

    @abstractmethod
    def list_customers(self) -> list[Customer]: ...

    @abstractmethod
    def save_customer(self, customer: Customer, stamp: str) -> Customer:
        """Alta o actualización (nombre y dirección) del cliente."""

    # ---- pedido esperando la dirección del cliente ----
    @abstractmethod
    def set_pending_order(self, channel: str, external_id: str, text: str, stamp: str) -> None: ...

    @abstractmethod
    def get_pending_order(self, channel: str, external_id: str) -> tuple[str, str] | None:
        """Devuelve (mensaje, fecha) del pedido retenido, o None."""

    @abstractmethod
    def clear_pending_order(self, channel: str, external_id: str) -> None: ...

    # ---- avisos para el admin (web) ----
    @abstractmethod
    def add_notice(self, kind: str, title: str, detail: str, order_id: str, stamp: str) -> None: ...

    @abstractmethod
    def list_notices(self, limit: int = 50, unread_only: bool = False) -> list[Notice]:
        """Del más nuevo al más viejo."""

    @abstractmethod
    def mark_notices_read(self, ids: list[int] | None = None) -> None:
        """Marca como leídos esos avisos (None = todos)."""

    # ---- admins de la web ----
    @abstractmethod
    def get_admin(self, username: str) -> Admin | None: ...

    @abstractmethod
    def list_admins(self) -> list[Admin]: ...

    @abstractmethod
    def save_admin(self, admin: Admin, stamp: str) -> None:
        """Alta o actualización (por username)."""

    # ---- pedidos ----
    @abstractmethod
    def add_order(self, order: Order) -> None: ...

    @abstractmethod
    def list_orders(self) -> list[Order]:
        """Todos los pedidos, del más viejo al más nuevo."""

    @abstractmethod
    def get_order(self, order_id: str) -> Order | None: ...

    @abstractmethod
    def update_order(self, order: Order) -> Order:
        """Reescribe un pedido existente: renglones, total, dirección, estado y motivo
        (se usa cuando el cliente modifica su pedido)."""

    @abstractmethod
    def update_order_status(self, order_id: str, status: str, reason: str,
                            reviewed_by: str, stamp: str,
                            lines: list[OrderLine] | None = None) -> Order:
        """Cambia el estado. Si se pasan `lines`, reescribe también los renglones y el total
        (se usa cuando el admin aprueba con precios actualizados)."""


# ---------------------------------------------------------------------------
# Conversión de valores escritos por personas (comandos del admin, CSV).
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
        # Número de serie de fecha de planillas (días desde 1899-12-30)
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


__all__ = ["Repository", "StorageError", "to_number", "to_date", "to_bool"]
