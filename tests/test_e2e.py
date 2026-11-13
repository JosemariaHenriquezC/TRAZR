"""
Prueba de punta a punta: mensajes reales → lógica del bot → base SQLite.
Mismo código que corre en Telegram.
"""
from app.models import ESTADO_CONFIRMADO, ESTADO_RECHAZADO, ESTADO_REVISION
from app.storage.base import StorageError

from .conftest import ADMIN, CLIENT, CLIENT_ADDRESS, MOTIVO_TOPE, avisos, pagar, pedir, plain


def stock(repo, sku):
    return repo.get_product(sku).stock


def last_order(repo):
    return repo.list_orders()[-1]


# 1) Pedido dentro de los parámetros → confirmado automáticamente
def test_pedido_confirmado(svc, repo):
    out = pedir(svc, CLIENT, "Hola, quiero 2 coca y 3 agua sin gas")
    assert len(out) == 1 and out[0].chat_id == CLIENT.id
    txt = plain(out[0].text)
    assert "¡Pedido completado! Queda pendiente de pago." in txt and "Pago: Efectivo al recibir · pendiente" in txt
    assert "2 × Coca-Cola 2.25L — $ 3.200 c/u = $ 6.400" in txt
    assert "3 × Agua Mineral sin Gas 2L — $ 1.100 c/u = $ 3.300" in txt
    assert "envío incluido, sin costo" in txt
    assert f"Dirección: {CLIENT_ADDRESS}" in txt
    assert "Total: $ 9.700" in txt
    # stock descontado
    assert stock(repo, "COC1") == 48 and stock(repo, "AGU1") == 27
    # registrado en la base de pedidos, con dirección y envío sin costo
    o = last_order(repo)
    assert o.status == ESTADO_CONFIRMADO and o.total == 9700 and o.customer_id == CLIENT.id
    assert o.address == CLIENT_ADDRESS and o.shipping_cost == 0 and o.channel == "telegram"
    assert o.created_at and o.original_text == "Hola, quiero 2 coca y 3 agua sin gas"
    assert [(l.sku, l.qty, l.unit_price) for l in o.lines] == [("COC1", 2, 3200), ("AGU1", 3, 1100)]


# 2) Falta stock → el cliente NO puede confirmar: se le dice cuánto queda y se ajusta
def test_faltante_no_se_puede_pedir_y_se_ajusta(svc, repo, settings):
    settings.rules.max_order_total = 0  # sin tope: este caso es solo de stock
    out = svc.handle_message(CLIENT, "mandame 10 cervezas y 2 coca")
    txt = plain(out[0].text)
    assert "No me alcanza el stock" in txt and "Cerveza Rubia 1L: pediste 10, me quedan 5." in txt
    assert "5 × Cerveza Rubia 1L" in txt and "2 × Coca-Cola 2.25L" in txt  # cómo quedaría
    assert [t for t, _ in out[0].buttons] == ["🔧 Ajustar a lo que hay", "❌ Cancelar"]  # sin «Confirmar»
    assert repo.list_orders() == [] and stock(repo, "CER1") == 5

    out = svc.handle_message(CLIENT, "/confirmar")  # con faltante, confirmar = aceptar el ajuste
    assert "lo ajusté a lo que hay" in plain(out[0].text) and "Revisá tu pedido" in plain(out[0].text)
    assert repo.list_orders() == []
    svc.handle_message(CLIENT, "si")  # ahora sí confirma
    pagar(svc, CLIENT)                 # y elige cómo paga
    o = last_order(repo)
    assert o.status == ESTADO_CONFIRMADO and [(l.sku, l.qty) for l in o.lines] == [("CER1", 5), ("COC1", 2)]
    assert stock(repo, "CER1") == 0
    # Sin stock, ni siquiera se nombra: no se puede pedir
    txt = plain(svc.handle_message(CLIENT, "1 cerveza")[0].text)
    assert "No tengo eso ahora" in txt and "Cerveza" not in txt


