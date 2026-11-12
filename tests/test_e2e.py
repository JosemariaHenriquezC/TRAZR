"""
Prueba de punta a punta: mensajes reales → lógica del bot → base SQLite.
Mismo código que corre en Telegram.
"""
from app.models import ESTADO_CONFIRMADO, ESTADO_RECHAZADO, ESTADO_REVISION
from app.storage.base import StorageError

from .conftest import ADMIN, CLIENT, CLIENT_ADDRESS, pedir, plain


def stock(repo, sku):
    return repo.get_product(sku).stock


def last_order(repo):
    return repo.list_orders()[-1]


# 1) Pedido dentro de los parámetros → confirmado automáticamente
def test_pedido_confirmado(svc, repo):
    out = pedir(svc, CLIENT, "Hola, quiero 2 coca y 3 agua sin gas")
    assert len(out) == 1 and out[0].chat_id == CLIENT.id
    txt = plain(out[0].text)
    assert "¡Pedido confirmado!" in txt
    assert "2 × Coca-Cola 2.25L — $ 3.200,00 c/u = $ 6.400,00" in txt
    assert "3 × Agua Mineral sin Gas 2L — $ 1.100,00 c/u = $ 3.300,00" in txt
    assert "Envío: incluido, sin costo" in txt
    assert f"Dirección: {CLIENT_ADDRESS}" in txt
    assert "Total: $ 9.700,00" in txt
    # stock descontado
    assert stock(repo, "COC1") == 48 and stock(repo, "AGU1") == 27
    # registrado en la base de pedidos, con dirección y envío sin costo
    o = last_order(repo)
    assert o.status == ESTADO_CONFIRMADO and o.total == 9700 and o.customer_id == CLIENT.id
    assert o.address == CLIENT_ADDRESS and o.shipping_cost == 0 and o.channel == "telegram"
    assert o.created_at and o.original_text == "Hola, quiero 2 coca y 3 agua sin gas"
    assert [(l.sku, l.qty, l.unit_price) for l in o.lines] == [("COC1", 2, 3200), ("AGU1", 3, 1100)]


# 2) Faltante de stock → en revisión → admin repone y aprueba
def test_faltante_revision_y_aprobacion(svc, repo):
    out = pedir(svc, CLIENT, "mandame 10 cervezas")
    client_msg, admin_msg = out[0], out[1]
    assert client_msg.chat_id == CLIENT.id and "en revisión" in plain(client_msg.text)
    assert admin_msg.chat_id == ADMIN.id and "Stock insuficiente: Cerveza Rubia 1L (pedido 10, disponible 5)" in plain(admin_msg.text)
    order_id = last_order(repo).order_id
    assert admin_msg.buttons == [("✅ Aprobar", f"ap:{order_id}"), ("❌ Rechazar", f"rj:{order_id}")]
    assert last_order(repo).status == ESTADO_REVISION
    assert stock(repo, "CER1") == 5  # NO se descuenta mientras está en revisión

    # Aprobar sin stock → bloqueado con instrucción
    out = svc.handle_message(ADMIN, f"/aprobar {order_id}")
    assert "No puedo aprobar todavía" in plain(out[0].text) and "/sumar CER1" in plain(out[0].text)

    # Admin repone stock y aprueba con el botón
    out = svc.handle_message(ADMIN, "/sumar CER1 10")
    assert "ahora: 15" in plain(out[0].text)
    out = svc.handle_callback(ADMIN, f"ap:{order_id}")
    assert plain(out[0].text).startswith("✅ Pedido") and out[1].chat_id == CLIENT.id
    assert "¡Tu pedido fue confirmado!" in plain(out[1].text) and "Total: $ 28.000,00" in plain(out[1].text)
    assert stock(repo, "CER1") == 5
    o = last_order(repo)
    assert o.status == ESTADO_CONFIRMADO and o.reviewed_by.startswith("Admin") and "Stock insuficiente" in o.reason

    # No se puede aprobar dos veces
    out = svc.handle_callback(ADMIN, f"ap:{order_id}")
    assert "ya está" in plain(out[0].text) and stock(repo, "CER1") == 5


# 3) Precio vencido → revisión → admin actualiza precio y aprueba con el precio nuevo
def test_precio_no_vigente(svc, repo):
    out = pedir(svc, CLIENT, "1 fernet")
    assert "Precio no vigente: Fernet 750ml" in plain(out[1].text)
    oid = last_order(repo).order_id
    assert "precio no vigente" in plain(svc.handle_message(ADMIN, f"/aprobar {oid}")[0].text)
    assert "✅" in svc.handle_message(ADMIN, "/precio FER1 13000")[0].text
    out = svc.handle_message(ADMIN, f"/aprobar {oid}")
    assert "Total: $ 13.000,00" in plain(out[1].text)
    assert last_order(repo).total == 13000 and stock(repo, "FER1") == 9


