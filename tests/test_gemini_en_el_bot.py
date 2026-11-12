"""El bot completo usa Gemini para interpretar (con un Gemini SIMULADO: sin API real)."""
import json
from types import SimpleNamespace

from app.ai_parser import AIConfig
from app.models import ESTADO_CONFIRMADO
from app.service import BotService

from .conftest import CLIENT, plain


class _FakeModels:
    def __init__(self, respuesta=None, error=None):
        self.respuesta, self.error, self.llamados = respuesta, error, 0

    async def generate_content(self, model, **kwargs):
        self.llamados += 1
        self.prompt = kwargs.get("contents", "")
        if self.error:
            raise self.error
        return SimpleNamespace(text=json.dumps(self.respuesta))


def _svc(repo, settings, models):
    settings.ai = AIConfig(enabled=True, api_key="falsa", model="m", timeout=5)
    return BotService(repo, settings, ai_client=SimpleNamespace(aio=SimpleNamespace(models=models)))


def test_gemini_entiende_lo_que_las_reglas_no(repo, settings):
    texto = "pasame un par de gaseosas cola grandes y una rubia"
    models = _FakeModels({"items": [
        {"sku": "COC1", "texto": "un par de gaseosas cola grandes", "cantidad": 2},
        {"sku": "CER1", "texto": "una rubia", "cantidad": 1},
    ]})
    out = _svc(repo, settings, models).handle_message(CLIENT, texto)
    txt = plain(out[0].text)
    assert models.llamados == 1 and "¡Pedido confirmado!" in txt
    assert "2 × Coca-Cola 2.25L" in txt and "1 × Cerveza Rubia 1L" in txt
    # El catálogo que ve Gemini sale de la base SQLite, sin precios ni stock
    assert "COC1 | Coca-Cola 2.25L" in models.prompt and "3200" not in models.prompt
    o = repo.list_orders()[-1]
    assert o.status == ESTADO_CONFIRMADO and o.total == 9200  # precios de la base, no de la IA


def test_si_gemini_falla_interpretan_las_reglas(repo, settings):
    models = _FakeModels(error=RuntimeError("503 UNAVAILABLE"))
    out = _svc(repo, settings, models).handle_message(CLIENT, "2 coca")
    assert models.llamados == 1 and "¡Pedido confirmado!" in plain(out[0].text)


def test_stock_se_relee_despues_de_interpretar(repo, settings):
    # Mientras Gemini interpreta, otro pedido se lleva el stock: decide el stock actual.
    class _Lento(_FakeModels):
        async def generate_content(self, model, **kwargs):
            repo.set_stock("COC1", 1, "s")
            return await super().generate_content(model, **kwargs)
    models = _Lento({"items": [{"sku": "COC1", "texto": "2 coca", "cantidad": 2}]})
    out = _svc(repo, settings, models).handle_message(CLIENT, "2 coca")
    assert "en revisión" in plain(out[0].text) and repo.get_product("COC1").stock == 1


def test_tests_nunca_usan_gemini_real(settings):
    assert settings.ai.usable is False