# 2b) Admin: pedido en revisión (supera el total máximo) que al aprobar ya no tiene stock
def test_aprobar_revisa_stock_y_precio(svc, repo):
    out = pedir(svc, CLIENT, "1 fernet")
    assert len(out) == 1 and out[0].chat_id == CLIENT.id  # al admin no le llega nada por chat
    order_id = last_order(repo).order_id
    aviso = avisos(repo)[0]
    assert aviso.kind == "revision" and aviso.order_id == order_id and MOTIVO_TOPE in aviso.detail
    assert last_order(repo).status == ESTADO_REVISION and stock(repo, "FER1") == 10

    assert svc.set_stock("FER1", 0).ok
    assert svc.set_price("FER1", 13000).ok
    r = svc.approve(order_id, ADMIN)
    assert not r.ok and "hay 0, se piden 1" in r.message and r.notify == []
    assert svc.add_stock("FER1", 4).ok
    r = svc.approve(order_id, ADMIN)
    assert r.ok and r.notify[0].chat_id == CLIENT.id and r.notify[0].channel == "telegram"
    assert "¡Tu pedido fue aprobado! Queda pendiente de pago." in plain(r.notify[0].text)
    assert "Total: $ 13.000" in plain(r.notify[0].text) and "efectivo" in plain(r.notify[0].text)
    assert stock(repo, "FER1") == 3 and last_order(repo).reviewed_by == ADMIN
    assert "ya está confirmado" in svc.approve(order_id, ADMIN).message


# 3) Los precios no vencen; al aprobar se toma el precio actual del catálogo
def test_aprobar_toma_el_precio_actual(svc, repo):
    pedir(svc, CLIENT, "1 fernet")
    oid = last_order(repo).order_id
    assert last_order(repo).total == 12000
    assert svc.set_price("FER1", 13000).ok      # el admin lo actualiza en la web
    r = svc.approve(oid, ADMIN)
    assert r.ok and "Total: $ 13.000" in plain(r.notify[0].text)
    assert last_order(repo).total == 13000 and stock(repo, "FER1") == 9
    # Un producto sin precio cargado no se aprueba
    pedir(svc, CLIENT, "1 fernet")
    oid2 = last_order(repo).order_id
    repo.set_price("FER1", 0, "s")
    assert "no tiene precio cargado" in svc.approve(oid2, ADMIN).message


# 4) Rechazo con motivo
def test_rechazo(svc, repo):
    pedir(svc, CLIENT, "1 fernet")
    oid = last_order(repo).order_id
    assert "Escribí el motivo" in svc.reject(oid, ADMIN, "  ").message
    r = svc.reject(oid, ADMIN, "Sin stock hasta el lunes")
    assert r.ok and r.notify[0].chat_id == CLIENT.id and "Sin stock hasta el lunes" in plain(r.notify[0].text)
    o = last_order(repo)
    assert o.status == ESTADO_RECHAZADO and o.reason == "Sin stock hasta el lunes"


# 5) Stock (acciones del admin en la web)
def test_actualizacion_de_stock(svc, repo):
    assert svc.set_stock("COC1", 120).message == "Stock de Coca-Cola 2.25L: 120 unidades."
    assert stock(repo, "COC1") == 120
    assert "115 unidades (-5)" in svc.add_stock("COC1", -5).message
    assert "negativo" in svc.add_stock("COC1", -500).message
    assert "No existe el producto" in svc.set_stock("XXX", 3).message
    assert not svc.set_stock("COC1", -1).ok and not svc.add_stock("COC1", 0).ok
    # El chat ya no tiene comandos de admin
    assert "No conozco ese comando" in svc.handle_message(CLIENT, "/setstock COC1 0")[0].text
    assert "No conozco ese comando" in svc.handle_message(CLIENT, "/aprobar P1")[0].text
    assert stock(repo, "COC1") == 115