# 4) Rechazo con motivo
def test_rechazo(svc, repo):
    pedir(svc, CLIENT, "2 vino")
    oid = last_order(repo).order_id
    out = svc.handle_message(ADMIN, f"/rechazar {oid} Sin stock hasta el lunes")
    assert out[1].chat_id == CLIENT.id and "Sin stock hasta el lunes" in plain(out[1].text)
    o = last_order(repo)
    assert o.status == ESTADO_RECHAZADO and o.reason == "Sin stock hasta el lunes"


# 5) Consulta y actualización de stock (admin)
def test_consulta_y_actualizacion_stock(svc, repo):
    txt = plain(svc.handle_message(ADMIN, "/stock coca")[0].text)
    assert "COC1 Coca-Cola 2.25L: 50 u." in txt
    assert "Stock de Coca-Cola 2.25L (COC1) actualizado a 120" in plain(svc.handle_message(ADMIN, "/setstock COC1 120")[0].text)
    assert stock(repo, "COC1") == 120
    assert "ahora: 115" in plain(svc.handle_message(ADMIN, "/sumar COC1 -5")[0].text)
    assert "negativo" in plain(svc.handle_message(ADMIN, "/sumar COC1 -500")[0].text)
    assert "No existe el SKU" in plain(svc.handle_message(ADMIN, "/setstock XXX 3")[0].text)
    assert "no es un número" in plain(svc.handle_message(ADMIN, "/setstock COC1 muchos")[0].text)
    # El cliente no puede usar comandos de admin
    assert "solo para el administrador" in svc.handle_message(CLIENT, "/setstock COC1 0")[0].text
    assert stock(repo, "COC1") == 115


# 6) Errores comunes: nada se registra y se pide aclaración
def test_errores_de_interpretacion(svc, repo):
    cases = {
        "3 agua": "¿cuál de estos? Agua Mineral con Gas 2L, Agua Mineral sin Gas 2L",
        "2 whisky": "«whisky»: no está en nuestro catálogo",
        "2,5 coca": "la cantidad debe ser un número entero entre 1 y 1000",
        "-3 sprite": "la cantidad debe ser un número entero",
        "5000 sprite": "entre 1 y 1000",
        "quiero coca": "¿cuántas unidades de Coca-Cola 2.25L?",
        "hola": "No encontré un pedido en tu mensaje",
        "2 coca y 1 whisky": "«whisky»: no está en nuestro catálogo",
    }
    for msg, expected in cases.items():
        out = pedir(svc, CLIENT, msg)
        assert expected in plain(out[0].text), (msg, out[0].text)
    assert repo.list_orders() == [] and stock(repo, "COC1") == 50


# 7) Falla de la base de datos: mensaje claro al cliente, alerta al admin, nada a medias
def test_falla_de_base_de_datos(svc, repo, monkeypatch):
    def boom(*a, **k):
        raise StorageError("La base de datos está ocupada")
    monkeypatch.setattr(repo, "list_products", boom)
    out = pedir(svc, CLIENT, "2 coca")
    assert "No pude acceder a la base de datos" in out[0].text
    assert out[1].chat_id == ADMIN.id and "La base de datos está ocupada" in out[1].text


def test_falla_al_registrar_revierte_stock(svc, repo, monkeypatch):
    def boom(order):
        raise StorageError("timeout")
    monkeypatch.setattr(repo, "add_order", boom)
    pedir(svc, CLIENT, "2 coca")
    assert stock(repo, "COC1") == 50  # se devolvió el stock


# 8) Reglas configurables
def test_regla_producto_desconocido_a_revision(svc, repo, settings):
    settings.rules.on_unknown_product = "review"
    out = pedir(svc, CLIENT, "2 coca y 1 whisky")
    assert "Producto no encontrado: «whisky»" in plain(out[1].text)
    assert last_order(repo).status == ESTADO_REVISION


def test_regla_total_maximo(svc, repo, settings):
    settings.rules.max_order_total = 10000
    pedir(svc, CLIENT, "4 coca")  # 12.800 > 10.000
    assert last_order(repo).status == ESTADO_REVISION and stock(repo, "COC1") == 50


def test_mismo_producto_dos_veces_se_suma(svc, repo):
    pedir(svc, CLIENT, "2 coca, 3 coca")
    o = last_order(repo)
    assert [(l.sku, l.qty) for l in o.lines] == [("COC1", 5)] and stock(repo, "COC1") == 45


def test_comandos_cliente(svc, repo):
    pedir(svc, CLIENT, "1 sprite")
    assert "Tus últimos pedidos" in svc.handle_message(CLIENT, "/mispedidos")[0].text
    cat = plain(svc.handle_message(CLIENT, "/catalogo")[0].text)
    assert "Vino Tinto Malbec 750ml — $ 6.500,00 ❌ sin stock" in cat and "Fernet 750ml — precio a confirmar" in cat
    assert "Gaseosas" in cat and "Envío incluido, sin costo" in cat
    assert "Hay 0" not in plain(svc.handle_message(ADMIN, "/revision")[0].text)
