"""Catálogo real del mayorista (catalogo/mayorista_tucuman.csv) y la carga con --reemplazar."""
import sys
from pathlib import Path

from app.categorias import CATEGORIAS
from app.parser import parse_order
from app.storage.sqlite import SQLiteRepository
from scripts import init_db

CSV = Path(__file__).resolve().parent.parent / "catalogo" / "mayorista_tucuman.csv"


def test_el_csv_del_mayorista_es_valido():
    products = init_db.read_csv(str(CSV))
    assert len(products) > 200 and len({p.sku for p in products}) == len(products)
    assert {p.category for p in products} <= set(CATEGORIAS)
    assert all(p.price > 0 and p.price == int(p.price) for p in products)
    assert all(p.stock % p.pack_size == 0 for p in products if p.pack_size)
    assert all(ch.isascii() or ch.isalpha() for p in products for ch in p.name)   # sin emojis


def test_con_el_catalogo_real_se_entienden_los_pedidos():
    products = [p for p in init_db.read_csv(str(CSV)) if p.stock]
    items = parse_order("2 cajas de branca, 3 bultos de norte, 2 fernet buhero", products)
    got = {i.product.name: i.qty for i in items}
    assert got == {"Fernet Branca 750ml": 24, "Cerveza Norte Lata 473ml": 72, "Fernet Buhero Negro 700ml": 2}


def test_reemplazar_desactiva_lo_que_no_esta_en_el_csv(tmp_path, monkeypatch):
    db = tmp_path / "t.db"
    monkeypatch.setenv("DATABASE_PATH", str(db))
    for args in (["--ejemplo"], ["--csv", str(CSV), "--reemplazar"]):
        monkeypatch.setattr(sys, "argv", ["init_db", *args])
        init_db.main()
    repo = SQLiteRepository(str(db))
    try:
        old = repo.get_product("COC1")
        assert old is not None and not old.active                      # no se borra: queda inactivo
        assert repo.get_product("FER001").active
    finally:
        repo.close()
