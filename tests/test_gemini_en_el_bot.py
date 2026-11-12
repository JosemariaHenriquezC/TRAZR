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

from .conftest import CLIENT, CLIENT_ADDRESS, avisos, plain


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


def charla(respuesta):
    return {"intencion": "charla", "respuesta": respuesta}


def intent(nombre, respuesta="", **extra):
    return {"intencion": nombre, "respuesta": respuesta, **extra}


def pedido(items, respuesta="¡Dale, te lo preparo!"):
    return {"intencion": "pedido", "respuesta": respuesta,
            "items": [{"sku": s, "texto": t, "cantidad": q} for s, t, q in items]}


def test_entiende_jerga_y_responde_como_persona(repo, settings):
    models = _FakeModels(pedido([("COC1", "un par de gaseosas cola grandes", 2),
                                 ("CER1", "una rubia", 1)], "¡Buenísimo, Juana! Fijate el resumen 🍻"),
                         intent("confirmar", "¡Listo!"))
    svc = _svc(repo, settings, models)
    out = svc.handle_message(CLIENT, "pasame un par de gaseosas cola grandes y una rubia")
    txt = plain(out[0].text)
    assert txt.startswith("¡Buenísimo, Juana! Fijate el resumen 🍻")
    assert "Revisá tu pedido" in txt and "2 × Coca-Cola 2.25L" in txt and "1 × Cerveza Rubia 1L" in txt
    assert repo.list_orders() == []
    out = svc.handle_message(CLIENT, "joya, mandalo")  # Gemini entiende que confirma
    assert "¿Cómo lo vas a pagar?" in plain(out[0].text)
    out = svc.handle_message(CLIENT, "en efectivo")
    assert "¡Pedido completado!" in plain(out[0].text)
    assert "BORRADOR ACTUAL" in models.prompts[1] and "2 x Coca-Cola 2.25L (COC1)" in models.prompts[1]
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
    assert "3 × Agua Mineral con Gas 2L" in plain(out[0].text) and "Revisá tu pedido" in plain(out[0].text)
    # La segunda vez Gemini recibió lo que se habló antes
    assert "CLIENTE: 3 aguas" in models.prompts[1] and "VOS: ¿Querés el agua con gas" in models.prompts[1]


def test_consulta_de_precio_los_numeros_los_pone_el_bot(repo, settings):
    models = _FakeModels({"intencion": "consulta", "respuesta": "¡Sí! Mirá:",
                          "consulta_skus": ["FER1", "VIN1", "INVENTADO"]})
    txt = plain(_svc(repo, settings, models).handle_message(CLIENT, "tenés fernet y vino? a cuánto?")[0].text)
    assert txt.startswith("¡Sí! Mirá:")
    assert "Fernet 750ml — $ 12.000,00 ✅ disponible" in txt
    assert "Vino" not in txt and "sin stock" not in txt   # lo agotado ni se nombra (no se le pasa a Gemini)
    assert "INVENTADO" not in txt and repo.list_orders() == []


def test_si_gemini_escribe_precios_se_descarta_su_texto(repo, settings):
    models = _FakeModels({"intencion": "consulta", "respuesta": "La coca sale $100, re barata",
                          "consulta_skus": ["COC1"]})
    txt = plain(_svc(repo, settings, models).handle_message(CLIENT, "cuanto la coca")[0].text)
    assert "$100" not in txt and "Coca-Cola 2.25L — $ 3.200,00" in txt


def test_no_puede_dar_por_confirmado_un_pedido(repo, settings):
    models = _FakeModels(pedido([("FER1", "1 fernet", 1)], "¡Confirmado, ya sale!"))
    txt = plain(_svc(repo, settings, models).handle_message(CLIENT, "1 fernet")[0].text)
    assert "ya sale" not in txt and "queda en revisión" in txt  # precio vencido: decide el bot
    assert repo.list_orders() == []


def test_info_del_negocio_llega_a_gemini(repo, settings):
    models = _FakeModels({"intencion": "charla", "respuesta": "Entregamos de lunes a sábado de 9 a 18."})
    svc = _svc(repo, settings, models, name="Bebidas Don Pepe", delivery_hours="lunes a sábado de 9 a 18")
    out = svc.handle_message(CLIENT, "hasta que hora reparten?")
    assert "lunes a sábado" in plain(out[0].text)
    assert "Días y horarios de entrega: lunes a sábado de 9 a 18" in models.prompts[0]
    assert "Envío: siempre incluido, sin costo" in models.prompts[0]


