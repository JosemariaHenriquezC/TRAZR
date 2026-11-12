"""Base de datos SQLite: persistencia, restricciones y carga del catálogo."""
from datetime import date

import pytest

from app.models import ESTADO_CONFIRMADO, Order, OrderLine
from app.sample_data import sample_products
from app.storage.base import StorageError
from app.storage.sqlite import SQLiteRepository
from scripts.init_db import read_csv


def _order(oid="P1", address="Calle 1 123, Tucumán", lines=None):
    return Order(order_id=oid, created_at="2026-11-12 10:00:00", customer_id=5, customer_name="Ana",
                 original_text="2 coca", lines=lines or [OrderLine("COC1", "Coca-Cola 2.25L", 2, 3200)],
                 status=ESTADO_CONFIRMADO, address=address)


def test_los_datos_persisten_al_reabrir(tmp_path):
    path = str(tmp_path / "x.db")
    r = SQLiteRepository(path)
    for p in sample_products():
        r.upsert_product(p, "s")
    r.add_order(_order())
    r.close()
    r2 = SQLiteRepository(path)
    assert len(r2.list_products()) == 10
    o = r2.get_order("p1")  # sin distinguir mayúsculas
    assert o.total == 6400 and o.address.startswith("Calle 1") and o.lines[0].qty == 2


def test_catalogo_solo_bebidas():
    cats = {p.category for p in sample_products()}
    assert cats == {"Gaseosas", "Aguas", "Cervezas", "Vinos", "Aperitivos", "Jugos", "Energizantes"}


def test_pedido_sin_direccion_rechazado_por_la_base(repo):
    with pytest.raises(StorageError, match="dirección"):
        repo.add_order(_order(address="   "))
    assert repo.list_orders() == []


def test_stock_nunca_negativo_y_transaccion_atomica(repo):
    with pytest.raises(StorageError, match="negativo"):
        repo.apply_stock_deltas({"COC1": -1, "CER1": -99}, "s")
    assert repo.get_product("COC1").stock == 50  # se deshizo todo
    with pytest.raises(StorageError):
        repo.apply_stock_deltas({"NOEXISTE": -1}, "s")


def test_actualizar_estado_reescribe_renglones_y_total(repo):
    repo.add_order(_order())
    o = repo.update_order_status("P1", ESTADO_CONFIRMADO, "ok", "Admin", "s2",
                                 [OrderLine("COC1", "Coca-Cola 2.25L", 3, 3300)])
    assert o.total == 9900 and [(l.qty, l.unit_price) for l in o.lines] == [(3, 3300)]
    with pytest.raises(KeyError):
        repo.update_order_status("NO", ESTADO_CONFIRMADO, "", "", "s")


def test_set_price_y_vigencia(repo):
    p = repo.set_price("fer1", 13000, date(2030, 1, 1), "s")
    assert p.price == 13000 and p.valid_until == date(2030, 1, 1)
    with pytest.raises(KeyError):
        repo.set_stock("XXX", 1, "s")


def test_importar_csv(tmp_path, repo):
    f = tmp_path / "cat.csv"
    f.write_text("SKU;Producto;Categoria;Alias;Stock;Precio;Vigente_hasta;Activo\n"
                 "COC1;Coca-Cola 2.25L;Gaseosas;coca, coca cola;80;3.400,50;31/12/2030;SI\n"
                 "PEP1;Pepsi 2.25L;Gaseosas;pepsi;20;2900;;\n", encoding="utf-8")
    for p in read_csv(str(f)):
        repo.upsert_product(p, "s")
    coc = repo.get_product("COC1")
    assert coc.stock == 80 and coc.price == 3400.5 and coc.valid_until == date(2030, 12, 31)
    pep = repo.get_product("PEP1")
    assert pep.active and pep.valid_until is None and pep.aliases == ["pepsi"]


def test_csv_con_errores_no_carga_nada(tmp_path):
    f = tmp_path / "mal.csv"
    f.write_text("SKU,Producto,Categoria,Alias,Stock,Precio,Vigente_hasta,Activo\n"
                 "X1,Algo,Gaseosas,,-3,100,,SI\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Stock"):
        read_csv(str(f))
