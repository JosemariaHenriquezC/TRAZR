"""Web de administración: rutas, login, CSRF y acciones (sin navegador ni APIs reales)."""
import re

import pytest
from fastapi.testclient import TestClient

from app.notifier import Notifier
from app.web.main import create_app

from .conftest import CLIENT, pedir


class FakeTelegram:
    def __init__(self):
        self.sent = []

    async def __call__(self, m):
        self.sent.append(m)


@pytest.fixture
def tg():
    return FakeTelegram()


@pytest.fixture
def client(svc, settings, tg):
    settings.secret_key = "clave-de-prueba"
    notifier = Notifier()
    notifier.register("telegram", tg)
    assert svc.create_admin("lucia", "Lucía Fernández", "limones123").ok
    return TestClient(create_app(svc, notifier, settings))


def csrf(html: str) -> str:
    return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)


def login(client, user="lucia", password="limones123"):
    token = csrf(client.get("/ingresar").text)
    return client.post("/ingresar", data={"usuario": user, "clave": password, "csrf_token": token},
                       follow_redirects=False)


@pytest.fixture
def admin(client):
    assert login(client).status_code == 303
    return client


def text(resp) -> str:
    return re.sub(r"\s+", " ", resp.text)


# ---------------------------------------------------------------- acceso
def test_todo_pide_ingresar(client):
    for url in ["/", "/pedidos", "/productos", "/clientes", "/pedidos/P1"]:
        r = client.get(url, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/ingresar", url
    assert client.get("/salud").text == "ok"


def test_login_correcto_e_incorrecto(client):
    r = login(client, password="mal")
    assert r.status_code == 200 and "Usuario o contraseña incorrectos" in r.text
    r = login(client)
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert "Lucía" in client.get("/").text
    page = client.get("/").text
    client.post("/salir", data={"csrf_token": csrf(page)})
    assert client.get("/", follow_redirects=False).status_code == 303


def test_sin_token_csrf_no_hace_nada(admin, svc, repo):
    r = admin.post("/productos/COC1/sumar", data={"cantidad": "10", "csrf_token": "falso"}, follow_redirects=False)
    assert r.status_code == 303 and repo.get_product("COC1").stock == 50


def test_la_cookie_es_httponly(client):
    r = login(client)
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie


# ---------------------------------------------------------------- tablero
def test_tablero(admin, svc, repo):
    pedir(svc, CLIENT, "2 coca")
    pedir(svc, CLIENT, "1 fernet")
    t = text(admin.get("/"))
    assert "Hay 1 pedido esperando tu revisión" in t
    assert "$ 6.400,00" in t                                   # ventas de hoy
    assert "Precio no vigente: Fernet 750ml" in t             # en «Para revisar» y en avisos
    assert "Vino Tinto Malbec 750ml" in t and "Sin stock" in t  # poco stock
    assert 'class="count"' in t                                # contador en el menú
    page = admin.get("/").text
    admin.post("/avisos/leidos", data={"csrf_token": csrf(page)})
    assert repo.list_notices(unread_only=True) == []


# ---------------------------------------------------------------- pedidos
def test_aprobar_con_precio_nuevo_avisa_al_cliente(admin, svc, repo, tg):
    pedir(svc, CLIENT, "1 fernet")
    oid = repo.list_orders()[-1].order_id
    t = text(admin.get("/pedidos"))
    assert oid in t and "En revisión · 1" in t
    page = admin.get(f"/pedidos/{oid}").text
    assert "Por qué está en revisión" in page and 'name="precio_FER1"' in page
    assert re.search(r"Total</strong></td><td class=\"num\"><strong>\$ 0,00", page)  # sin el precio a confirmar
    r = admin.post(f"/pedidos/{oid}/aprobar", data={"csrf_token": csrf(page), "precio_FER1": "13.000,00"})
    assert "aprobado: se descontó el stock y se avisó al cliente" in r.text
    o = repo.get_order(oid)
    assert o.status == "CONFIRMADO" and o.reviewed_by == "Lucía Fernández" and o.total == 13000
    assert len(tg.sent) == 1 and tg.sent[0].chat_id == CLIENT.id and "¡Tu pedido fue confirmado!" in tg.sent[0].text
    assert "Aprobado por Lucía Fernández" in r.text and "Aprobar y avisar" not in r.text


def test_aprobar_bloqueado_por_stock(admin, svc, repo, tg):
    pedir(svc, CLIENT, "1 fernet")
    oid = repo.list_orders()[-1].order_id
    svc.set_stock("FER1", 0)
    page = admin.get(f"/pedidos/{oid}").text
    assert "Hay 0" in page
    r = admin.post(f"/pedidos/{oid}/aprobar", data={"csrf_token": csrf(page), "precio_FER1": "13000"})
    assert "No se puede aprobar todavía" in r.text and repo.get_order(oid).status == "EN REVISION"
    assert tg.sent == []


def test_rechazar_con_motivo(admin, svc, repo, tg):
    pedir(svc, CLIENT, "1 fernet")
    oid = repo.list_orders()[-1].order_id
    page = admin.get(f"/pedidos/{oid}").text
    r = admin.post(f"/pedidos/{oid}/rechazar", data={"csrf_token": csrf(page), "motivo": " "})
    assert "Escribí el motivo" in r.text
    r = admin.post(f"/pedidos/{oid}/rechazar", data={"csrf_token": csrf(page), "motivo": "Sin Fernet hasta el lunes"})
    assert "rechazado" in r.text and repo.get_order(oid).status == "RECHAZADO"
    assert "Sin Fernet hasta el lunes" in tg.sent[0].text
    assert "Rechazados · 1" in text(admin.get("/pedidos?estado=rechazados"))


def test_buscar_y_pedido_inexistente(admin, svc, repo):
    pedir(svc, CLIENT, "1 fernet")
    assert repo.list_orders()[-1].order_id in admin.get("/pedidos?estado=todos&q=fernet").text
    assert "No hay pedidos que coincidan" in admin.get("/pedidos?estado=todos&q=whisky").text
    assert "No encontré el pedido" in admin.get("/pedidos/NOEXISTE").text


def test_html_escapado(admin, svc, repo):
    from app.service import User
    pedir(svc, User(77, "<script>alert(1)</script>"), "/direccion Calle 123, Tucumán")
    pedir(svc, User(77, "<script>alert(1)</script>"), "1 fernet")
    page = admin.get("/pedidos").text
    assert "<script>alert(1)</script>" not in page and "&lt;script&gt;" in page


# ---------------------------------------------------------------- productos
def test_sumar_stock_y_filtros(admin, repo):
    page = admin.get("/productos").text
    r = admin.post("/productos/CER1/sumar", data={"csrf_token": csrf(page), "cantidad": "24"})
    assert "Cerveza Rubia 1L: 29 unidades (+24)" in r.text and repo.get_product("CER1").stock == 29
    r = admin.post("/productos/CER1/sumar", data={"csrf_token": csrf(page), "cantidad": "2,5"})
    assert "cantidad entera" in r.text
    assert "Fernet 750ml" in admin.get("/productos?filtro=vencido").text
    assert "Coca-Cola" not in admin.get("/productos?filtro=vencido").text


def test_alta_y_edicion_de_producto(admin, repo):
    page = admin.get("/productos?nuevo=1").text
    data = {"csrf_token": csrf(page), "es_nuevo": "1", "sku": "pep1", "nombre": "Pepsi 2.25L",
            "alias": "pepsi, pecsi", "categoria": "Gaseosas", "precio": "2.900,50", "vigente_hasta": "",
            "stock": "24", "activo": "1"}
    r = admin.post("/productos/guardar", data=data)
    assert "Producto creado: Pepsi 2.25L" in r.text
    p = repo.get_product("PEP1")
    assert p.price == 2900.5 and p.aliases == ["pepsi", "pecsi"] and p.valid_until is None
    r = admin.post("/productos/guardar", data=data)
    assert "Ya existe" in r.text
    page = admin.get("/productos?editar=PEP1").text
    assert "Editar Pepsi 2.25L" in page and 'value="2900,50"' in page
    edit = dict(data, csrf_token=csrf(page), es_nuevo="0", sku="PEP1", precio="3100", activo="0",
                vigente_hasta="2030-01-31")
    assert "Cambios guardados" in admin.post("/productos/guardar", data=edit).text
    p = repo.get_product("PEP1")
    assert p.price == 3100 and not p.active and p.valid_until.isoformat() == "2030-01-31"
    bad = dict(edit, precio="mucho")
    assert "precio válido" in admin.post("/productos/guardar", data=bad).text


# ---------------------------------------------------------------- clientes
def test_clientes(admin, svc, repo):
    pedir(svc, CLIENT, "2 coca")
    t = text(admin.get("/clientes"))
    assert CLIENT.name in t and "Av. Mate de Luna 1234" in t and "$ 6.400,00" in t
    assert "Coca-Cola 2.25L" in t   # compra típica
    assert "No hay clientes que coincidan" in admin.get("/clientes?q=zzzz").text