def test_cambio_de_pedido_ya_hecho(repo, settings):
    models = _FakeModels(pedido([("COC1", "2 coca", 2), ("FAN1", "1 fanta", 1)]), intent("confirmar"),
                         intent("cambio_pedido", "Dale, te lo cambio. Fijate cómo queda 👇",
                                items=[{"sku": "COC1", "texto": "2 coca", "cantidad": 2},
                                       {"sku": "SPR1", "texto": "1 sprite", "cantidad": 1}]),
                         intent("confirmar"))
    svc = _svc(repo, settings, models)
    svc.handle_message(CLIENT, "2 coca y 1 fanta")
    svc.handle_message(CLIENT, "si")
    svc.handle_message(CLIENT, "efectivo")
    oid = repo.list_orders()[-1].order_id
    out = svc.handle_message(CLIENT, "che, cambiame la fanta por una sprite")
    txt = plain(out[0].text)
    assert txt.startswith("Dale, te lo cambio") and f"Así quedaría tu pedido {oid}" in txt
    assert "CONFIRMADO; 2 x Coca-Cola 2.25L, 1 x Fanta Naranja 2.25L" in models.prompts[2]
    out = svc.handle_message(CLIENT, "dale")
    assert "¡Pedido modificado y confirmado!" in plain(out[0].text)
    assert [(l.sku, l.qty) for l in repo.get_order(oid).lines] == [("COC1", 2), ("SPR1", 1)]
    assert repo.get_product("FAN1").stock == 40 and repo.get_product("SPR1").stock == 39


def test_cambiar_direccion_por_chat(repo, settings):
    models = _FakeModels(pedido([("COC1", "2 coca", 2)]),
                         intent("cambiar_direccion", "¡Dale!", direccion="Belgrano 300, Tafí Viejo"),
                         intent("confirmar"))
    svc = _svc(repo, settings, models)
    assert f"Dirección: {CLIENT_ADDRESS}" in plain(svc.handle_message(CLIENT, "2 coca")[0].text)
    out = svc.handle_message(CLIENT, "mandalo a lo de mi vieja, Belgrano 300, Tafí Viejo")
    assert "Dirección: Belgrano 300, Tafí Viejo" in plain(out[0].text)
    svc.handle_message(CLIENT, "si")
    svc.handle_message(CLIENT, "efectivo")
    assert repo.list_orders()[-1].address == "Belgrano 300, Tafí Viejo"


def test_cambiar_direccion_sin_decirla_la_pide(repo, settings):
    models = _FakeModels(pedido([("COC1", "2 coca", 2)]), intent("cambiar_direccion", "¡Dale!"))
    svc = _svc(repo, settings, models)
    svc.handle_message(CLIENT, "2 coca")
    assert "mandame la nueva dirección" in plain(svc.handle_message(CLIENT, "a otra dirección")[0].text)
    out = svc.handle_message(CLIENT, "Belgrano 300, Tafí Viejo")  # no pasa por Gemini
    assert "Dirección: Belgrano 300, Tafí Viejo" in plain(out[0].text) and len(models.prompts) == 2


def test_cancelar_por_chat(repo, settings):
    models = _FakeModels(pedido([("COC1", "2 coca", 2)]), intent("cancelar", "Uh, bueno"))
    svc = _svc(repo, settings, models)
    svc.handle_message(CLIENT, "2 coca")
    assert "descarté" in plain(svc.handle_message(CLIENT, "mejor no, después te escribo")[0].text)
    assert repo.list_orders() == []


def test_consulta_con_borrador_pendiente_lo_recuerda(repo, settings):
    models = _FakeModels(pedido([("COC1", "2 coca", 2)]), charla("Entregamos de lunes a sábado."))
    svc = _svc(repo, settings, models)
    svc.handle_message(CLIENT, "2 coca")
    txt = plain(svc.handle_message(CLIENT, "qué días reparten?")[0].text)
    assert "lunes a sábado" in txt and "sigue esperando confirmación" in txt


def test_cambio_fuera_de_termino_avisa_al_admin(repo, settings):
    models = _FakeModels(intent("cambio_pedido", "Dale, veo qué se puede hacer",
                                items=[{"sku": "COC1", "texto": "3 coca", "cantidad": 3}]))
    out = _svc(repo, settings, models).handle_message(CLIENT, "cambiá mi pedido a 3 cocas")
    assert "ya no se puede modificar" in plain(out[0].text)
    assert avisos(repo)[0].kind == "fuera_de_termino" and "3 cocas" in avisos(repo)[0].detail


