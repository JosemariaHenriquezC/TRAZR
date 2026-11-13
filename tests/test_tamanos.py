"""Tamaños y envases: el mismo producto en 750ml / 1L, lata / porrón (con el catálogo real)."""
import json
from pathlib import Path

import pytest

from app.models import Customer
from app.parser import parse_order, size_label
from app.service import BotService
from app.storage.sqlite import SQLiteRepository
from scripts.init_db import read_csv

from .conftest import CLIENT, CLIENT_ADDRESS, button, plain
from .test_gemini_en_el_bot import _FakeModels, _svc, pedido

CSV = Path(__file__).resolve().parent.parent / "catalogo" / "mayorista_tucuman.csv"
PRODUCTS = [p for p in read_csv(str(CSV)) if p.stock]


def one(text):
    [item] = parse_order(text, PRODUCTS)
    return item


@pytest.mark.parametrize("texto,producto,cantidad", [
    ("3 branca de litro", "Fernet Branca 1L", 3), ("branca 750 x 6", "Fernet Branca 750ml", 6),
    ("2 branca chico", "Fernet Branca 450ml", 2), ("10 miniaturas de branca", "Fernet Branca Miniatura", 10),
    ("4 coca de 3 litros", "Coca-Cola 3L", 4), ("12 coca lata", "Coca-Cola Lata 375ml", 12),
    ("6 coca 2.25", "Coca-Cola 2.25L", 6), ("6 coca 2 1/4", "Coca-Cola 2.25L", 6),
    ("24 heineken lata", "Cerveza Heineken Lata 473ml", 24), ("2 red label de litro",
     "Whisky Johnnie Walker Red Label 1L", 2), ("3 amarula chica", "Amarula 375ml", 3),
    ("24 latones de corona", "Cerveza Corona 710ml", 24), ("5 smirnoff", "Smirnoff", 5),
])
def test_el_tamano_que_dice_el_cliente_elige_el_producto(texto, producto, cantidad):
    item = one(texto)
    assert not item.error and item.product.name == producto and item.qty == cantidad


@pytest.mark.parametrize("texto,opciones", [
    ("2 branca", ["1L", "750ml", "450ml", "Miniatura"]),
    ("2 coca", ["Coca-Cola 3L", "Coca-Cola 2.25L", "Coca-Cola Lata 375ml"]),
    ("2 red label", ["1L", "750ml"]), ("6 agua", ["2L", "500ml"]), ("2 pepsi", ["3L", "2L"]),
])
def test_sin_tamano_se_pregunta(texto, opciones):
    item = one(texto)
    assert item.error == "ambiguo"
    assert [size_label(c, item.candidates) for c in item.candidates] == opciones


@pytest.fixture
def real(settings):
    r = SQLiteRepository(settings.database_path)
    for p in PRODUCTS:
        r.upsert_product(p, "2026-01-01 00:00:00")
    r.save_customer(Customer(CLIENT.channel, str(CLIENT.id), CLIENT.name, CLIENT_ADDRESS), "2026-01-01 00:00:00")
    yield r
    r.close()


def items(repo):
    found = repo.get_pending_order(CLIENT.channel, str(CLIENT.id))
    return json.loads(found[0])["items"] if found else None


def test_el_bot_pregunta_el_tamano_con_botones(real, settings):
    svc = BotService(real, settings)
    out = svc.handle_message(CLIENT, "2 branca y 24 norte")
    assert "¿de qué tamaño? Tengo 1L, 750ml, 450ml o Miniatura" in plain(out[0].text)
    assert [t for t, _ in out[0].buttons][:4] == ["1L", "750ml", "450ml", "Miniatura"]
    out = svc.handle_callback(CLIENT, button(out, "pk:") .rsplit(":", 1)[0] + ":FER002")   # 1L
    assert sorted(items(real)) == [["CER004", 24], ["FER002", 2]]
    assert button(out, "cf:") or button(out, "cp:")


def test_el_tamano_se_puede_contestar_escribiendo(real, settings):
    svc = BotService(real, settings)
    svc.handle_message(CLIENT, "2 branca y 24 norte")
    svc.handle_message(CLIENT, "la de 750")
    assert sorted(items(real)) == [["CER004", 24], ["FER001", 2]]


def test_cajas_sin_tamano_se_multiplican_por_la_caja_elegida(real, settings):
    svc = BotService(real, settings)
    out = svc.handle_message(CLIENT, "2 cajas de branca y 24 norte")
    out = svc.handle_callback(CLIENT, next(d for _, d in out[0].buttons if d.endswith(":FER001")))
    assert sorted(items(real)) == [["CER004", 24], ["FER001", 24]]       # 2 cajas de 12


def test_cajas_de_un_tamano_sin_caja_preguntan_las_unidades(real, settings):
    svc = BotService(real, settings)
    out = svc.handle_message(CLIENT, "2 cajas de branca y 24 norte")
    out = svc.handle_callback(CLIENT, next(d for _, d in out[0].buttons if d.endswith(":FER002")))
    assert "¿cuántas unidades de Fernet Branca 1L son en total?" in plain(out[0].text)


def test_si_gemini_elige_un_tamano_que_el_cliente_no_dijo_se_pregunta(real, settings):
    svc = _svc(real, settings, _FakeModels(pedido([("FER001", "2 branca", 2), ("CER004", "24 norte", 24)])))
    out = svc.handle_message(CLIENT, "2 branca y 24 norte")
    assert "¿de qué tamaño?" in plain(out[0].text) and items(real) == [["CER004", 24]]


def test_si_el_cliente_dijo_el_tamano_gemini_se_respeta(real, settings):
    svc = _svc(real, settings, _FakeModels(pedido([("FER002", "2 branca de litro", 2)])))
    svc.handle_message(CLIENT, "2 branca de litro")
    assert items(real) == [["FER002", 2]]


# ---------------------------------------------------------------- catálogo completo
@pytest.mark.parametrize("texto", ["ver todo", "quiero ver todo", "pasame el catálogo completo",
                                   "mostrame todos los productos", "la lista completa", "todo el catálogo"])
def test_ver_todo_manda_el_catalogo_entero_en_varios_mensajes(real, settings, texto):
    out = BotService(real, settings).handle_message(CLIENT, texto)
    joined = "\n".join(m.text for m in out)
    assert len(out) > 1 and all(len(m.text) <= 3500 for m in out)
    assert all(p.name.replace("&", "&amp;").replace("'", "&#x27;") in joined or p.name in joined for p in PRODUCTS)


def test_el_menu_de_categorias_ofrece_ver_todo(real, settings):
    svc = BotService(real, settings)
    out = svc.handle_message(CLIENT, "catálogo")
    assert ("📋 Ver todo", "cc:todo") in out[0].buttons and len(out[0].buttons) <= 10
    assert len(svc.handle_callback(CLIENT, "cc:todo")) > 1


def test_todos_los_vinos_sigue_siendo_la_categoria(real, settings):
    out = BotService(real, settings).handle_message(CLIENT, "mostrame todos los vinos")
    assert len(out) == 1 and "Vinos" in out[0].text and "Cerveza" not in out[0].text
