"""Al arrancar, el bot vuelve a poner su nombre, textos, comandos y menú (bot simulado)."""
import asyncio
from types import SimpleNamespace

from app.bot import BOT_COMMANDS, profile_texts, restore_profile


class FakeBot:
    def __init__(self, name, description, short):
        self.state = {"name": name, "description": description, "short_description": short}
        self.calls = []

    async def get_my_name(self, language_code=None):
        return SimpleNamespace(name=self.state["name"])

    async def get_my_description(self, language_code=None):
        return SimpleNamespace(description=self.state["description"])

    async def get_my_short_description(self, language_code=None):
        return SimpleNamespace(short_description=self.state["short_description"])

    def __getattr__(self, method):          # set_my_*, delete_my_commands, set_chat_menu_button…
        async def call(*args, **kwargs):
            self.calls.append((method, args, kwargs))
            if method == "set_my_name" and not kwargs.get("language_code"):
                self.state["name"] = args[0]
        return call


def test_un_perfil_cambiado_por_otro_se_restablece():
    bot = FakeBot("Аниме бот", "реклама", "реклама")
    asyncio.run(restore_profile(bot, "Trazr"))
    texts = profile_texts("Trazr")
    methods = [m for m, _, _ in bot.calls]
    assert ("set_my_name", ("Trazr",), {}) in bot.calls
    assert ("set_my_description", (texts["description"],), {}) in bot.calls
    assert ("set_my_short_description", (texts["short"],), {}) in bot.calls
    assert ("set_my_commands", (BOT_COMMANDS,), {}) in bot.calls
    assert "set_chat_menu_button" in methods and "set_my_default_administrator_rights" in methods
    assert ("delete_my_commands", (), {"language_code": "ru"}) in bot.calls
    assert len(texts["short"]) <= 120 and len(texts["description"]) <= 512


def test_si_el_perfil_esta_bien_no_cambia_el_nombre():
    texts = profile_texts("Trazr")
    bot = FakeBot(texts["name"], texts["description"], texts["short"])
    asyncio.run(restore_profile(bot, "Trazr"))
    assert not any(m == "set_my_name" and not kw for m, _, kw in bot.calls)
