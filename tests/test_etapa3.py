"""Etapa 3: «lo de siempre», saludo con nombre y catálogo, «sale hoy» y resumen compacto."""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.config import Business
from app.service import User, _wants_repeat

from .conftest import CLIENT, button, plain
from .test_gemini_en_el_bot import _FakeModels, _svc, pedido

TZ = ZoneInfo("UTC")


def labels(out):
    return [t for m in out for t, _ in m.buttons]


def _comprar(svc, texto="2 coca", medio="pe:"):
    out = svc.handle_message(CLIENT, texto)
    out = svc.handle_callback(CLIENT, button(out, "cf:"))
    return svc.handle_callback(CLIENT, button(out, medio))


# ---------------------------------------------------------------- cuándo sale
@pytest.mark.parametrize("cuando,esperado", [
    (datetime(2026, 11, 13, 10, 0, tzinfo=TZ), "hoy a la mañana (9 a 13)"),        # viernes antes de las 11
    (datetime(2026, 11, 13, 11, 0, tzinfo=TZ), "hoy a la tarde (17 a 21)"),        # pasó el corte de la mañana
    (datetime(2026, 11, 13, 14, 30, tzinfo=TZ), "hoy a la tarde (17 a 21)"),       # siesta: sale a la tarde
    (datetime(2026, 11, 13, 19, 0, tzinfo=TZ), "mañana a la mañana (9 a 13)"),     # pasó el corte de la tarde
    (datetime(2026, 11, 14, 18, 59, tzinfo=TZ), "hoy a la tarde (17 a 21)"),       # sábado antes de las 19
    (datetime(2026, 11, 14, 20, 0, tzinfo=TZ), "el lunes 16/11 a la mañana (9 a 13)"),  # sábado tarde → lunes
    (datetime(2026, 11, 15, 10, 0, tzinfo=TZ), "mañana a la mañana (9 a 13)"),     # domingo → lunes
])
def test_cuando_sale_el_pedido(svc, cuando, esperado):
    assert svc.ship_day(cuando) == esperado


def test_el_resumen_y_el_comprobante_dicen_cuando_sale(svc, repo):
    out = svc.handle_message(CLIENT, "2 coca")
    assert f"Si lo confirmás ahora, sale {svc.ship_day()}." in plain(out[0].text)
    out = _comprar(svc, "1 fanta")
    assert f"Sale {svc.ship_day()}." in plain(out[0].text)


# ---------------------------------------------------------------- formato compacto
def test_plata_sin_centavos_cuando_es_redondo(svc):
    assert svc.money(3200) == "$ 3.200" and svc.money(3200.5) == "$ 3.200,50" and svc.money(0) == "$ 0"


def test_comprobante_compacto(svc, repo):
    txt = plain(_comprar(svc)[0].text)
    assert "Estado:" not in txt and "Fecha:" not in txt
    assert "Pago: Efectivo al recibir · pendiente" in txt and "Total: $ 6.400 · envío incluido, sin costo" in txt
    assert "2026-" not in txt     # la fecha va como la lee una persona


# ---------------------------------------------------------------- saludo
def test_saludo_a_un_cliente_nuevo_ofrece_el_catalogo(svc):
    nuevo = User(7007, "Almacén Don Pepe")
    out = svc.handle_message(nuevo, "hola")
    assert "¡Hola, Almacén Don Pepe!" in plain(out[0].text) and labels(out) == ["📋 Ver catálogo"]
    out = svc.handle_callback(nuevo, button(out, "ct:"))
    assert "Catálogo de bebidas" in plain(out[0].text)


def test_saludo_al_que_ya_compro(svc, repo):
    _comprar(svc)
    out = svc.handle_message(CLIENT, "buenas")
    assert f"¡Hola, {CLIENT.name}! 👋 ¿Qué te mando hoy?" in plain(out[0].text)
    assert labels(out) == ["📋 Ver catálogo", "🔁 Lo de siempre"]


def test_saludo_con_gemini_no_lo_llama(repo, settings):
    models = _FakeModels()
    svc = _svc(repo, settings, models)
    out = svc.handle_message(CLIENT, "hola")
    assert "¡Hola" in plain(out[0].text) and models.prompts == []


def test_hola_con_un_pedido_a_medio_armar_no_lo_pisa(svc, repo):
    svc.handle_message(CLIENT, "2 coca")
    svc.handle_message(CLIENT, "hola")
    assert repo.get_pending_order("telegram", str(CLIENT.id)) is not None


