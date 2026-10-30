"""
Prueba de punta a punta: mensajes reales → lógica del bot → hojas (.xlsx).
Mismo código que corre en Telegram; solo cambia el almacenamiento.
"""
from app.models import ESTADO_CONFIRMADO, ESTADO_RECHAZADO, ESTADO_REVISION
from app.storage.base import StorageError

from .conftest import ADMIN, CLIENT, plain


def stock(repo, sku):
    return repo.get_product(sku).stock


def last_order(repo):
    return repo.list_orders()[-1]


# 1) Pedido dentro de los parámetros → confirmado automáticamente
def test_pedido_confirmado(svc, repo):
    out = svc.handle_message(CLIENT, "Hola, quiero 2 yerba y 3 azúcar")
    assert len(out) == 1 and out[0].chat_id == CLIENT.id
    txt = plain(out[0].text)
    assert "¡Pedido confirmado!" in txt
    assert "2 × Yerba Mate 1kg — $ 4.500,00 c/u = $ 9.000,00" in txt
    assert "3 × Azúcar 1kg — $ 1.200,00 c/u = $ 3.600,00" in txt
    assert "Total: $ 12.600,00" in txt
    # stock descontado
    assert stock(repo, "YER1") == 48 and stock(repo, "AZU1") == 27
    # registrado en la hoja de pedidos
    o = last_order(repo)
    assert o.status == ESTADO_CONFIRMADO and o.total == 12600 and o.customer_id == CLIENT.id
    assert o.created_at and o.original_text == "Hola, quiero 2 yerba y 3 azúcar"
    assert [(l.sku, l.qty, l.unit_price) for l in o.lines] == [("YER1", 2, 4500), ("AZU1", 3, 1200)]


# 2) Faltante de stock → en revisión → admin repone y aprueba
def test_faltante_revision_y_aprobacion(svc, repo):
    out = svc.handle_message(CLIENT, "mandame 10 aceites")
    client_msg, admin_msg = out[0], out[1]
    assert client_msg.chat_id == CLIENT.id and "en revisión" in plain(client_msg.text)
    assert admin_msg.chat_id == ADMIN.id and "Stock insuficiente: Aceite de Girasol 1.5L (pedido 10, disponible 5)" in plain(admin_msg.text)
    order_id = last_order(repo).order_id
    assert admin_msg.buttons == [("✅ Aprobar", f"ap:{order_id}"), ("❌ Rechazar", f"rj:{order_id}")]
    assert last_order(repo).status == ESTADO_REVISION
    assert stock(repo, "ACE1") == 5  # NO se descuenta mientras está en revisión

    # Aprobar sin stock → bloqueado con instrucción
    out = svc.handle_message(ADMIN, f"/aprobar {order_id}")
    assert "No puedo aprobar todavía" in plain(out[0].text) and "/sumar ACE1" in plain(out[0].text)

    # Admin repone stock y aprueba con el botón
    out = svc.handle_message(ADMIN, "/sumar ACE1 10")
    assert "ahora: 15" in plain(out[0].text)
    out = svc.handle_callback(ADMIN, f"ap:{order_id}")
    assert plain(out[0].text).startswith("✅ Pedido") and out[1].chat_id == CLIENT.id
    assert "¡Tu pedido fue confirmado!" in plain(out[1].text) and "Total: $ 38.000,00" in plain(out[1].text)
    assert stock(repo, "ACE1") == 5
    o = last_order(repo)
    assert o.status == ESTADO_CONFIRMADO and o.reviewed_by.startswith("Admin") and "Stock insuficiente" in o.reason

    # No se puede aprobar dos veces
    out = svc.handle_callback(ADMIN, f"ap:{order_id}")
    assert "ya está" in plain(out[0].text) and stock(repo, "ACE1") == 5


# 3) Precio vencido → revisión → admin actualiza precio y aprueba con el precio nuevo
def test_precio_no_vigente(svc, repo):
    out = svc.handle_message(CLIENT, "1 cafe")
    assert "Precio no vigente: Café Molido 250g" in plain(out[1].text)
    oid = last_order(repo).order_id
    assert "precio no vigente" in plain(svc.handle_message(ADMIN, f"/aprobar {oid}")[0].text)
    assert "✅" in svc.handle_message(ADMIN, "/precio CAF1 5500")[0].text
    out = svc.handle_message(ADMIN, f"/aprobar {oid}")
    assert "Total: $ 5.500,00" in plain(out[1].text)
    assert last_order(repo).total == 5500 and stock(repo, "CAF1") == 9


