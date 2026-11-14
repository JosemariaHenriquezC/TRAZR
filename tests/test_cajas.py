"""Etapa 2 del plan de mejora: cajas, packs y fardos (unidades por caja por producto)."""
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.models import Product, plural
from app.notifier import Notifier
from app.parser import parse_order
from app.sample_data import sample_products
from app.storage.sqlite import SQLiteRepository
from app.web.main import create_app

from .conftest import ADMIN, CLIENT, button, plain
from .test_gemini_en_el_bot import _FakeModels, _svc, pedido


def coca(**kw):
    return Product("COC1", "Coca-Cola 2.25L", ["coca"], 50, 3200, pack_size=12, **kw)


@pytest.mark.parametrize("qty,label", [(25, "2 cajas + 1 suelta"), (30, "2 cajas + 6 sueltas"), (24, "2 cajas"),
                                       (12, "1 caja"), (5, "")])
def test_cajas_cerradas_y_sueltas(qty, label):
    assert coca().pack_label(qty) == label


@pytest.mark.parametrize("word,n,out", [("caja", 2, "cajas"), ("pack", 3, "packs"), ("cajón", 2, "cajones"),
                                        ("fardo", 2, "fardos"), ("caja", 1, "caja")])
def test_plural(word, n, out):
    assert plural(word, n) == out


@pytest.mark.parametrize("texto,esperado", [("2 cajas de coca", ("COC1", 12)), ("3 fardos de agua sin gas", ("AGU1", 18)),
                                            ("un cajón de birra", ("CER1", 12)), ("1 caja de energizantes", ("ENE1", 24))])
def test_las_cajas_se_pasan_a_unidades(texto, esperado):
    [item] = parse_order(texto, sample_products())
    assert (item.product.sku, item.qty) == esperado and item.error is None


def test_cajas_y_sueltas_del_mismo_producto_se_suman(svc, repo):
    svc.handle_message(CLIENT, "2 packs de coca y 3 cocas sueltas")
    out = svc.handle_message(CLIENT, "2 packs de coca y 3 cocas sueltas")
    assert "15 × Coca-Cola 2.25L (2 packs + 3 sueltas)" in plain(out[0].text)


def test_cajas_sin_cantidad_preguntan(svc, repo):
    out = svc.handle_message(CLIENT, "cajas de coca")
    assert "¿cuántas unidades de Coca-Cola" in plain(out[0].text)


def test_el_pedido_muestra_como_se_arma(svc, repo):
    out = svc.handle_message(CLIENT, "13 cocas")
    out = svc.handle_callback(CLIENT, button(out, "cf:"))
    out = svc.handle_callback(CLIENT, button(out, "pe:"))
    assert "13 × Coca-Cola 2.25L (2 packs + 1 suelta)" in plain(out[0].text)


def test_con_gemini_las_unidades_las_calcula_el_codigo(repo, settings):
    models = _FakeModels(pedido([("COC1", "2 cajas de coca", 2), ("FAN1", "3 fantas", 3)]))
    svc = _svc(repo, settings, models)
    svc.handle_message(CLIENT, "2 cajas de coca y 3 fantas")
    found = repo.get_pending_order("telegram", str(CLIENT.id))
    import json
    assert sorted(json.loads(found[0])["items"]) == [["COC1", 12], ["FAN1", 3]]


def test_base_vieja_se_actualiza_con_las_columnas_de_caja(tmp_path):
    path = tmp_path / "vieja.db"
    con = sqlite3.connect(path)
    con.execute("""CREATE TABLE productos (sku TEXT PRIMARY KEY COLLATE NOCASE, producto TEXT NOT NULL,
                   categoria TEXT NOT NULL DEFAULT '', alias TEXT NOT NULL DEFAULT '', stock INTEGER NOT NULL DEFAULT 0,
                   precio REAL NOT NULL DEFAULT 0, activo INTEGER NOT NULL DEFAULT 1, actualizado TEXT NOT NULL DEFAULT '')""")
    con.execute("INSERT INTO productos (sku, producto, stock, precio) VALUES ('COC1', 'Coca', 10, 3200)")
    con.commit()
    con.close()
    repo = SQLiteRepository(str(path))
    p = repo.get_product("COC1")
    assert (p.name, p.stock, p.pack_size, p.pack_name) == ("Coca", 10, 0, "caja")
    repo.upsert_product(Product("COC1", "Coca", [], 10, 3200, pack_size=6, pack_name="pack"), "2026-01-01 00:00:00")
    assert repo.get_product("COC1").pack_size == 6
    repo.close()


def test_la_web_guarda_las_unidades_por_caja(svc, repo, settings):
    from .test_web import csrf as read_csrf, login
    client = TestClient(create_app(svc, Notifier(), settings))
    assert svc.create_admin("lucia", "Lucía Fernández", "limones123").ok
    assert login(client).status_code == 303
    csrf = read_csrf(client.get("/admin/productos?editar=SPR1").text)
    r = client.post("/admin/productos/guardar", data={"csrf_token": csrf, "es_nuevo": "0", "sku": "SPR1",
                                                "nombre": "Sprite 2.25L", "alias": "sprite", "categoria": "Gaseosas",
                                                "precio": "3000", "stock": "40", "activo": "1",
                                                "unidades_caja": "6", "nombre_caja": "Pack"}, follow_redirects=False)
    assert r.status_code == 303
    p = repo.get_product("SPR1")
    assert (p.pack_size, p.pack_name) == (6, "pack")
    r = client.post("/admin/productos/guardar", data={"csrf_token": csrf, "es_nuevo": "0", "sku": "SPR1",
                                                "nombre": "Sprite 2.25L", "precio": "3000", "stock": "40",
                                                "unidades_caja": "seis"}, follow_redirects=False)
    assert repo.get_product("SPR1").pack_size == 6        # no se guardó lo inválido


def test_csv_con_unidades_por_caja(tmp_path):
    from scripts.init_db import read_csv
    f = tmp_path / "cat.csv"
    f.write_text("SKU;Producto;Categoria;Alias;Stock;Precio;Activo;UnidadesPorCaja;NombreCaja\n"
                 "PEP1;Pepsi 2.25L;Gaseosas;pepsi;20;2900;SI;6;Pack\n"
                 "AGU9;Agua 500ml;Aguas;agua chica;50;600;SI;;\n", encoding="utf-8")
    pep, agua = read_csv(str(f))
    assert (pep.pack_size, pep.pack_name) == (6, "pack") and (agua.pack_size, agua.pack_name) == (0, "caja")