# 6) Errores comunes: nada se registra y se pide aclaración
def test_errores_de_interpretacion(svc, repo):
    cases = {
        "3 agua": "¿cuál de estos? Agua Mineral con Gas 2L, Agua Mineral sin Gas 2L",
        "2 whisky": "No tengo eso ahora",
        "2 vino": "No tengo eso ahora",              # agotado: ni lo nombra
        "2,5 coca": "la cantidad debe ser un número entero entre 1 y 1000",
        "-3 sprite": "la cantidad debe ser un número entero",
        "5000 sprite": "entre 1 y 1000",
        "quiero coca": "¿cuántas unidades de Coca-Cola 2.25L?",
        "hola": "¡Hola",                             # un saludo recibe la bienvenida
    }
    for msg, expected in cases.items():
        out = pedir(svc, CLIENT, msg)
        assert expected in plain(out[0].text), (msg, out[0].text)
        assert "whisky" not in plain(out[0].text).lower() and "vino" not in plain(out[0].text).lower()
    assert repo.list_orders() == [] and stock(repo, "COC1") == 50


# 6b) Lo que no hay no se nombra: se sigue con lo que sí hay
def test_lo_agotado_no_se_nombra(svc, repo):
    txt = plain(svc.handle_message(CLIENT, "2 vino y 3 coca")[0].text)
    assert "3 × Coca-Cola 2.25L" in txt and "Del resto que pediste no tengo ahora." in txt
    assert "Vino" not in txt and "vino" not in txt
    txt = plain(svc.handle_message(CLIENT, "2 coca y 1 whisky")[0].text)
    assert "2 × Coca-Cola 2.25L" in txt and "Del resto que pediste no tengo ahora." in txt and "whisky" not in txt
    # Agotado a medias: lo que queda sí se nombra, con la cantidad
    txt = plain(svc.handle_message(CLIENT, "8 cerveza y 2 vino")[0].text)
    assert "Cerveza Rubia 1L: pediste 8, me quedan 5." in txt and "Vino" not in txt


# 7) Falla de la base de datos: mensaje claro al cliente, alerta al admin, nada a medias
def test_falla_de_base_de_datos(svc, repo, monkeypatch):
    def boom(*a, **k):
        raise StorageError("La base de datos está ocupada")
    monkeypatch.setattr(repo, "list_products", boom)
    out = pedir(svc, CLIENT, "2 coca")
    assert len(out) == 1 and "No pude acceder a la base de datos" in out[0].text
    falla = avisos(repo)[0]
    assert falla.kind == "falla" and "La base de datos está ocupada" in falla.detail


def test_falla_al_registrar_revierte_stock(svc, repo, monkeypatch):
    def boom(order):
        raise StorageError("timeout")
    monkeypatch.setattr(repo, "add_order", boom)
    pedir(svc, CLIENT, "2 coca")
    assert stock(repo, "COC1") == 50  # se devolvió el stock


# 8) Reglas configurables
def test_regla_producto_desconocido_a_revision(svc, repo, settings):
    settings.rules.on_unknown_product = "review"
    pedir(svc, CLIENT, "2 coca y 1 whisky")
    assert last_order(repo).status == ESTADO_REVISION
    assert "Producto no encontrado: «whisky»" in avisos(repo)[0].detail


def test_regla_total_maximo(svc, repo, settings):
    settings.rules.max_order_total = 10000
    pedir(svc, CLIENT, "4 coca")  # 12.800 > 10.000
    assert last_order(repo).status == ESTADO_REVISION and stock(repo, "COC1") == 50


def test_mismo_producto_dos_veces_se_suma(svc, repo, settings):
    settings.rules.max_order_total = 0
    pedir(svc, CLIENT, "2 coca, 3 coca")
    o = last_order(repo)
    assert [(l.sku, l.qty) for l in o.lines] == [("COC1", 5)] and stock(repo, "COC1") == 45


def test_comandos_cliente(svc, repo):
    pedir(svc, CLIENT, "1 sprite")
    assert "Tus últimos pedidos" in svc.handle_message(CLIENT, "/mispedidos")[0].text
    cat = plain(svc.handle_message(CLIENT, "/catalogo")[0].text)
    assert "Vino Tinto Malbec" not in cat and "sin stock" not in cat and "Fernet 750ml — $ 12.000" in cat
    assert "Gaseosas" in cat and "Envío incluido, sin costo" in cat

