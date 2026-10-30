"""
Almacenamiento en Google Sheets (producción).

Usa una cuenta de servicio de Google (archivo JSON) y la librería gspread.
Las dos hojas de cálculo (stock y pedidos) son archivos SEPARADOS; cada una
se identifica por su ID (el código largo de la URL) y se debe compartir con
el email de la cuenta de servicio como "Editor".
"""
from __future__ import annotations

import logging
import time
from datetime import date
from typing import Callable, TypeVar

import gspread
from gspread.utils import DateTimeOption, ValueInputOption, ValueRenderOption, rowcol_to_a1

from ..models import ORDER_COLUMNS, STOCK_COLUMNS, Order, Product
from .base import (Repository, StorageError, check_header, order_to_row,
                   row_to_order, row_to_product)

log = logging.getLogger(__name__)
T = TypeVar("T")


def _friendly(exc: Exception) -> str:
    """Traduce errores de Google a mensajes entendibles."""
    if isinstance(exc, gspread.exceptions.SpreadsheetNotFound):
        return ("No encuentro la hoja de cálculo. Revisá el ID en el .env y que esté "
                "compartida con el email de la cuenta de servicio.")
    if isinstance(exc, gspread.exceptions.WorksheetNotFound):
        return f"No existe la pestaña indicada: {exc}"
    if isinstance(exc, gspread.exceptions.APIError):
        code = getattr(exc.response, "status_code", "?")
        if code == 403:
            return "Sin permiso sobre la hoja: compartila con la cuenta de servicio como Editor."
        if code == 404:
            return "La hoja no existe o el ID es incorrecto."
        if code == 429:
            return "Google limitó la cantidad de pedidos por minuto; reintentá en unos segundos."
        return f"Error de la API de Google Sheets (HTTP {code})."
    return f"Error de acceso a la hoja: {exc.__class__.__name__}: {exc}"


