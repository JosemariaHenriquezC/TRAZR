"""El bot completo con Gemini conversacional (SIMULADO: nunca se llama a la API real).

Límite que se prueba acá: Gemini entiende y redacta, pero precios, stock,
totales y estados los pone el bot desde la base de datos.
"""
import json
from types import SimpleNamespace

from app.ai_parser import AIConfig, respuesta_segura
from app.config import Business
from app.models import ESTADO_CONFIRMADO
from app.service import BotService

from .conftest import ADMIN, CLIENT, plain


class _FakeModels:
    """respuestas: lista de dicts (una por llamada) o una Exception."""

    def __init__(self, *respuestas, error=None):
        self.respuestas, self.error, self.prompts = list(respuestas), error, []

    async def generate_content(self, model, **kwargs):
        self.prompts.append(kwargs.get("contents", ""))
        if self.error:
            raise self.error
        return SimpleNamespace(text=json.dumps(self.respuestas.pop(0)))


def _svc(repo, settings, models, **business):
    settings.ai = AIConfig(enabled=True, api_key="falsa", model="m", timeout=5)
    settings.business = Business(**business)
    return BotService(repo, settings, ai_client=SimpleNamespace(aio=SimpleNamespace(models=models)))


def pedido(items, respuesta="¡Dale, te lo preparo!"):
    return {"intencion": "pedido", "respuesta": respuesta,
            "items": [{"sku": s, "texto": t, "cantidad": q} for s, t, q in items]}


def test_entiende_jerga_y_responde_como_persona(repo, settings):
    models = _FakeModels(pedido([("COC1", "un par de gaseosas cola grandes", 2),
                                 ("CER1", "una rubia", 1)], "¡Buenísimo, Juana! Ya te lo armo 🍻"))
    out = _svc(repo, settings, models).handle_message(CLIENT, "pasame un par de gaseosas cola grandes y una rubia")
    txt = plain(out[0].text)
    assert txt.startswith("¡Buenísimo, Juana! Ya te lo armo 🍻")
    assert "¡Pedido confirmado!" in txt and "2 × Coca-Cola 2.25L" in txt and "1 × Cerveza Rubia 1L" in txt
    # Gemini ve el catálogo de la base, pero sin precios
    assert "COC1 | Coca-Cola 2.25L" in models.prompts[0] and "3200" not in models.prompts[0]
    o = repo.list_orders()[-1]
    assert o.status == ESTADO_CONFIRMADO and o.total == 9200  # precios de la base, no de la IA


def test_recuerda_la_conversacion(repo, settings):
    models = _FakeModels(
        {"intencion": "charla", "respuesta": "¿Querés el agua con gas o sin gas?"},
        pedido([("AGU2", "3 agua con gas", 3)]),
    )
    svc = _svc(repo, settings, models)
    assert plain(svc.handle_message(CLIENT, "3 aguas")[0].text) == "¿Querés el agua con gas o sin gas?"
    out = svc.handle_message(CLIENT, "con gas")
    assert "3 × Agua Mineral con Gas 2L" in plain(out[0].text)
    # La segunda vez Gemini recibió lo que se habló antes
    assert "CLIENTE: 3 aguas" in models.prompts[1] and "VOS: ¿Querés el agua con gas" in models.prompts[1]


def test_consulta_de_precio_los_numeros_los_pone_el_bot(repo, settings):
    models = _FakeModels({"intencion": "consulta", "respuesta": "¡Sí! Mirá:",
                          "consulta_skus": ["FER1", "VIN1", "INVENTADO"]})
    txt = plain(_svc(repo, settings, models).handle_message(CLIENT, "tenés fernet y vino? a cuánto?")[0].text)
    assert txt.startswith("¡Sí! Mirá:")
    assert "Fernet 750ml — precio a confirmar ✅ disponible" in txt   # precio vencido en la base
    assert "Vino Tinto Malbec 750ml — $ 6.500,00 ❌ sin stock por ahora" in txt
    assert "INVENTADO" not in txt and repo.list_orders() == []