# ---------------------------------------------------------------- «lo de siempre»
@pytest.mark.parametrize("texto,repite", [("lo de siempre", True), ("lo mismo que la semana pasada", True),
                                          ("repetí mi último pedido", True), ("hola, lo de siempre", True),
                                          ("lo mismo pero sin fanta", False), ("lo de siempre y 2 cocas más", False),
                                          ("2 coca", False)])
def test_cuando_es_repetir(texto, repite):
    from app.checkout import _norm
    assert _wants_repeat(_norm(texto)) is repite


def test_lo_de_siempre_arma_el_ultimo_pedido_con_precios_de_hoy(svc, repo):
    _comprar(svc, "1 coca y 1 fanta")      # (por debajo del tope de revisión de los tests)
    repo.set_price("COC1", 3500, "2026-01-01 00:00:00")
    out = svc.handle_message(CLIENT, "lo de siempre")
    txt = plain(out[0].text)
    assert "Armé tu último pedido" in txt and "1 × Coca-Cola 2.25L — $ 3.500 c/u" in txt
    assert "1 × Fanta Naranja 2.25L" in txt and labels(out) == ["✅ Confirmar", "💳 Cambiar pago", "❌ Cancelar"]
    out = svc.handle_message(CLIENT, "dale")
    assert "¡Pedido completado!" in plain(out[0].text) and len(repo.list_orders()) == 2


def test_lo_de_siempre_desde_el_boton_del_saludo(svc, repo):
    _comprar(svc)
    out = svc.handle_message(CLIENT, "hola")
    out = svc.handle_callback(CLIENT, button(out, "rp:"))
    assert "Armé tu último pedido" in plain(out[0].text)


def test_lo_de_siempre_sin_pedidos_anteriores(svc, repo):
    out = svc.handle_message(CLIENT, "lo de siempre")
    assert "Todavía no tenés pedidos para repetir" in plain(out[0].text) and labels(out) == ["📋 Ver catálogo"]


def test_lo_de_siempre_no_nombra_lo_agotado(svc, repo):
    _comprar(svc, "2 coca y 3 fanta")
    repo.set_stock("FAN1", 0, "2026-01-01 00:00:00")
    out = svc.handle_message(CLIENT, "lo de siempre")
    assert "Fanta" not in plain(out[0].text)


def test_horarios_nuevos_por_defecto():
    from app.config import TRAZR
    assert "lunes a sábado de 9 a 13 y de 17 a 21" in TRAZR["NEGOCIO_HORARIOS"]
    b = Business()
    assert b.close_hour == 21 and b.delivery_days == (0, 1, 2, 3, 4, 5)
    assert (b.morning_cutoff, b.cutoff_hour, b.morning_shift, b.evening_shift) == (11, 19, "9 a 13", "17 a 21")


def test_dias_de_reparto_desde_el_env(monkeypatch):
    from app.config import _days
    monkeypatch.setenv("X_DIAS", "lun-vie")
    assert _days("X_DIAS", ()) == (0, 1, 2, 3, 4)
    monkeypatch.setenv("X_DIAS", "lun,mie,sab")
    assert _days("X_DIAS", ()) == (0, 2, 5)


# ---------------------------------------------------------------- catálogo por categorías
def _catalogo_grande(repo):
    from app.models import Product
    nuevos = [("WHI1", "Whisky Escocés 750ml", "Whisky"), ("GIN1", "Gin London Dry 700ml", "Gin"),
              ("VOD1", "Vodka 700ml", "Vodka"), ("RON1", "Ron Blanco 750ml", "Ron"),
              ("LIC1", "Licor de Café 700ml", "Licores"), ("ESP1", "Espumante Extra Brut 750ml", "Espumante"),
              ("COM1", "Combo Fernet + 2 Cocas", "Combos"), ("REG1", "Estuche Malbec x2", "Regalería")]
    for sku, name, cat in nuevos:
        repo.upsert_product(Product(sku, name, [], 20, 9000, True, cat), "2026-01-01 00:00:00")


def test_catalogo_chico_va_entero(svc):
    out = svc.handle_message(CLIENT, "catálogo")
    assert "Catálogo de bebidas" in plain(out[0].text) and "Coca-Cola 2.25L — $ 3.200 · pack × 6" in plain(out[0].text)
    assert labels(out) == []


