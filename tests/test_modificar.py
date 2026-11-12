"""Modificar un pedido ya hecho: productos y/o dirección, con confirmación."""
from datetime import datetime, timedelta

from app.models import ESTADO_CONFIRMADO, ESTADO_RECHAZADO, ESTADO_REVISION

from .conftest import ADMIN, CLIENT, CLIENT_ADDRESS, avisos, button, pedir, plain


def stock(repo, sku):
    return repo.get_product(sku).stock


def test_modificar_productos_ajusta_el_stock(svc, repo, settings):
    settings.rules.max_order_total = 0
    pedir(svc, CLIENT, "2 coca y 1 fanta")
    oid = repo.list_orders()[-1].order_id
    assert stock(repo, "COC1") == 48 and stock(repo, "FAN1") == 39

    out = svc.handle_message(CLIENT, "/modificar")
    txt = plain(out[0].text)
    assert f"Así quedaría tu pedido {oid}" in txt and "Decime qué querés cambiar" in txt

    out = svc.handle_message(CLIENT, "3 coca, 1 sprite")   # lista completa nueva
    assert "3 × Coca-Cola 2.25L" in plain(out[0].text) and "Fanta" not in plain(out[0].text)
    assert stock(repo, "COC1") == 48  # nada cambia hasta confirmar

    out = svc.handle_callback(CLIENT, button(out, "cf:"))
    assert "¡Pedido modificado y confirmado!" in plain(out[0].text)
    o = repo.get_order(oid)
    assert len(repo.list_orders()) == 1  # mismo pedido, no uno nuevo
    assert [(l.sku, l.qty) for l in o.lines] == [("COC1", 3), ("SPR1", 1)] and o.total == 12600
    assert o.status == ESTADO_CONFIRMADO and "Modificado por el cliente" in o.reason
    assert stock(repo, "COC1") == 47 and stock(repo, "FAN1") == 40 and stock(repo, "SPR1") == 39
    assert all(m.chat_id == CLIENT.id for m in out)
    aviso = avisos(repo)[0]
    assert aviso.kind == "modificado" and aviso.order_id == oid and "3 × Coca-Cola 2.25L" in aviso.detail


def test_modificar_solo_la_direccion(svc, repo):
    pedir(svc, CLIENT, "2 coca")
    oid = repo.list_orders()[-1].order_id
    out = svc.handle_message(CLIENT, "/modificar")
    svc.handle_callback(CLIENT, button(out, "cd:"))
    out = svc.handle_message(CLIENT, "Belgrano 300, Tafí Viejo")
    assert "Dirección: Belgrano 300, Tafí Viejo" in plain(out[0].text)
    out = svc.handle_message(CLIENT, "sí")
    o = repo.get_order(oid)
    assert o.address == "Belgrano 300, Tafí Viejo" and o.status == ESTADO_CONFIRMADO
    assert stock(repo, "COC1") == 48  # el stock no se toca
    assert "¿Qué dirección dejo guardada" in plain(out[-1].text)  # y pregunta cuál guardar
    assert repo.get_customer("telegram", str(CLIENT.id)).address == CLIENT_ADDRESS


def test_al_modificar_cuenta_lo_que_el_pedido_ya_tenia(svc, repo, settings):
    settings.rules.max_order_total = 0
    pedir(svc, CLIENT, "2 cerveza")
    oid = repo.list_orders()[-1].order_id
    assert stock(repo, "CER1") == 3
    svc.handle_message(CLIENT, "/modificar")
    out = svc.handle_message(CLIENT, "5 cerveza")  # 3 libres + 2 suyas = 5: alcanza
    assert "Así quedaría tu pedido" in plain(out[0].text)
    out = svc.handle_message(CLIENT, "8 cerveza")
    assert "pediste 8, me quedan 5" in plain(out[0].text)
    svc.handle_message(CLIENT, "si")   # ajusta a 5
    out = svc.handle_message(CLIENT, "si")
    assert "¡Pedido modificado y confirmado!" in plain(out[0].text)
    assert [(l.sku, l.qty) for l in repo.get_order(oid).lines] == [("CER1", 5)] and stock(repo, "CER1") == 0