def test_si_gemini_escribe_precios_se_descarta_su_texto(repo, settings):
    models = _FakeModels({"intencion": "consulta", "respuesta": "La coca sale $100, re barata",
                          "consulta_skus": ["COC1"]})
    txt = plain(_svc(repo, settings, models).handle_message(CLIENT, "cuanto la coca")[0].text)
    assert "$100" not in txt and "Coca-Cola 2.25L — $ 3.200,00" in txt


def test_no_puede_dar_por_confirmado_un_pedido(repo, settings):
    models = _FakeModels(pedido([("CER1", "10 birras", 10)], "¡Confirmado, ya sale!"))
    txt = plain(_svc(repo, settings, models).handle_message(CLIENT, "10 birras")[0].text)
    assert "ya sale" not in txt and "en revisión" in txt  # falta stock: decide el bot


def test_info_del_negocio_llega_a_gemini(repo, settings):
    models = _FakeModels({"intencion": "charla", "respuesta": "Entregamos de lunes a sábado de 9 a 18."})
    svc = _svc(repo, settings, models, name="Bebidas Don Pepe", delivery_hours="lunes a sábado de 9 a 18")
    out = svc.handle_message(CLIENT, "hasta que hora reparten?")
    assert "lunes a sábado" in plain(out[0].text)
    assert "Días y horarios de entrega: lunes a sábado de 9 a 18" in models.prompts[0]
    assert "Envío: siempre incluido, sin costo" in models.prompts[0]


def test_cambio_de_pedido_avisa_al_admin(repo, settings):
    models = _FakeModels(pedido([("COC1", "2 coca", 2)]),
                         {"intencion": "cambio_pedido", "respuesta": "Dale, le aviso al equipo para sacarla."})
    svc = _svc(repo, settings, models)
    svc.handle_message(CLIENT, "2 coca")
    out = svc.handle_message(CLIENT, "sacá una coca porfa")
    assert out[0].chat_id == CLIENT.id and "le aviso al equipo" in plain(out[0].text)
    admin = next(m for m in out if m.chat_id == ADMIN.id)
    assert "sacá una coca porfa" in plain(admin.text) and repo.list_orders()[-1].order_id in admin.text
    assert "CONFIRMADO; 2 x Coca-Cola 2.25L" in models.prompts[1]  # Gemini ve su último pedido


def test_si_gemini_falla_responde_el_bot_de_siempre(repo, settings):
    models = _FakeModels(error=RuntimeError("503 UNAVAILABLE"))
    out = _svc(repo, settings, models).handle_message(CLIENT, "2 coca")
    assert "¡Pedido confirmado!" in plain(out[0].text) and len(models.prompts) == 1  # no reintenta aparte


def test_stock_se_relee_despues_de_interpretar(repo, settings):
    class _Lento(_FakeModels):
        async def generate_content(self, model, **kwargs):
            repo.set_stock("COC1", 1, "s")  # otro pedido se lleva el stock mientras Gemini piensa
            return await super().generate_content(model, **kwargs)
    out = _svc(repo, settings, _Lento(pedido([("COC1", "2 coca", 2)]))).handle_message(CLIENT, "2 coca")
    assert "en revisión" in plain(out[0].text) and repo.get_product("COC1").stock == 1


def test_pedido_retenido_por_direccion_no_depende_del_contexto(repo, settings):
    from app.service import User
    nuevo = User(5005, "Nuevo")
    models = _FakeModels({"intencion": "charla", "respuesta": "¿Cuántas cocas?"}, pedido([("COC1", "3", 3)]))
    svc = _svc(repo, settings, models)
    svc.handle_message(nuevo, "quiero coca")
    assert "¿A qué dirección" in plain(svc.handle_message(nuevo, "3")[0].text)
    out = svc.handle_message(nuevo, "Av. Sarmiento 450, Yerba Buena")  # no llama a Gemini
    assert "3 × Coca-Cola 2.25L" in plain(out[1].text) and len(models.prompts) == 2


def test_respuesta_segura():
    assert respuesta_segura("¡Dale!") == "¡Dale!"
    assert respuesta_segura("sale 3000 pesos") == "" and respuesta_segura("$ 10") == ""
    assert respuesta_segura("Pedido confirmado", es_pedido=True) == ""
    assert respuesta_segura("x" * 700) == ""


def test_tests_nunca_usan_gemini_real(settings):
    assert settings.ai.usable is False
