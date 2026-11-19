"""Acciones del admin para la web: login, productos, avisos y tablero."""
from app.auth import hash_password, verify_password
from app.models import Product

from .conftest import CLIENT, pedir


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
    nuevo = Product("pep1", "Pepsi 2.25L", ["pepsi"], 24, 2900, True, "Gaseosas")
    assert svc.save_product(nuevo, is_new=True).ok
    assert repo.get_product("PEP1").name == "Pepsi 2.25L"
    assert "Ya existe" in svc.save_product(Product("COC1", "X", [], 1, 1), is_new=True).message
    assert "No existe" in svc.save_product(Product("ZZZ", "X", [], 1, 1), is_new=False).message
    assert not svc.save_product(Product("A B", "X", [], 1, 1), is_new=True).ok
    assert not svc.save_product(Product("OK1", " ", [], 1, 1), is_new=True).ok
    assert not svc.save_product(Product("OK1", "X", [], -1, 1), is_new=True).ok
    editado = Product("PEP1", "Pepsi 2.25L", ["pepsi", "pecsi"], 24, 3100, True, "Gaseosas")
    r = svc.save_product(editado, is_new=False)
    assert r.ok and r.product.price == 3100 and r.product.aliases == ["pepsi", "pecsi"]


def test_precio_validaciones(svc, repo):
    assert not svc.set_price("COC1", 0).ok and not svc.set_price("COC1", -5).ok
    assert "No existe" in svc.set_price("NOPE", 100).message
    assert svc.set_price("COC1", 3500).ok and repo.get_product("COC1").price == 3500


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


def _envejecer(repo, order_id, horas):
    from datetime import datetime, timedelta, timezone
    viejo = (datetime.now(timezone.utc) - timedelta(hours=horas)).strftime("%Y-%m-%d %H:%M:%S")
    repo._conn.execute("UPDATE pedidos SET fecha = ?, actualizado = ? WHERE pedido_id = ?", (viejo, viejo, order_id))


def test_transferencia_impaga_se_cancela_sola(svc, repo):
    pedir(svc, CLIENT, "2 coca", pago="transferencia")
    pedir(svc, CLIENT, "1 sprite")                       # efectivo: no vence
    t, e = repo.list_orders()
    assert t.awaiting_payment and e.awaiting_payment and repo.get_product("COC1").stock == 48
    assert svc.expire_unpaid() == []                     # todavía está en término
    _envejecer(repo, t.order_id, 25)
    _envejecer(repo, e.order_id, 25)
    avisos_cliente = svc.expire_unpaid()
    assert len(avisos_cliente) == 1 and "No recibimos la transferencia en 24 h" in avisos_cliente[0].text
    assert repo.get_order(t.order_id).status == "CANCELADO" and repo.get_product("COC1").stock == 50
    assert repo.get_order(e.order_id).awaiting_payment     # el efectivo sigue esperando
    assert any(n.kind == "pago_vencido" for n in repo.list_notices())


def test_pago_solo_de_pedidos_confirmados(svc, repo):
    from .conftest import ADMIN
    pedir(svc, CLIENT, "1 fernet")                        # supera el tope: en revisión
    oid = repo.list_orders()[-1].order_id
    assert "solo se registra el pago de pedidos confirmados" in svc.confirm_payment(oid, ADMIN).message
    assert "no está pendiente de pago" in svc.cancel_unpaid(oid, ADMIN).message
    assert svc.approve(oid, ADMIN).ok and svc.confirm_payment(oid, ADMIN).ok
    # Un pedido pagado ya no se puede modificar desde el chat
    assert "ya no se puede modificar" in svc.handle_message(CLIENT, "/modificar")[0].text


def test_base_vieja_se_migra_sin_perder_pedidos(tmp_path):
    import sqlite3
    from app.storage.sqlite import SQLiteRepository
    path = str(tmp_path / "vieja.db")
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE pedidos (pedido_id TEXT PRIMARY KEY, fecha TEXT NOT NULL, canal TEXT NOT NULL,
          cliente_id TEXT NOT NULL, cliente_nombre TEXT NOT NULL DEFAULT '', mensaje_original TEXT NOT NULL DEFAULT '',
          direccion_envio TEXT NOT NULL, costo_envio REAL NOT NULL DEFAULT 0, total REAL NOT NULL,
          estado TEXT NOT NULL CHECK (estado IN ('CONFIRMADO','EN REVISION','RECHAZADO')),
          motivo TEXT NOT NULL DEFAULT '', actualizado TEXT NOT NULL, revisado_por TEXT NOT NULL DEFAULT '');
        INSERT INTO pedidos VALUES ('P1','2026-11-01 10:00:00','telegram','5','Ana','2 coca','Calle 1',0,6400,'CONFIRMADO','','2026-11-01 10:00:00','');
        INSERT INTO pedidos VALUES ('P2','2026-11-01 11:00:00','telegram','5','Ana','9 fernet','Calle 1',0,108000,'EN REVISION','','2026-11-01 11:00:00','');
    """)
    con.commit(); con.close()
    repo = SQLiteRepository(path)
    p1, p2 = repo.get_order("P1"), repo.get_order("P2")
    assert p1.customer_name == "Ana" and p1.address == "Calle 1" and p1.is_paid   # lo confirmado se da por pagado
    assert p2.status == "EN REVISION" and p2.payment_status == "pendiente" and p2.payment_method == "efectivo"
    p2.status = "CANCELADO"
    repo.update_order(p2)                                         # el estado nuevo ya se acepta
    assert repo.get_order("P2").status == "CANCELADO"
    repo.close()