# 4) Rechazo con motivo
def test_rechazo(svc, repo):
    svc.handle_message(CLIENT, "2 leche")
    oid = last_order(repo).order_id
    out = svc.handle_message(ADMIN, f"/rechazar {oid} Sin stock hasta el lunes")
    assert out[1].chat_id == CLIENT.id and "Sin stock hasta el lunes" in plain(out[1].text)
    o = last_order(repo)
    assert o.status == ESTADO_RECHAZADO and o.reason == "Sin stock hasta el lunes"


# 5) Consulta y actualización de stock (admin)
def test_consulta_y_actualizacion_stock(svc, repo):
    txt = plain(svc.handle_message(ADMIN, "/stock yerba")[0].text)
    assert "YER1 Yerba Mate 1kg: 50 u." in txt
    assert "Stock de Yerba Mate 1kg (YER1) actualizado a 120" in plain(svc.handle_message(ADMIN, "/setstock YER1 120")[0].text)
    assert stock(repo, "YER1") == 120
    assert "ahora: 115" in plain(svc.handle_message(ADMIN, "/sumar YER1 -5")[0].text)
    assert "negativo" in plain(svc.handle_message(ADMIN, "/sumar YER1 -500")[0].text)
    assert "No existe el SKU" in plain(svc.handle_message(ADMIN, "/setstock XXX 3")[0].text)
    assert "no es un número" in plain(svc.handle_message(ADMIN, "/setstock YER1 muchos")[0].text)
    # El cliente no puede usar comandos de admin
    assert "solo para el administrador" in svc.handle_message(CLIENT, "/setstock YER1 0")[0].text
    assert stock(repo, "YER1") == 115


# 6) Errores comunes: nada se registra y se pide aclaración
def test_errores_de_interpretacion(svc, repo):
    cases = {
        "3 galletitas": "¿cuál de estos? Galletitas Dulces 300g, Galletitas Saladas 300g",
        "2 caviar": "«caviar»: no está en nuestro catálogo",
        "2,5 yerba": "la cantidad debe ser un número entero entre 1 y 1000",
        "-3 arroz": "la cantidad debe ser un número entero",
        "5000 arroz": "entre 1 y 1000",
        "quiero yerba": "¿cuántas unidades de Yerba Mate 1kg?",
        "hola": "No encontré un pedido en tu mensaje",
        "2 yerba y 1 caviar": "«caviar»: no está en nuestro catálogo",
    }
    for msg, expected in cases.items():
        out = svc.handle_message(CLIENT, msg)
        assert expected in plain(out[0].text), (msg, out[0].text)
    assert repo.list_orders() == [] and stock(repo, "YER1") == 50


# 7) Falla de acceso a la hoja: mensaje claro al cliente, alerta al admin, nada a medias
def test_falla_de_hoja(svc, repo, monkeypatch):
    def boom(*a, **k):
        raise StorageError("Sin permiso sobre la hoja")
    monkeypatch.setattr(repo, "list_products", boom)
    out = svc.handle_message(CLIENT, "2 yerba")
    assert "No pude acceder a la planilla" in out[0].text
    assert out[1].chat_id == ADMIN.id and "Sin permiso sobre la hoja" in out[1].text


def test_falla_al_registrar_revierte_stock(svc, repo, monkeypatch):
    def boom(order):
        raise StorageError("timeout")
    monkeypatch.setattr(repo, "add_order", boom)
    svc.handle_message(CLIENT, "2 yerba")
    assert stock(repo, "YER1") == 50  # se devolvió el stock


# 8) Reglas configurables
def test_regla_producto_desconocido_a_revision(svc, repo, settings):
    settings.rules.on_unknown_product = "review"
    out = svc.handle_message(CLIENT, "2 yerba y 1 caviar")
    assert "Producto no encontrado: «caviar»" in plain(out[1].text)
    assert last_order(repo).status == ESTADO_REVISION


def test_regla_total_maximo(svc, repo, settings):
    settings.rules.max_order_total = 10000
    svc.handle_message(CLIENT, "3 yerba")  # 13.500 > 10.000
    assert last_order(repo).status == ESTADO_REVISION and stock(repo, "YER1") == 50


def test_mismo_producto_dos_veces_se_suma(svc, repo):
    svc.handle_message(CLIENT, "2 yerba, 3 yerba")
    o = last_order(repo)
    assert [(l.sku, l.qty) for l in o.lines] == [("YER1", 5)] and stock(repo, "YER1") == 45


def test_comandos_cliente(svc, repo):
    svc.handle_message(CLIENT, "1 arroz")
    assert "Tus últimos pedidos" in svc.handle_message(CLIENT, "/mispedidos")[0].text
    cat = plain(svc.handle_message(CLIENT, "/catalogo")[0].text)
    assert "Leche Entera 1L — $ 1.300,00 ❌ sin stock" in cat and "Café Molido 250g — precio a confirmar" in cat
    assert "Hay 0" not in plain(svc.handle_message(ADMIN, "/revision")[0].text)
