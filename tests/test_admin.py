"""Acciones del admin para la web: login, productos, avisos y tablero."""
from datetime import date

from app.auth import hash_password, verify_password
from app.models import Product

from .conftest import ADMIN, CLIENT, pedir


def test_contrasenas_con_hash():
    h = hash_password("limones123")
    assert h.startswith("scrypt$") and "limones123" not in h
    assert verify_password("limones123", h) and not verify_password("otra", h)
    assert hash_password("limones123") != h  # sal distinta cada vez
    assert not verify_password("x", "basura") and not verify_password("x", "")


def test_crear_admin_y_login(svc, repo):
    assert not svc.create_admin("lucía fernández", "Lucía", "limones123").ok   # espacios
    assert "8 caracteres" in svc.create_admin("lucia", "Lucía", "corta").message
    assert svc.create_admin("Lucia", "Lucía Fernández", "limones123").ok
    admin = svc.authenticate("LUCIA", "limones123")  # el usuario no distingue mayúsculas
    assert admin and admin.name == "Lucía Fernández"
    assert svc.authenticate("lucia", "mal") is None and svc.authenticate("nadie", "limones123") is None
    assert svc.create_admin("pedro", "Pedro", "naranjas12").ok
    assert [a.username for a in repo.list_admins()] == ["lucia", "pedro"]
    admin.active = False
    repo.save_admin(admin, svc.stamp())
    assert svc.authenticate("lucia", "limones123") is None  # desactivado


def test_alta_y_edicion_de_producto(svc, repo):
    nuevo = Product("pep1", "Pepsi 2.25L", ["pepsi"], 24, 2900, None, True, "Gaseosas")
    assert svc.save_product(nuevo, is_new=True).ok
    assert repo.get_product("PEP1").name == "Pepsi 2.25L"
    assert "Ya existe" in svc.save_product(Product("COC1", "X", [], 1, 1, None, True), is_new=True).message
    assert "No existe" in svc.save_product(Product("ZZZ", "X", [], 1, 1, None, True), is_new=False).message
    assert not svc.save_product(Product("A B", "X", [], 1, 1, None, True), is_new=True).ok
    assert not svc.save_product(Product("OK1", " ", [], 1, 1, None, True), is_new=True).ok
    assert not svc.save_product(Product("OK1", "X", [], -1, 1, None, True), is_new=True).ok
    editado = Product("PEP1", "Pepsi 2.25L", ["pepsi", "pecsi"], 24, 3100, date(2030, 1, 1), True, "Gaseosas")
    r = svc.save_product(editado, is_new=False)
    assert r.ok and r.product.price == 3100 and r.product.aliases == ["pepsi", "pecsi"]


def test_precio_validaciones(svc, repo):
    assert not svc.set_price("COC1", 0).ok
    assert "ya pasó" in svc.set_price("COC1", 100, date(2000, 1, 1)).message
    assert "No existe" in svc.set_price("NOPE", 100).message
    pedir(svc, CLIENT, "1 fernet")
    oid = repo.list_orders()[-1].order_id
    assert "mayor a 0" in svc.approve(oid, ADMIN, {"FER1": -5}).message


def test_avisos_se_marcan_leidos(svc, repo):
    pedir(svc, CLIENT, "1 fernet")
    assert len(repo.list_notices(unread_only=True)) == 1
    repo.mark_notices_read()
    assert repo.list_notices(unread_only=True) == [] and len(repo.list_notices()) == 1


def test_tablero(svc, repo):
    pedir(svc, CLIENT, "2 coca")      # confirmado solo: 6.400
    pedir(svc, CLIENT, "1 fernet")    # en revisión
    d = svc.dashboard()
    assert d["sales_today"] == 6400 and d["orders_today"] == 2 and d["auto_confirmed_today"] == 1
    assert [o.order_id for o in d["pending"]] == [repo.list_orders()[-1].order_id]
    assert [p.sku for p in d["low_stock"]] == ["VIN1", "CER1"]   # menos de 10, el más crítico primero
    assert d["unread_notices"] == 1
