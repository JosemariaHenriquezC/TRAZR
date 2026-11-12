"""La capa de Telegram traduce bien entre Update/botones y BotService (sin red)."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from telegram import InlineKeyboardMarkup

from app import bot as tg
from app.service import BotService

from .conftest import ADMIN, CLIENT


def make_context(svc):
    fake_bot = SimpleNamespace(send_message=AsyncMock(), send_chat_action=AsyncMock())
    return SimpleNamespace(application=SimpleNamespace(bot_data={"service": svc}), bot=fake_bot)


def make_update(user, text=None, data=None):
    u = SimpleNamespace(full_name=user.name, username=None, id=user.id)
    cq = None
    if data is not None:
        cq = SimpleNamespace(data=data, answer=AsyncMock(), edit_message_reply_markup=AsyncMock())
    return SimpleNamespace(effective_user=u, effective_chat=SimpleNamespace(id=user.id),
                           effective_message=SimpleNamespace(text=text), callback_query=cq)


def test_mensaje_y_botones(svc: BotService, repo):
    ctx = make_context(svc)
    asyncio.run(tg.on_text(make_update(CLIENT, "10 cerveza"), ctx))
    calls = ctx.bot.send_message.call_args_list
    assert calls[0].kwargs["chat_id"] == CLIENT.id and calls[0].kwargs["parse_mode"] == "HTML"
    admin_call = calls[1].kwargs
    assert admin_call["chat_id"] == ADMIN.id
    assert isinstance(admin_call["reply_markup"], InlineKeyboardMarkup)
    data = admin_call["reply_markup"].inline_keyboard[0][1].callback_data
    assert data.startswith("rj:")

    ctx2 = make_context(svc)
    upd = make_update(ADMIN, data=data)
    asyncio.run(tg.on_button(upd, ctx2))
    upd.callback_query.edit_message_reply_markup.assert_awaited()  # se quitan los botones
    sent_to = [c.kwargs["chat_id"] for c in ctx2.bot.send_message.call_args_list]
    assert sent_to == [ADMIN.id, CLIENT.id]
    assert repo.list_orders()[-1].status == "RECHAZADO"
