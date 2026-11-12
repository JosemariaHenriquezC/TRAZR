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

### Fase 4: web de administración (NO empezar hasta que el usuario lo indique: va a sumar cosas)
Decidido:
- La revisión del admin (pedidos EN REVISION, aprobar/rechazar con motivo) sale del chat y
  pasa a la web. Motivo: el chat es la conversación con el cliente; el admin no debe recibir
  pedidos ni botones de aprobar ahí. Hoy eso vive en `checkout._notify_review`,
  `service.approve/reject`, `cmd_pending` y los botones `ap:`/`rj:` de `bot.py`.
- El cliente sigue confirmando su pedido en el chat; cuando el admin aprueba o rechaza en la
  web, al cliente le llega el aviso por el chat (Telegram ahora, WhatsApp en la fase 5).
- La lógica no se duplica: la web llama a los mismos métodos de `BotService` (approve, reject,
  set_stock, set_price...). La web es otro «canal» del admin.

Propuesta técnica (a confirmar): FastAPI + plantillas Jinja2 simples (sin framework de
frontend), mismo archivo SQLite, bot de Telegram corriendo dentro del mismo proceso
(un solo comando para levantar todo). Es la misma app que después recibe el webhook de
WhatsApp (fase 5). Corre local (http://localhost) hasta la fase 5, que necesita URL pública.

Pantallas propuestas: pedidos (filtro por estado), detalle con aprobar/rechazar, stock y
precios (editar, sumar), alta/edición de productos (reemplaza al CSV), clientes, y un tablero
simple (ventas del día, pedidos en revisión) útil para el TFI.

Método de diseño (pedido por el usuario): partir de buenas referencias, usar skills de diseño
y trabajar sobre un diseño real en vez de inventarlo.
1. Diseño primero en Claude Design (artifacts tipo «Design System» y «Design»): armar el
   sistema de diseño de Trazr (colores, tipografía, espaciado, componentes) y las pantallas
   en el canvas; el usuario las revisa y recién después se programa a partir de eso.
   Alternativa/complemento: diseño en Figma + plugin oficial de Figma (MCP) para leerlo.
2. Skills al programar el frontend:
   - `frontend-design` (oficial de Anthropic): criterio visual, evitar diseños genéricos.
     Reemplaza a «Impeccable», que no está en el catálogo.
   - `web-animation-design` y `emil-design-engineering` (basados en el curso de Emil
     Kowalski, vienen en el plugin comunitario «audit-suite»): revisar animaciones y
     microinteracciones de los componentes.
3. Sin framework pesado: las páginas son HTML + CSS (Jinja2); animaciones con CSS.

Falta decidir con el usuario antes de empezar:
1. Login: ¿un solo usuario y contraseña en el `.env`, o varios admins?
2. Avisos al admin: ¿nada fuera de la web, o un aviso corto por Telegram al admin
   («hay un pedido en revisión, entrá a la web»), sin botones?
3. Comandos de admin del chat (/stock, /precio, /aprobar...): ¿se quitan o quedan de respaldo?
4. ¿Confirmar las pantallas de la lista? ¿Algo más (exportar a Excel, reportes)?
5. Lo que el usuario dijo que le falta agregar.
