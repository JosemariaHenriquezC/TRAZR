# Bot de pedidos (Trazr) — contexto

Bot de Telegram en Python que recibe pedidos de bebidas en lenguaje natural,
valida stock y precio contra la base SQLite, confirma o deriva a revisión del
admin. TFI de Administración de Recursos, UTN-FRT, comisión 4K1.

## Arquitectura
- `app/bot.py` — capa de Telegram, sin lógica de negocio
- `app/service.py` — `BotService`: entrada de mensajes, comandos, revisión del admin (independiente del canal)
- `app/checkout.py` — borrador → resumen → confirmación; dirección; modificar pedidos hechos
- `app/messages.py` — `Outgoing` (texto + botones genéricos)
- `app/parser.py` — intérprete por reglas (respaldo)
- `app/ai_parser.py` — Gemini: `parse_order` (intérprete) y `conversar` (intención + respuesta humana)
- `app/rules.py` — regla "dentro de los parámetros"
- `app/storage/` — interfaz `Repository` (`base.py`) e implementación SQLite (`sqlite.py`)
- `scripts/init_db.py` — crea la base y carga el catálogo (`--ejemplo` o `--csv`)

## Reglas del proyecto
- Catálogo solo de bebidas.
- Gemini interpreta y redacta, pero NUNCA pone números: precios, stock, totales y estados
  salen de la base y de rules.py (`respuesta_segura` descarta textos con plata o "confirmado").
- `service.py` no debe depender de Telegram: va a sumarse WhatsApp. Los clientes se
  identifican por `canal` + `externo_id`.
- Stock: se verifica al armar el borrador y otra vez al confirmar. Si no alcanza, el cliente
  NO puede confirmar: el bot le dice la cantidad exacta que queda y ofrece ajustar
  (🔧 / «sí»). Al modificar, lo que el pedido ya tenía cuenta como disponible.
  La revisión del admin queda solo para precio vencido y total máximo.
- Ningún pedido se registra sin dirección de envío ni sin confirmación del cliente.
  El envío siempre está incluido, sin costo.
- La dirección guardada solo cambia si el cliente lo elige: tras un pedido a otra dirección,
  el bot pregunta cuál dejar (🏠 la de siempre / 📍 la nueva). La primera se guarda sola.
- Tienda ficticia de ejemplo: «Trazr» (valores por defecto en `config.TRAZR`).
- Nunca commitear `.env`, `credentials.json` ni la base (`data/`).
- Los tests no deben llamar a APIs reales (`Settings.ai` viene apagado por defecto;
  para probar con IA se pasa un cliente simulado a `BotService(ai_client=...)`). Correr con `python -m pytest -q`.

## Plan por fases
0. Preparación — hecha
1. Gemini como intérprete — hecha y conectada en `service.py` (respaldo: reglas)
2. SQLite + dirección obligatoria (envío siempre incluido, sin costo) — hecha
3. Confirmación del pedido y de la dirección (botones), modificación de pedidos hechos — hecha
4. **Web de administración** (FastAPI) — SOLO para el admin
5. WhatsApp Cloud API (webhook en la misma app FastAPI)
6. Mercado Pago (link de pago + webhook idempotente)
7. Faltantes del TFI: crédito, recompra, planes
8. (A evaluar al final) web para clientes. Por ahora el canal del cliente es el chat.

## Próxima sesión — empezar por acá

### Fase 4: web de administración
- Interpretación a confirmar con el usuario: «todo lo de confirmar el pedido va a la
  web» = la gestión del admin (pedidos en revisión, aprobar/rechazar, ver pedidos) pasa
  a la web. El cliente sigue confirmando su pedido en el chat.
- Propuesta: FastAPI + plantillas HTML (Jinja2) simples, login de admin, misma base
  SQLite y misma lógica de `BotService` (approve/reject/stock/precio): la web es otro
  «canal» del admin, sin duplicar reglas. Pantallas: pedidos (filtro por estado),
  detalle con aprobar/rechazar, stock y precios, clientes.
- Decidir: ¿los avisos al admin por Telegram siguen como notificación, o todo solo en la web?
  ¿Cómo corren juntos el bot y la web (un proceso o dos)? La FastAPI de la fase 4 es la
  misma que después recibe el webhook de WhatsApp (fase 5).