def test_pedido_en_revision_que_al_modificar_entra(svc, repo):
    pedir(svc, CLIENT, "1 fernet")
    oid = repo.list_orders()[-1].order_id
    assert repo.get_order(oid).status == ESTADO_REVISION      # $ 12.000 supera el tope de $ 10.000
    svc.handle_message(CLIENT, "/modificar")
    svc.handle_message(CLIENT, "3 coca")                       # $ 9.600: entra en el tope
    svc.handle_message(CLIENT, "si")
    assert repo.get_order(oid).status == ESTADO_CONFIRMADO
    assert stock(repo, "FER1") == 10 and stock(repo, "COC1") == 47


def test_cambio_que_deja_el_pedido_en_revision(svc, repo):
    pedir(svc, CLIENT, "2 coca")
    oid = repo.list_orders()[-1].order_id
    svc.handle_message(CLIENT, "/modificar")
    svc.handle_message(CLIENT, "2 coca y 1 fernet")  # fernet con precio vencido
    out = svc.handle_message(CLIENT, "si")
    assert repo.get_order(oid).status == ESTADO_REVISION and stock(repo, "COC1") == 50  # devolvió las 2
    assert all(m.chat_id == CLIENT.id for m in out)
    assert avisos(repo)[0].kind == "revision" and "quedó en revisión" in avisos(repo)[0].title


def test_descartar_cambios_deja_el_pedido_igual(svc, repo):
    pedir(svc, CLIENT, "2 coca")
    oid = repo.list_orders()[-1].order_id
    svc.handle_message(CLIENT, "/modificar")
    svc.handle_message(CLIENT, "5 coca")
    assert "queda como estaba" in plain(svc.handle_message(CLIENT, "no")[0].text)
    assert [(l.sku, l.qty) for l in repo.get_order(oid).lines] == [("COC1", 2)] and stock(repo, "COC1") == 48


def test_sin_cambios(svc, repo):
    pedir(svc, CLIENT, "2 coca")
    svc.handle_message(CLIENT, "/modificar")
    assert "No hay cambios" in plain(svc.handle_message(CLIENT, "si")[0].text)


def test_fuera_de_termino_o_rechazado_no_se_modifica(svc, repo, settings):
    pedir(svc, CLIENT, "1 fernet")
    oid = repo.list_orders()[-1].order_id
    assert svc.reject(oid, ADMIN, "Sin Fernet").ok
    assert repo.get_order(oid).status == ESTADO_RECHAZADO
    assert "ya no se puede modificar" in plain(svc.handle_message(CLIENT, "/modificar")[0].text)

    settings.rules.modify_minutes = 30
    pedir(svc, CLIENT, "1 coca")
    o = repo.list_orders()[-1]
    o.created_at = (datetime.now(svc.tz) - timedelta(minutes=31)).strftime("%Y-%m-%d %H:%M:%S")
    repo._conn.execute("UPDATE pedidos SET fecha = ? WHERE pedido_id = ?", (o.created_at, o.order_id))
    assert "ya no se puede modificar" in plain(svc.handle_message(CLIENT, "/modificar")[0].text)


def test_si_el_admin_lo_rechaza_mientras_tanto(svc, repo):
    pedir(svc, CLIENT, "1 fernet")
    oid = repo.list_orders()[-1].order_id
    svc.handle_message(CLIENT, "/modificar")
    svc.handle_message(CLIENT, "4 cerveza")
    assert svc.reject(oid, ADMIN, "sin camión").ok
    assert "ya no se puede modificar" in plain(svc.handle_message(CLIENT, "si")[0].text)
    assert stock(repo, "CER1") == 5 and repo.get_order(oid).status == ESTADO_RECHAZADO


def test_modificar_conserva_la_direccion_del_pedido(svc, repo):
    pedir(svc, CLIENT, "2 coca")
    svc.handle_message(CLIENT, "/modificar")
    out = svc.handle_message(CLIENT, "3 coca")
    assert f"Dirección: {CLIENT_ADDRESS}" in plain(out[0].text)
