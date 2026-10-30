"""
Almacenamiento en dos archivos Excel locales (.xlsx).

Pensado para PROBAR el bot sin crear nada en Google: mismas columnas, mismas
reglas. No apto para producción con varios usuarios editando a la vez (no es
colaborativo ni está en la nube).
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from openpyxl import Workbook, load_workbook

from ..models import ORDER_COLUMNS, STOCK_COLUMNS, Order, Product
from .base import (Repository, StorageError, check_header, order_to_row,
                   row_to_order, row_to_product)


class ExcelRepository(Repository):
    def __init__(self, stock_path: str, orders_path: str,
                 stock_tab: str = "Stock", orders_tab: str = "Pedidos"):
        self.stock_path, self.orders_path = Path(stock_path), Path(orders_path)
        self.stock_tab, self.orders_tab = stock_tab, orders_tab

    # ---------------- utilidades ----------------
    def _open(self, path: Path, tab: str):
        if not path.exists():
            raise StorageError(f"No existe el archivo {path}. Ejecutá: python -m scripts.init_sheets")
        try:
            wb = load_workbook(path)
        except Exception as exc:
            raise StorageError(f"No pude abrir {path}: {exc}") from exc
        if tab not in wb.sheetnames:
            raise StorageError(f"El archivo {path} no tiene la pestaña '{tab}'.")
        return wb, wb[tab]

    def _save(self, wb, path: Path):
        try:
            wb.save(path)
        except PermissionError as exc:
            raise StorageError(f"No pude guardar {path} (¿está abierto en Excel?).") from exc

    @staticmethod
    def _rows(ws) -> list[list]:
        return [list(r) for r in ws.iter_rows(values_only=True)]

    # ---------------- stock ----------------
    def _stock(self):
        wb, ws = self._open(self.stock_path, self.stock_tab)
        rows = self._rows(ws)
        if not rows:
            raise StorageError("La hoja de stock está vacía: falta el encabezado.")
        idx = check_header(rows[0], STOCK_COLUMNS, self.stock_tab)
        products = [p for n, v in enumerate(rows[1:], start=2) if (p := row_to_product(v, idx, n))]
        return wb, ws, idx, products

    def list_products(self) -> list[Product]:
        return self._stock()[3]

    def set_stock(self, sku, new_stock, stamp) -> Product:
        wb, ws, idx, products = self._stock()
        p = next((x for x in products if x.sku.upper() == sku.upper()), None)
        if p is None:
            raise KeyError(sku)
        ws.cell(p.row, idx["Stock"] + 1).value = new_stock
        ws.cell(p.row, idx["Actualizado"] + 1).value = stamp
        self._save(wb, self.stock_path)
        p.stock = new_stock
        return p

    def apply_stock_deltas(self, deltas, stamp) -> None:
        wb, ws, idx, products = self._stock()
        by_sku = {p.sku.upper(): p for p in products}
        for sku in deltas:
            if sku.upper() not in by_sku:
                raise KeyError(sku)
        for sku, delta in deltas.items():
            p = by_sku[sku.upper()]
            ws.cell(p.row, idx["Stock"] + 1).value = p.stock + delta
            ws.cell(p.row, idx["Actualizado"] + 1).value = stamp
        self._save(wb, self.stock_path)

    def set_price(self, sku, price, valid_until: date | None, stamp) -> Product:
        wb, ws, idx, products = self._stock()
        p = next((x for x in products if x.sku.upper() == sku.upper()), None)
        if p is None:
            raise KeyError(sku)
        ws.cell(p.row, idx["Precio"] + 1).value = price
        ws.cell(p.row, idx["Vigente_hasta"] + 1).value = valid_until.isoformat() if valid_until else None
        ws.cell(p.row, idx["Actualizado"] + 1).value = stamp
        self._save(wb, self.stock_path)
        p.price, p.valid_until = price, valid_until
        return p

    # ---------------- pedidos ----------------
    def _orders(self):
        wb, ws = self._open(self.orders_path, self.orders_tab)
        rows = self._rows(ws)
        if not rows:
            raise StorageError("La hoja de pedidos está vacía: falta el encabezado.")
        idx = check_header(rows[0], ORDER_COLUMNS, self.orders_tab)
        orders = [o for n, v in enumerate(rows[1:], start=2) if (o := row_to_order(v, idx, n))]
        return wb, ws, idx, orders

    def add_order(self, order: Order) -> None:
        wb, ws, _, _ = self._orders()
        ws.append(order_to_row(order))
        self._save(wb, self.orders_path)

    def list_orders(self) -> list[Order]:
        return self._orders()[3]

    def update_order_status(self, order_id, status, reason, reviewed_by, stamp, lines=None) -> Order:
        wb, ws, idx, orders = self._orders()
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
        for c in cols:
            ws.cell(o.row, idx[c] + 1).value = row[c]
        self._save(wb, self.orders_path)
        return o


def create_excel_files(stock_path: str, orders_path: str, sample_rows: list[list],
                       stock_tab="Stock", orders_tab="Pedidos", overwrite=False) -> None:
    """Crea los dos .xlsx con encabezados (y productos de ejemplo en stock)."""
    for path, tab, header, rows in [(stock_path, stock_tab, STOCK_COLUMNS, sample_rows),
                                    (orders_path, orders_tab, ORDER_COLUMNS, [])]:
        p = Path(path)
        if p.exists() and not overwrite:
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        wb = Workbook()
        ws = wb.active
        ws.title = tab
        ws.append(header)
        for r in rows:
            ws.append(r)
        wb.save(p)
