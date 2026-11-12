"""
Configuración del bot.

Todo lo sensible (token de Telegram, claves de APIs) se lee de
variables de entorno o de un archivo .env (ver .env.example). Nada de eso
está escrito en el código.

Las reglas de negocio ("dentro de los parámetros") también son configurables
desde el .env, así se pueden ajustar sin tocar código.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

from .ai_parser import AIConfig, load_ai_config


class ConfigError(Exception):
    """Falta o es inválida una variable de configuración."""


def _bool(value: str | None, default: bool) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "si", "sí", "yes", "y"}


def _int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if raw == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} debe ser un número entero (valor actual: {raw!r})") from exc


def _float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip().replace(",", ".")
    if raw == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} debe ser un número (valor actual: {raw!r})") from exc


def _delays(name: str, default: tuple[float, ...]) -> tuple[float, ...]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return tuple(float(x) for x in raw.replace(";", ",").split(",") if x.strip())
    except ValueError as exc:
        raise ConfigError(f"{name} debe ser una lista de segundos, por ejemplo 4,8 (valor actual: {raw!r})") from exc


@dataclass
class Rules:
    """
    Regla mínima de "pedido dentro de los parámetros".

    Un pedido se CONFIRMA AUTOMÁTICAMENTE si:
      1. Todos sus productos existen en el catálogo, están activos y tienen precio cargado.
      2. Hay stock suficiente (si no, el cliente NO puede confirmar: el bot le dice cuánto
         queda y ofrece ajustar; ver checkout.py).
      3. El total no supera MAX_ORDER_TOTAL (0 = sin límite).
    Si falla 3 (o un producto no tiene precio) → se registra "EN REVISION" y se avisa en la web.
    Los precios no vencen: se cambian desde la web cuando hace falta.
    Si un producto no existe → depende de ON_UNKNOWN_PRODUCT:
        "ask"    (default) se le pide al cliente que corrija; no se registra.
        "review" se registra en revisión igual.

    Errores de interpretación (mensaje ambiguo, cantidad inválida o mayor a
    MAX_QTY_PER_ITEM) nunca se registran: el bot pide aclaración.
    """

    require_stock: bool = True
    max_order_total: float = 150000.0     # 0 = sin límite
    max_qty_per_item: int = 1000          # cantidades mayores se consideran inválidas
    on_unknown_product: str = "ask"       # "ask" | "review"
    match_threshold: float = 0.72         # similitud mínima para reconocer un producto (0-1)
    modify_minutes: int = 60              # minutos que tiene el cliente para modificar su pedido
    payment_hours: int = 24               # una transferencia sin pagar se cancela pasado este tiempo


# Tienda ficticia de ejemplo: se usa si el .env no define los NEGOCIO_*.
TRAZR = {
    "NEGOCIO_NOMBRE": "Trazr",
    "NEGOCIO_HORARIOS": ("lunes a viernes de 9 a 20 y sábados de 9 a 14; domingos y feriados cerrado. "
                         "Los pedidos confirmados antes de las 13 se entregan en el día; "
                         "después de las 13, el siguiente día hábil."),
    "NEGOCIO_ZONA": "San Miguel de Tucumán, Yerba Buena, Tafí Viejo y Banda del Río Salí",
    "NEGOCIO_PEDIDO_MINIMO": "no hay pedido mínimo",
    "NEGOCIO_PAGOS": "efectivo o transferencia bancaria al recibir el pedido",
    "NEGOCIO_INFO": ("Trazr es una distribuidora de bebidas de Tucumán: gaseosas, aguas, cervezas, vinos, "
                     "aperitivos, jugos y energizantes. Venta a mayoristas, almacenes y particulares. "
                     "Las bebidas alcohólicas solo se venden a mayores de 18 años."),
}


@dataclass
class Business:
    """Datos del negocio que Gemini puede usar para responder consultas.
    Lo que quede vacío, Gemini responde que lo consulta con el equipo."""
    name: str = ""
    delivery_hours: str = ""     # días y horarios de entrega
    delivery_zone: str = ""      # zona de reparto
    min_order: str = ""          # pedido mínimo (texto libre)
    payment: str = ""            # formas de pago
    extra: str = ""              # cualquier otra info útil
    transfer_alias: str = "TRAZR.BEBIDAS.TUC"          # alias para transferencias (de ejemplo)
    transfer_holder: str = "Trazr Distribuidora (cuenta de ejemplo)"
    open_hour: int = 9           # horario de atención (para el tablero «el día en reparto»)
    close_hour: int = 20
    cutoff_hour: int = 13        # lo confirmado antes de esta hora sale en el día

    def as_text(self) -> str:
        rows = [("Nombre", self.name), ("Días y horarios de entrega", self.delivery_hours),
                ("Zona de reparto", self.delivery_zone), ("Pedido mínimo", self.min_order),
                ("Formas de pago", self.payment), ("Otros datos", self.extra)]
        out = [f"- {k}: {v}" for k, v in rows if v]
        out.append("- Envío: siempre incluido, sin costo.")
        return "\n".join(out)


@dataclass
class Settings:
    telegram_token: str
    # Base de datos SQLite (un archivo; se crea solo si no existe)
    database_path: str = "data/trazr.db"
    # Presentación
    currency_symbol: str = "$"
    timezone: str = "UTC"
    rules: Rules = field(default_factory=Rules)
    # Intérprete con Gemini. Apagado por defecto: load_settings() lo lee del .env.
    # (Así los tests, que arman Settings a mano, nunca llaman a la API real.)
    ai: AIConfig = field(default_factory=lambda: AIConfig(enabled=False))
    business: Business = field(default_factory=Business)
    # Si Gemini no contesta: se le pide al cliente que espere y se reintenta tras estas pausas (s)
    ai_retry_delays: tuple[float, ...] = (4.0, 8.0)
    # Web de administración
    secret_key: str = ""                  # firma la cookie de sesión (vacío = se genera al arrancar)
    web_host: str = "127.0.0.1"
    web_port: int = 8000


def load_settings(require_telegram: bool = True, env_file: str | None = None) -> Settings:
    """Lee la configuración. Lanza ConfigError con un mensaje claro si falta algo."""
    load_dotenv(env_file or Path.cwd() / ".env", override=False)

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if require_telegram and not token:
        raise ConfigError(
            "Falta TELEGRAM_BOT_TOKEN. Crealo con @BotFather y pegalo en el archivo .env."
        )

    on_unknown = os.getenv("ON_UNKNOWN_PRODUCT", "ask").strip().lower()
    if on_unknown not in {"ask", "review"}:
        raise ConfigError("ON_UNKNOWN_PRODUCT debe ser 'ask' o 'review'.")

    rules = Rules(
        require_stock=_bool(os.getenv("RULE_REQUIRE_STOCK"), True),
        max_order_total=_float("MAX_ORDER_TOTAL", 150000.0),
        max_qty_per_item=_int("MAX_QTY_PER_ITEM", 1000),
        on_unknown_product=on_unknown,
        match_threshold=_float("MATCH_THRESHOLD", 0.72),
        modify_minutes=_int("MODIFICACION_MINUTOS", 60),
        payment_hours=_int("PAGO_HORAS", 24),
    )

    settings = Settings(
        telegram_token=token,
        database_path=os.getenv("DATABASE_PATH", "data/trazr.db").strip() or "data/trazr.db",
        currency_symbol=os.getenv("CURRENCY_SYMBOL", "$").strip() or "$",
        timezone=os.getenv("TIMEZONE", "UTC").strip() or "UTC",
        rules=rules,
        ai=load_ai_config(),
        ai_retry_delays=_delays("AI_RETRY_DELAYS", (4.0, 8.0)),
        secret_key=os.getenv("SECRET_KEY", "").strip(),
        web_host=os.getenv("WEB_HOST", "127.0.0.1").strip() or "127.0.0.1",
        web_port=_int("WEB_PORT", 8000),
        business=Business(
            name=os.getenv("NEGOCIO_NOMBRE", TRAZR["NEGOCIO_NOMBRE"]).strip(),
            delivery_hours=os.getenv("NEGOCIO_HORARIOS", TRAZR["NEGOCIO_HORARIOS"]).strip(),
            delivery_zone=os.getenv("NEGOCIO_ZONA", TRAZR["NEGOCIO_ZONA"]).strip(),
            min_order=os.getenv("NEGOCIO_PEDIDO_MINIMO", TRAZR["NEGOCIO_PEDIDO_MINIMO"]).strip(),
            payment=os.getenv("NEGOCIO_PAGOS", TRAZR["NEGOCIO_PAGOS"]).strip(),
            extra=os.getenv("NEGOCIO_INFO", TRAZR["NEGOCIO_INFO"]).strip(),
            transfer_alias=os.getenv("NEGOCIO_ALIAS", "TRAZR.BEBIDAS.TUC").strip(),
            transfer_holder=os.getenv("NEGOCIO_TITULAR", "Trazr Distribuidora (cuenta de ejemplo)").strip(),
            open_hour=_int("NEGOCIO_HORA_APERTURA", 9),
            close_hour=_int("NEGOCIO_HORA_CIERRE", 20),
            cutoff_hour=_int("NEGOCIO_HORA_CORTE", 13),
        ),
    )

    return settings
