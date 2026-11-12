"""
Arranca todo junto: la web de administración y el bot de Telegram, en un proceso.

    python -m app.server

La web queda en http://127.0.0.1:8000 (WEB_HOST / WEB_PORT en el .env). El bot
corre dentro del mismo proceso (mismo BotService, mismo candado, misma base), y
cuando el admin aprueba o rechaza en la web, el aviso al cliente sale por el bot.
Sin TELEGRAM_BOT_TOKEN arranca solo la web (útil para probarla).

Correr siempre con UN solo proceso (no usar --workers ni --reload): dos bots
leyendo el mismo token chocan en Telegram.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

import uvicorn

from .bot import BOT_COMMANDS, build_telegram_app, telegram_sender
from .config import ConfigError, Settings, load_settings
from .notifier import Notifier
from .service import BotService
from .storage import StorageError, build_repository
from .web.main import create_app

log = logging.getLogger(__name__)


def build(settings: Settings):
    repo = build_repository(settings)
    svc = BotService(repo, settings)
    notifier = Notifier()

    @asynccontextmanager
    async def lifespan(app):
        tg = None
        if settings.telegram_token:
            tg = build_telegram_app(settings.telegram_token, svc)
            await tg.initialize()
            await tg.bot.set_my_commands(BOT_COMMANDS)
            await tg.start()
            await tg.updater.start_polling(allowed_updates=["message", "callback_query"])
            notifier.register("telegram", telegram_sender(tg.bot))
            me = await tg.bot.get_me()
            log.info("Bot @%s en marcha junto con la web.", me.username)
        else:
            log.warning("Sin TELEGRAM_BOT_TOKEN: arranca solo la web (no se pueden avisar clientes).")

        async def expire_loop():
            # Cada 5 minutos: cancela transferencias sin pagar pasadas PAGO_HORAS y avisa al cliente.
            while True:
                try:
                    await notifier.send(await asyncio.to_thread(svc.expire_unpaid))
                except Exception:
                    log.exception("Falló la revisión de pagos vencidos")
                await asyncio.sleep(300)

        expirer = asyncio.create_task(expire_loop())
        try:
            yield
        finally:
            expirer.cancel()
            if tg is not None:
                await tg.updater.stop()
                await tg.stop()
                await tg.shutdown()

    return create_app(svc, notifier, settings, lifespan=lifespan), svc


def main() -> None:
    logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        settings = load_settings(require_telegram=False)
    except ConfigError as exc:
        raise SystemExit(f"Error de configuración: {exc}")
    try:
        app, svc = build(settings)
        n = len(svc.repo.list_products())
    except StorageError as exc:
        raise SystemExit(f"No pude acceder a la base de datos: {exc}")
    if n == 0:
        log.warning("El catálogo está vacío. Cargalo con: python -m scripts.init_db --ejemplo")
    if not svc.repo.list_admins():
        log.warning("No hay admins para la web. Creá uno con: python -m scripts.crear_admin usuario \"Nombre\"")
    log.info("Web de administración: http://%s:%s", settings.web_host, settings.web_port)
    uvicorn.run(app, host=settings.web_host, port=settings.web_port, workers=1, log_level="info")


if __name__ == "__main__":
    main()