def test_catalogo_grande_primero_los_grupos(svc, repo):
    _catalogo_grande(repo)
    out = svc.handle_message(CLIENT, "catálogo")
    txt = plain(out[0].text)
    assert "¿Qué querés ver?" in txt and "• Whisky, gin, vodka, ron (4)" in txt
    assert "Vinos y espumantes (1)" in txt                     # el Malbec está agotado: solo el espumante
    assert labels(out) == ["Vinos y espumantes", "Cervezas", "Fernet y aperitivos", "Whisky, gin, vodka, ron",
                           "Licores", "Sin alcohol", "Combos y regalería"]
    assert out[0].menu_label == "Ver categorías"
    out = svc.handle_callback(CLIENT, button(out, "cc:whisky gin vodka ron"))
    txt = plain(out[0].text)
    assert txt.startswith("Whisky, gin, vodka, ron") and "Gin London Dry" in txt and "Ron Blanco" in txt
    assert txt.index("Whisky Escocés") < txt.index("Gin London") < txt.index("Vodka 700") < txt.index("Ron Blanco")
    assert labels(out) == ["📋 Otra categoría"]


@pytest.mark.parametrize("texto,esperado", [("gin", "Gin London Dry"), ("Sin alcohol", "Coca-Cola 2.25L"),
                                            ("regalería", "Estuche Malbec x2")])
def test_escribir_la_categoria_o_el_grupo_lo_muestra(svc, repo, texto, esperado):
    _catalogo_grande(repo)
    out = svc.handle_message(CLIENT, texto)
    assert esperado in plain(out[0].text)


def test_en_whatsapp_las_categorias_van_como_lista(svc, repo):
    from app.whatsapp import build_payloads
    _catalogo_grande(repo)
    [m] = svc.handle_message(CLIENT, "catálogo")
    [p] = build_payloads(m)
    assert p["interactive"]["type"] == "list" and p["interactive"]["action"]["button"] == "Ver categorías"
    rows = p["interactive"]["action"]["sections"][0]["rows"]
    assert len(rows) <= 10 and all(len(r["title"]) <= 24 for r in rows)


def test_categorias_y_grupos():
    from app.categorias import CATEGORIAS, GRUPOS, grupo_de
    assert len(CATEGORIAS) == 15 and len(GRUPOS) <= 10 and all(len(n) <= 24 for n, _ in GRUPOS)
    agrupadas = [c for _, cats in GRUPOS for c in cats]
    assert sorted(agrupadas) == sorted(CATEGORIAS)            # cada categoría en un solo grupo
    assert grupo_de("regaleria") == "Combos y regalería" and grupo_de("Cervezas artesanales") == "Cervezas artesanales"


# ---------------------------------------------------------------- un mensaje por paso
def test_confirmacion_y_pregunta_de_direccion_en_un_mensaje(svc, repo):
    out = svc.handle_message(CLIENT, "2 coca")
    out = svc.handle_message(CLIENT, "/direccion Belgrano 300, Tafí Viejo")
    out = svc.handle_callback(CLIENT, button(out, "cf:"))
    out = svc.handle_callback(CLIENT, button(out, "pe:"))
    assert len(out) == 1
    txt = plain(out[0].text)
    assert "¡Pedido completado!" in txt and "¿Qué dirección dejo guardada" in txt
    assert labels(out) == ["🏠 La de siempre", "📍 La nueva"]


@pytest.mark.parametrize("texto", ["pasame el catálogo", "me pasás el catalogo?", "¿qué tienen?",
                                   "el catálogo por favor", "quiero ver la lista de precios"])
def test_pedir_el_catalogo_con_cualquier_frase(svc, texto):
    out = svc.handle_message(CLIENT, texto)
    assert "Catálogo de bebidas" in plain(out[0].text)


@pytest.mark.parametrize("texto,esperado", [("¿qué cervezas tienen?", "Cerveza Rubia 1L"),
                                            ("mostrame el fernet", "Fernet 750ml"),
                                            ("cuáles energizantes tienen", "Bebida Energizante")])
def test_pedir_una_categoria_con_una_frase(svc, texto, esperado):
    txt = plain(svc.handle_message(CLIENT, texto)[0].text)
    assert esperado in txt and "Coca-Cola" not in txt


def test_un_pedido_con_cantidad_no_es_pedir_el_catalogo(svc):
    assert "Revisá tu pedido" in plain(svc.handle_message(CLIENT, "2 cervezas")[0].text)


def test_si_gemini_promete_el_catalogo_lo_manda(repo, settings):
    from .test_gemini_en_el_bot import intent
    models = _FakeModels(intent("charla", "¡Claro! Te paso el catálogo 👇"))
    svc = _svc(repo, settings, models)
    out = svc.handle_message(CLIENT, "che, contame un poco del negocio")
    assert "Te paso el catálogo" in plain(out[0].text) and "Catálogo de bebidas" in plain(out[-1].text)
