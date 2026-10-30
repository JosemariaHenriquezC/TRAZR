"""
Prueba del adaptador de Google Sheets contra una hoja falsa en memoria
(no hay credenciales reales acá): verifica lectura, escritura de celdas,
alta de filas y traducción de errores de la API.
"""
import gspread
import pytest
from gspread.utils import a1_to_rowcol

from app.models import ORDER_COLUMNS, STOCK_COLUMNS
from app.sample_data import sample_stock_rows
from app.service import BotService
from app.storage.base import StorageError
from app.storage.gsheets import GoogleSheetsRepository

from .conftest import ADMIN, CLIENT, plain


class FakeWorksheet:
    def __init__(self, rows):
        self.rows = [list(r) for r in rows]

    def get(self, **kwargs):
        width = max(len(r) for r in self.rows)
        return [r + [""] * (width - len(r)) for r in self.rows]

    def batch_update(self, data, **kwargs):
        for item in data:
            r, c = a1_to_rowcol(item["range"])
            while len(self.rows) < r:
                self.rows.append([])
            row = self.rows[r - 1]
            while len(row) < c:
                row.append("")
            row[c - 1] = item["values"][0][0]

    def append_row(self, values, **kwargs):
        self.rows.append(list(values))


class FakeResponse:
    def __init__(self, code):
        self.status_code = code
        self.text = "error"

    def json(self):
        return {"error": {"code": self.status_code, "message": "denied", "status": "X"}}


@pytest.fixture
def gs(settings, monkeypatch):
    stock_ws = FakeWorksheet([STOCK_COLUMNS] + sample_stock_rows())
    orders_ws = FakeWorksheet([ORDER_COLUMNS])
    repo = GoogleSheetsRepository("x.json", "STOCK_ID", "ORDERS_ID")
    monkeypatch.setattr(repo, "_ws", lambda which: stock_ws if which == "stock" else orders_ws)
    return repo, stock_ws, orders_ws, BotService(repo, settings)


def test_flujo_completo_en_sheets(gs):
    repo, stock_ws, orders_ws, svc = gs
    svc.handle_message(CLIENT, "2 yerba y 3 azucar")
    assert stock_ws.rows[1][3] == 48 and stock_ws.rows[2][3] == 27
    assert orders_ws.rows[1][ORDER_COLUMNS.index("Estado")] == "CONFIRMADO"
    assert orders_ws.rows[1][ORDER_COLUMNS.index("Total")] == 12600

    svc.handle_message(CLIENT, "10 aceite")
    oid = orders_ws.rows[2][0]
    assert orders_ws.rows[2][ORDER_COLUMNS.index("Estado")] == "EN REVISION"
    svc.handle_message(ADMIN, "/sumar ACE1 5")
    out = svc.handle_callback(ADMIN, f"ap:{oid}")
    assert "¡Tu pedido fue confirmado!" in plain(out[1].text)
    assert orders_ws.rows[2][ORDER_COLUMNS.index("Estado")] == "CONFIRMADO"
    assert stock_ws.rows[3][3] == 0


def test_numeros_como_texto_formato_es(gs):
    repo, stock_ws, _, _ = gs
    stock_ws.rows[1][4] = "4.500,50"     # precio escrito como texto con formato local
    stock_ws.rows[1][5] = "31/12/2099"   # fecha dd/mm/aaaa
    p = repo.get_product("YER1")
    assert p.price == 4500.5 and p.valid_until.year == 2099


def test_encabezado_incorrecto(gs):
    repo, stock_ws, _, _ = gs
    stock_ws.rows[0][3] = "Cantidad"
    with pytest.raises(StorageError, match="no tiene las columnas: Stock"):
        repo.list_products()


def test_error_api_403_se_traduce(settings, monkeypatch):
    repo = GoogleSheetsRepository("x.json", "S", "O")
    repo.RETRIES = 1

    def denied(which):
        raise gspread.exceptions.APIError(FakeResponse(403))
    monkeypatch.setattr(repo, "_ws", denied)
    with pytest.raises(StorageError, match="compartila con la cuenta de servicio"):
        repo.list_products()