class GoogleSheetsRepository(Repository):
    RETRIES = 3

    def __init__(self, credentials_file: str, stock_id: str, orders_id: str,
                 stock_tab: str = "Stock", orders_tab: str = "Pedidos"):
        self._creds = credentials_file
        self._stock_id, self._orders_id = stock_id, orders_id
        self._stock_tab, self._orders_tab = stock_tab, orders_tab
        self._client: gspread.Client | None = None
        self._stock_ws = None
        self._orders_ws = None

    # ---------------- conexión y reintentos ----------------
    def _call(self, fn: Callable[[], T]) -> T:
        """Ejecuta una llamada a Google reintentando ante errores transitorios."""
        last: Exception | None = None
        for attempt in range(1, self.RETRIES + 1):
            try:
                return fn()
            except gspread.exceptions.APIError as exc:
                code = getattr(exc.response, "status_code", 0)
                last = exc
                if code in (429, 500, 502, 503, 504) and attempt < self.RETRIES:
                    time.sleep(1.5 * attempt)
                    continue
                break
            except StorageError:
                raise
            except Exception as exc:  # red caída, credenciales inválidas, etc.
                last = exc
                if attempt < self.RETRIES:
                    time.sleep(1.0 * attempt)
                    continue
                break
        # Se resetea la conexión para que el próximo intento la recree.
        self._client = self._stock_ws = self._orders_ws = None
        log.exception("Fallo de Google Sheets", exc_info=last)
        raise StorageError(_friendly(last)) from last

    def _ws(self, which: str):
        if self._client is None:
            self._client = gspread.service_account(filename=self._creds)
        if which == "stock":
            if self._stock_ws is None:
                self._stock_ws = self._client.open_by_key(self._stock_id).worksheet(self._stock_tab)
            return self._stock_ws
        if self._orders_ws is None:
            self._orders_ws = self._client.open_by_key(self._orders_id).worksheet(self._orders_tab)
        return self._orders_ws

    def _read(self, which: str) -> list[list]:
        return self._call(lambda: self._ws(which).get(
            value_render_option=ValueRenderOption.unformatted,
            date_time_render_option=DateTimeOption.formatted_string,
            pad_values=True,
        ))

    # ---------------- stock ----------------
    def _stock_table(self):
        rows = self._read("stock")
        if not rows:
            raise StorageError(f"La pestaña '{self._stock_tab}' está vacía: falta el encabezado.")
        idx = check_header(rows[0], STOCK_COLUMNS, self._stock_tab)
        products = []
        for n, values in enumerate(rows[1:], start=2):
            p = row_to_product(values, idx, n)
            if p:
                products.append(p)
        return idx, products

    def list_products(self) -> list[Product]:
        return self._stock_table()[1]

    def _write_cells(self, which: str, updates: list[tuple[int, int, object]]) -> None:
        """updates = [(fila, columna_1based, valor)] en una sola llamada."""
        data = [{"range": rowcol_to_a1(r, c), "values": [[v]]} for r, c, v in updates]
        self._call(lambda: self._ws(which).batch_update(
            data, value_input_option=ValueInputOption.user_entered))

    def set_stock(self, sku: str, new_stock: float, stamp: str) -> Product:
        idx, products = self._stock_table()
        p = next((x for x in products if x.sku.upper() == sku.upper()), None)
        if p is None:
            raise KeyError(sku)
        self._write_cells("stock", [(p.row, idx["Stock"] + 1, new_stock),
                                    (p.row, idx["Actualizado"] + 1, stamp)])
        p.stock = new_stock
        return p

    def apply_stock_deltas(self, deltas: dict[str, float], stamp: str) -> None:
        idx, products = self._stock_table()
        by_sku = {p.sku.upper(): p for p in products}
        updates = []
        for sku, delta in deltas.items():
            p = by_sku.get(sku.upper())
            if p is None:
                raise KeyError(sku)
            updates.append((p.row, idx["Stock"] + 1, p.stock + delta))
            updates.append((p.row, idx["Actualizado"] + 1, stamp))
        if updates:
            self._write_cells("stock", updates)

    def set_price(self, sku: str, price: float, valid_until: date | None, stamp: str) -> Product:
        idx, products = self._stock_table()
        p = next((x for x in products if x.sku.upper() == sku.upper()), None)
        if p is None:
            raise KeyError(sku)
        self._write_cells("stock", [
            (p.row, idx["Precio"] + 1, price),
            (p.row, idx["Vigente_hasta"] + 1, valid_until.isoformat() if valid_until else ""),
            (p.row, idx["Actualizado"] + 1, stamp),
        ])
        p.price, p.valid_until = price, valid_until
        return p

    # ---------------- pedidos ----------------
    def _orders_table(self):
        rows = self._read("orders")
        if not rows:
            raise StorageError(f"La pestaña '{self._orders_tab}' está vacía: falta el encabezado.")
        idx = check_header(rows[0], ORDER_COLUMNS, self._orders_tab)
        orders = []
        for n, values in enumerate(rows[1:], start=2):
            o = row_to_order(values, idx, n)
            if o:
                orders.append(o)
        return idx, orders

    def add_order(self, order: Order) -> None:
        row = order_to_row(order)
        # RAW: se guarda tal cual (evita que Sheets interprete el ID o el JSON como fórmula).
        self._call(lambda: self._ws("orders").append_row(
            row, value_input_option=ValueInputOption.raw, table_range="A1"))

    def list_orders(self) -> list[Order]:
        return self._orders_table()[1]

    def update_order_status(self, order_id, status, reason, reviewed_by, stamp, lines=None) -> Order:
        idx, orders = self._orders_table()
        o = next((x for x in orders if x.order_id.upper() == order_id.upper()), None)
        if o is None:
            raise KeyError(order_id)
        o.status, o.reason, o.updated_at, o.reviewed_by = status, reason, stamp, reviewed_by
        if lines is not None:
            o.lines = lines
        row = dict(zip(ORDER_COLUMNS, order_to_row(o)))
        cols = ["Estado", "Motivo", "Actualizado", "Revisado_por"]
        if lines is not None:
            cols += ["Detalle", "Items_JSON", "Total"]
        # RAW para que el JSON y los textos se guarden tal cual
        data = [{"range": rowcol_to_a1(o.row, idx[c] + 1), "values": [[row[c]]]} for c in cols]
        self._call(lambda: self._ws("orders").batch_update(data, value_input_option=ValueInputOption.raw))
        return o