def test_si_gemini_no_contesta_pide_esperar_reintenta_y_si_no_pide_escribir_despues(repo, settings):
    models = _FakeModels(error=RuntimeError("503 UNAVAILABLE"))
    svc = _svc(repo, settings, models)
    esperas = []
    out = svc.handle_message(CLIENT, "2 coca", on_wait=esperas.append)
    assert len(esperas) == 1 and "Dame un momento" in esperas[0].text and esperas[0].chat_id == CLIENT.id
    assert len(models.prompts) == 3              # 1 intento + 2 reintentos (settings.ai_retry_delays)
    assert "Escribime de nuevo en unos minutos" in plain(out[0].text) and repo.list_orders() == []
    assert any(n.kind == "falla" and "Gemini no responde" in n.title for n in repo.list_notices())


def test_si_gemini_vuelve_en_un_reintento_sigue_normal(repo, settings):
    class _Intermitente(_FakeModels):
        async def generate_content(self, model, **kwargs):
            if not self.prompts:
                self.prompts.append("fallo")
                raise RuntimeError("503")
            return await super().generate_content(model, **kwargs)
    models = _Intermitente(pedido([("COC1", "2 coca", 2)]))
    esperas = []
    out = _svc(repo, settings, models).handle_message(CLIENT, "2 coca", on_wait=esperas.append)
    assert len(esperas) == 1 and "Revisá tu pedido" in plain(out[0].text)


def test_stock_se_relee_despues_de_interpretar(repo, settings):
    # Entre el resumen y la confirmación, otro pedido se lleva el stock: decide el stock al confirmar.
    svc = _svc(repo, settings, _FakeModels(pedido([("COC1", "2 coca", 2)]), intent("confirmar")))
    svc.handle_message(CLIENT, "2 coca")
    repo.set_stock("COC1", 1, "s")
    out = svc.handle_message(CLIENT, "si")
    assert "pediste 2, me quedan 1" in plain(out[0].text) and repo.list_orders() == []


def test_pedido_retenido_por_direccion_no_depende_del_contexto(repo, settings):
    from app.service import User
    nuevo = User(5005, "Nuevo")
    models = _FakeModels({"intencion": "charla", "respuesta": "¿Cuántas cocas?"}, pedido([("COC1", "3", 3)]))
    svc = _svc(repo, settings, models)
    svc.handle_message(nuevo, "quiero coca")
    assert "¿A qué dirección" in plain(svc.handle_message(nuevo, "3")[0].text)
    out = svc.handle_message(nuevo, "Av. Sarmiento 450, Yerba Buena")  # no llama a Gemini
    assert "3 × Coca-Cola 2.25L" in plain(out[0].text) and len(models.prompts) == 2


def test_respuesta_segura():
    assert respuesta_segura("¡Dale!") == "¡Dale!"
    assert respuesta_segura("sale 3000 pesos") == "" and respuesta_segura("$ 10") == ""
    assert respuesta_segura("Pedido confirmado", es_pedido=True) == ""
    assert respuesta_segura("x" * 700) == ""


def test_tests_nunca_usan_gemini_real(settings):
    assert settings.ai.usable is False


def test_falta_de_stock_charla_con_gemini(repo, settings):
    settings.rules.max_order_total = 0
    models = _FakeModels(pedido([("CER1", "10 birras", 10)], "¡Dale!"),
                         intent("confirmar", "Dale, ajustalo"), intent("confirmar"))
    svc = _svc(repo, settings, models)
    out = svc.handle_message(CLIENT, "10 birras")
    assert "pediste 10, me quedan 5" in plain(out[0].text)  # el número lo pone el bot
    out = svc.handle_message(CLIENT, "bueno, mandame las que tengas")
    assert "no alcanza el stock" in models.prompts[1]  # Gemini sabe en qué paso estamos
    assert "5 × Cerveza Rubia 1L" in plain(out[0].text) and "Revisá tu pedido" in plain(out[0].text)
    assert "¿Cómo lo vas a pagar?" in plain(svc.handle_message(CLIENT, "dale")[0].text)
    assert "¡Pedido completado!" in plain(svc.handle_message(CLIENT, "por transferencia")[0].text)
