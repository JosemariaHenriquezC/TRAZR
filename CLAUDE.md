# Bot de pedidos (Trazr) — contexto

Bot de Telegram en Python que recibe pedidos de bebidas en lenguaje natural,
valida stock y precio contra la base SQLite, confirma o deriva a revisión del
admin. TFI de Administración de Sistemas de Información, UTN-FRT, comisión 4K1.
Equipo: Josemaria Henriquez Colacioppo, Nicolás Quinteros, Isa Pizzuto Dip (solo nombres, sin roles).

## Arquitectura
- `app/server.py` — `python -m app.server`: web + bot de Telegram en un proceso
- `landing/` — landing en React (se sirve en `/`; ver landing/README.md)
- `app/web/` — web de admin en `/admin`: `main.py` (rutas FastAPI, login, CSRF), `templates/` (Jinja2), `static/` (CSS del diseño)
- `app/bot.py` — capa de Telegram, sin lógica de negocio
- `app/tunnel.py` — túnel de ngrok que abre el servidor si hay NGROK_DOMAIN
- `app/whatsapp.py` — canal WhatsApp (Cloud API): webhook `/whatsapp/webhook` (firma
  X-Hub-Signature-256, sin duplicados, procesa en segundo plano) y envío por la Graph API
- `app/service.py` — `BotService`: entrada de mensajes, comandos, revisión del admin (independiente del canal)
- `app/checkout.py` — borrador → resumen → confirmación; dirección; modificar pedidos hechos
- `app/messages.py` — `Outgoing` (texto + botones + canal)
- `app/admin.py` — acciones del admin para la web (`AdminResult`: ok, mensaje, `notify` para clientes)
- `app/auth.py` — hash de contraseñas (scrypt) · `app/notifier.py` — envío por canal del cliente
- `app/parser.py` — intérprete por reglas (respaldo)
- `app/ai_parser.py` — Gemini: `parse_order` (intérprete) y `conversar` (intención + respuesta humana)
- `app/rules.py` — regla "dentro de los parámetros"
- `app/storage/` — interfaz `Repository` (`base.py`) e implementación SQLite (`sqlite.py`)
- `scripts/init_db.py` — crea la base y carga el catálogo (`--ejemplo` o `--csv`)

## Reglas del proyecto
- Catálogo solo de bebidas. Categorías = la división de un mayorista de Tucumán (`app/categorias.py`,
  15: Vinos, Vinos Blancos/Rosados, Espumante, Whisky, Gin, Vodka, Ron, Cervezas, Fernet, Aperitivos,
  Licores, Sin Alcohol, Energizantes, Combos, Regalería). En el chat se agrupan en 7 GRUPOS (lista de
  WhatsApp ≤ 10 opciones, títulos ≤ 24); escribir un grupo o una categoría la muestra.
- Gemini interpreta y redacta, pero NUNCA pone números: precios, stock, totales y estados
  salen de la base y de rules.py (`respuesta_segura` descarta textos con plata o "confirmado").
- `service.py` no debe depender de Telegram: va a sumarse WhatsApp. Los clientes se
  identifican por `canal` + `externo_id`.
- Los precios NO vencen (se sacó «vigente hasta» de todo).
- Clientes = empresas (pedidos grandes). Revisión del admin: el PRIMER pedido de cada cliente
  (REVISAR_CLIENTES_NUEVOS) hasta que tenga un pedido pagado; después todo se confirma solo.
  MAX_ORDER_TOTAL queda opcional (0 = apagado, por defecto). Producto sin precio → revisión.
- Los mensajes del chat NO se guardan en la base: la memoria de Gemini vive en RAM (12 h).
- Si Gemini no contesta: «dame un momento» (on_wait), reintentos (AI_RETRY_DELAYS) y si no,
  SIGUE CON REGLAS (decisión del usuario): si el mensaje nombra productos (o hay un borrador), lo
  atiende `_converse_rules`; si es una consulta, `AI_DOWN_MSG` («escribilo como pedido o más
  tarde»). Siempre aviso «Gemini no responde» en la web. Sin clave → reglas.
- Pago: el PRIMER pedido pregunta el medio tras confirmar (efectivo / transferencia); desde el
  segundo, el resumen trae el medio de siempre (`_usual_payment`, «como la última vez») con
  [✅ Confirmar] [💳 Cambiar pago] [❌ Cancelar] y confirmar deja el pedido hecho (se escribe
  «por transferencia» → confirma con ese medio) → pedido CONFIRMADO con
  pago «pendiente» (stock reservado) → el admin confirma el pago en la web → el bot avisa.
  Transferencia impaga se cancela sola a las PAGO_HORAS (estado CANCELADO, vuelve el stock).
  Pago = columnas pago_* en `pedidos`, aparte del estado. El cobro lo confirma el admin
  en la web (`confirm_payment`); Mercado Pago quedó DESCARTADO del plan.
- Lo agotado no se nombra: catálogo, Gemini y el parser solo ven productos con stock (`_sellable`).
- Stock: se verifica al armar el borrador y otra vez al confirmar. Si no alcanza, el cliente
  NO puede confirmar: el bot le dice la cantidad exacta que queda y ofrece ajustar
  (🔧 / «sí»). Al modificar, lo que el pedido ya tenía cuenta como disponible.
  La revisión del admin queda para el primer pedido de cada cliente.
- Ningún pedido se registra sin dirección de envío ni sin confirmación del cliente.
  El envío siempre está incluido, sin costo.
- La dirección guardada solo cambia si el cliente lo elige: tras un pedido a otra dirección,
  el bot pregunta cuál dejar (🏠 la de siempre / 📍 la nueva). La primera se guarda sola.
- Tienda ficticia de ejemplo: «Trazr» (valores por defecto en `config.TRAZR`).
- Nunca commitear `.env`, `credentials.json` ni la base (`data/`). EL REPO ES PÚBLICO: en el primer commit
  (29/10) el token de Telegram estaba en `.env.example` y alguien lo usó (19/11, bot respondiendo spam);
  el usuario lo revoca con BotFather. `tests/test_sin_secretos.py` falla si aparece una clave en el repo.
- Los tests no deben llamar a APIs reales (`Settings.ai` viene apagado por defecto;
  para probar con IA se pasa un cliente simulado a `BotService(ai_client=...)`). Correr con `python -m pytest -q`.

## Herramientas del proyecto
- Agente `consultor` (`.claude/agents/consultor.md`, modelo Fable, solo lectura): consultarlo
  antes de decisiones de arquitectura, diseño o alcance. Compara opciones y recomienda una.
- Plugins para la fase 4: `frontend-design`, `audit-suite` (skills de Emil Kowalski:
  `web-animation-design`, `emil-design-engineering`), `figma`, `playwright` (navegador para
  probar la web y sacar capturas).

## Plan por fases
0. Preparación — hecha
1. Gemini como intérprete — hecha y conectada en `service.py` (respaldo: reglas)
2. SQLite + dirección obligatoria (envío siempre incluido, sin costo) — hecha
3. Confirmación del pedido y de la dirección (botones), modificación de pedidos hechos — hecha
4. **Web de administración** (FastAPI) — SOLO para el admin
5. **WhatsApp Cloud API** (webhook en la misma app FastAPI) — EN CURSO (ver abajo)
6. ~~Mercado Pago~~ — DESCARTADO por el usuario (los pagos siguen: efectivo o transferencia,
   confirmados por el admin en la web)
7. Faltantes del TFI: recompra. CRÉDITO — POSTERGADO por el usuario (no programar nada de deuda
   hasta que lo pida; idea anotada: límite de deuda impaga por empresa → revisión o pago
   anticipado). Planes/precios: fuera del plan (la landing no los muestra).
8. (A evaluar al final) web para clientes. Por ahora el canal del cliente es el chat.

## Próxima sesión — empezar por acá

### Landing page (TFI) — HECHA
- Decidido: para DISTRIBUIDORAS (Trazr empresa de software), React (Vite) en `landing/`, compilada a
  estáticos que sirve FastAPI en `/`; la web del admin pasa a `/admin` (/privacidad y
  /whatsapp/webhook no se mueven). Conexión: botones «Probar el bot» (wa.me / t.me desde el .env) e
  «Ingresar» al panel. Textos en el código (sin API, nunca «Cargando…»). SIN planes ni precios.
  NO dar datos del negocio de prueba (catálogo, mayorista, cantidades, horarios): Trazr se vende a
  muchas distribuidoras; hablar en genérico («tus categorías», «tus horarios»).
  Separar «Hoy» de «Próximamente» (crédito, handoff, recompra, Tango): no prometer lo que no está.
- Diseño: canvas https://claude.ai/artifact/JSU973J8yFvvhyP7jE8DwD (aprobado; aviso de demo: «Es una
  demo: pedí lo que quieras, no se cobra ni se entrega nada»).
- PROGRAMADA: `landing/` (React 18 + Vite 5; textos en `src/contenido.js`; `npm run build` →
  `app/web/landing/`, que SE COMMITEA para no necesitar Node). `main.py`: GET `/` sirve el index
  reemplazando `__TRAZR_CONFIG__` por los links (LANDING_WHATSAPP → wa.me, LANDING_TELEGRAM o el
  usuario del bot en marcha → t.me; sin link, el botón no se muestra); `/landing/assets` estático.
  Panel movido a `/admin` (login `/admin/ingresar`); links viejos (/ingresar, /pedidos…) redirigen.
- La beta anterior (React + API en C#, otro equipo) se descartó: prometía Tango/facturación.

### Documento del TFI — ACTUALIZADO (18/11)
- Word/PDF armado con docx-js (script fuera del repo, en el scratchpad de la sesión). Materia: «Administración
  de Sistemas de Información». Sin Mercado Pago, SIN planes ni precios (se sacó «Cómo facturamos»: el
  usuario decidió que ya no se hace); en su lugar «Modelo de negocio»: Trazr gana dinero VENDIENDO el sistema
  a distribuidoras de bebidas (incluye instalación, catálogo, configuración, canales, capacitación,
  acompañamiento; adaptaciones a medida aparte; sin precios). ERS al día (RF01–RF23: landing = RF21, atención por una persona = RF22, uso de WhatsApp/avisos
  pendientes = RF23), figuras: chat, panel, landing, atención. Actualizado otra vez el 19/11 (número propio,
  sin plantillas pagas, 364 pruebas, anexo con enlaces; falta el t.me del bot y la presentación).

### WhatsApp sin costo extra — 19/11 (decisión del usuario)
- Chip propio comprado: va DIRECTO a la Cloud API (camino A: sin app WhatsApp en ese número; la
  coexistencia exige ser Tech Provider/BSP → descartada). Que escriba todo el mundo + QR de WhatsApp.
- PLANTILLAS APAGADAS (las cobra Meta): `WhatsAppConfig.templates` (WHATSAPP_PLANTILLAS=no por defecto).
  Aviso de la web fuera de 24 h → se intenta normal; si Meta lo rechaza (131047, sync o «failed»), queda
  en `WhatsAppClient._held` (RAM) + aviso «Aviso pendiente» en el panel, y `flush()` lo manda cuando el
  cliente vuelve a escribir. Las respuestas del bot no se pueden evitar (posible cobro desde 10/2026).
- ATENCIÓN POR UNA PERSONA (handoff) — HECHA 19/11: tabla `atenciones` (solo quién/por qué/desde; NO
  mensajes). Disparador automático SOLO cuando el cliente pide una persona (`_wants_human`, HUMAN_PHRASES)
  → HANDOFF_MSG + aviso «atencion»; mientras está abierta el bot no contesta (`_handle_message` devuelve [] y
  guarda la línea en la charla en RAM). «Lo consulto con el equipo» y Gemini caído siguen como antes (aviso /
  AI_DOWN_MSG): el usuario decidió esas reglas; el admin puede «Atender yo» desde Clientes. Panel
  `/admin/atencion` (contador en el menú; charla de las últimas 12 h en memoria, HISTORY_KEEP=40; responder;
  «Devolver al bot» → RELEASE_MSG). `admin.attention_*`; tests en tests/test_atencion.py.
- NÚMERO DEL BOT (19/11): eSIM limpia +54 9 3865 65-6821 (wa.me/5493865656821). QR y afiche en
  `app/web/static/marca/trazr-qr-whatsapp(-afiche).png` → wa.me/5493865656821?text=Hola (verificados con
  lector). El chip anterior (+54 9 381 556-8463) estaba en la API de otro negocio (SALEM): soporte de
  WhatsApp ofrece liberarlo con comprobante; quedó de repuesto. Falta: verificar en Meta (SMS/llamada),
  PHONE_NUMBER_ID y WABA_ID nuevos en .env, registrar con PIN, subscribed_apps, token permanente,
  LANDING_WHATSAPP=5493865656821.
- NÚMERO NUEVO EN MARCHA (19/11): cuenta de WhatsApp «Trazr» (WABA 1621445749095062), número
  +54 9 3865 65-6821 (phone number ID 1458479824016739) registrado, webhooks suscriptos, token permanente
  de usuario del sistema `trazr-bot`; el bot contesta. App de Meta PUBLICADA (Live) el 19/11: le puede
  escribir cualquiera. PROBADO con un tercero (19/11): contestó y el pedido llegó al panel. SIN tarjeta en Meta (decisión del usuario): 1.000 respuestas gratis por mes y por número;
  pasado eso no salen (no se cobra). Contador en el tablero: «WhatsApp este mes» (`whatsapp_month`,
  eventos `wa_enviado` vía `WhatsAppClient.on_sent`; no cuenta plantillas ni el «leído»;
  WHATSAPP_GRATIS_MES=1000).
- TELEGRAM: bot @asistenteTRAZRbot (https://t.me/asistenteTRAZRbot). QR distinto al de WhatsApp (fondo limón,
  franja verde): `app/web/static/marca/trazr-qr-telegram(-afiche).png`, verificado con lector.
- SERVIDOR PERMANENTE — EN MARCHA (19/11): **https://trazr.tech** (landing `/`, panel `/admin`, privacidad
  `/privacidad`, webhook `/whatsapp/webhook`). Google Cloud DESCARTADO (pedía tarjeta/depósito). **Azure for
  Students** (sin tarjeta, US$ 100 por 12 meses): VM Arm **Standard_B2pts_v2**, Ubuntu 24.04 Arm64, región
  Canadá, IP pública estática 52.138.17.238 (`trazr-ip`, se paga con el crédito), puertos 22/80/443. Dominio
  **trazr.tech** gratis 1 año (GitHub Student Pack, get.tech; registro A → la IP; hay que renovarlo o se pierde).
  FreeDNS trazr.bot.nu quedó sin usar. Instalado con `scripts/instalar_servidor.sh trazr.tech` (Caddy con
  HTTPS automático, uv/Python 3.13, servicio systemd `trazr` en /opt/trazr; correrlo de nuevo = actualizar
  desde `development`). El .env (NGROK_DOMAIN vacío) y data/trazr.db se subieron con scp. Entrar:
  `ssh trazr@52.138.17.238`; registro: `sudo journalctl -u trazr -f`; admins:
  `cd /opt/trazr && sudo -u trazr .venv/bin/python -m scripts.crear_admin ...`. La PC del usuario ya NO corre
  el servidor (dos instancias = Conflict de Telegram). Meta: webhook, privacidad y sitio web → trazr.tech.
  Los QR no cambian (apuntan a wa.me / t.me). TFI actualizado con trazr.tech (servidor en la nube, anexo).
- MENSAJES ATRASADOS (19/11): tras la migración, Meta reintentó un mensaje que no se había podido entregar y el
  bot lo contestó 50 min después, fuera de contexto. Ahora los mensajes con más de MENSAJE_ATRASO_MAX_MIN (15)
  de atraso no se contestan (`messages.too_late`; WhatsApp usa el `timestamp` de Meta, Telegram `message.date`;
  los botones no tienen hora y se atienden siempre). 0 = contestar todo. Landing: «Pedir por Telegram» con ícono
  (hotfix en main, PR #3).
- Sitio anterior (ngrok, solo con la PC prendida): https://residence-pod-pug.ngrok-free.dev — reemplazado.
- Meta: la app usaba «Test Messaging Account» (solo admite el número de prueba). Para el chip se
  crea una cuenta de WhatsApp nueva «Trazr» en el portfolio; antes Meta exigió completar la
  información del negocio (dirección, teléfono, sitio, mail). Después: nuevo WHATSAPP_WABA_ID y
  WHATSAPP_PHONE_NUMBER_ID en el .env, registrar el número (PIN) y suscribir la app (subscribed_apps).

### Rama main — 19/11
- `development` se pasó a `main` (PR #2, merge) a pedido del usuario. Se sigue trabajando en `development`;
  el servidor se actualiza desde `development` (script de instalación). El anexo del TFI apunta al repo
  (sin rama). Falta: la presentación (ofrecí armarla con la identidad de Trazr). Catálogo revisado por el
  usuario (19/11): vinos caja 6, Quilmes Negra ok, Reserva San Juan = coñac (Licores).

### Agradecimiento al cobrar — HECHO
- `confirm_payment` → `_thanks(order)`: transferencia = «¡Gracias por tu compra! Recibimos tu transferencia:
  pronto estaremos llegando con tu envío»; efectivo = «¡Gracias por tu compra! Te esperamos en el próximo
  pedido». Plantillas nuevas `trazr_gracias_transferencia` / `trazr_gracias_efectivo` (1 variable: pedido)
  reemplazan a `trazr_pago_recibido`: el usuario tiene que crearlas con `--crear` (las viejas dan
  «ya existe», es normal).

### Evaluación con Gemini real — 19/11: 74/74 (100 %)
- Todos los temas completos (simple 11/11, jerga 8/8, ambigüedad 5/5, cajas 11/11, cambios 8/8,
  confirmar 5/5, dirección 4/4, pago 7/7, consultas 5/5, saludo/lo de siempre 6/6, stock 2/2,
  seguridad 2/2). 87 consultas, mediana 1,3 s, p95 2,7 s. Es la línea de base para próximos cambios.

### Plan de mejora del bot (con el consultor) — propuesto, falta el OK del usuario
Página: https://claude.ai/artifact/1h6opp7ZJDfQngy1FAn6it. Etapas 0 a 3 HECHAS.
- Bugs confirmados en el simulador: esperando dirección, cualquier texto ≥8 letras se toma como
  dirección («esperá, agregale 2 fantas»); esperando pago, «no tengo plata, cancelá» elige efectivo
  y REGISTRA el pedido (answer_payment busca palabras sueltas, efectivo antes que cancelar).
- Núcleo nuevo: pedido en curso + operaciones (agregar/fijar/quitar/vaciar, unidad|bulto) que
  devuelve Gemini en un JSON por turno; pendientes (dudas) guardados en el Draft y resueltos con
  botones/listas; una sola voz (sistema) en pasos transaccionales.
- Etapas: 0 red de seguridad (bugs, sí/no sin Gemini, timeout, «escribiendo» WhatsApp, golden set
  ~50 casos + eventos) → 1 pedido en curso → 2 bultos (unidades_por_bulto) → 3 voz/forma, pago en
  el resumen, «lo de siempre» → 4 handoff a humano + métricas (Mercado Pago descartado).
- PRÓXIMO (a decidir con el usuario): ETAPA 4 = el admin habla con el cliente. Recomendado:
  desde la web (el bot se pausa para ese cliente; aviso «necesita atención» con contador; la web
  muestra la charla reciente desde la RAM —o guardarla, cambiando la regla—; el admin escribe y
  sale por el mismo canal/número; «Devolver al bot»). Disparadores: pide una persona, 3
  aclaraciones fallidas, «lo consulto con el equipo», Gemini caído con una consulta. Opcional:
  aviso al celular del admin (hoy decidido que el admin no recibe nada por chat). Alternativa:
  «coexistencia» de Meta (app WhatsApp Business + API en el mismo número) al pasar al número
  propio.
- CATÁLOGO REAL CARGADO: `catalogo/mayorista_tucuman.csv` (242 productos de la lista del mayorista,
  precio por unidad = precio del bulto / unidades; «2 x $X» → X/2; promos ignoradas; stock de
  distribuidora grande en cajas enteras). Se carga con
  `python -m scripts.init_db --csv catalogo/mayorista_tucuman.csv --reemplazar` (lo que no está en el
  CSV queda inactivo, no se borra). Parser: entre coincidencias parciales gana la que comparte más
  palabras («fernet buhero» → Buhero).
- TAMAÑOS: `parser.pull_size`/`family`/`pick_size`: productos de la misma familia (nombre sin
  tamaño ni envase) se eligen por lo que dice el cliente («de litro», «750», «2 1/4», «lata», «latón»,
  «porrón», «chico» = la más chica sin contar miniatura, «grande»). Sin tamaño → duda «¿de qué
  tamaño?» con un botón por opción (pk:<token>:<sku>) o escribiendo («la de 750»). «2 cajas de
  branca» → al elegir, cajas de esa presentación (sin caja cargada → pregunta unidades). Si Gemini
  elige un SKU y el texto no dice tamaño, igual se pregunta. Productos nuevos del CSV van al FINAL
  de su categoría (los SKU son por orden: no correrlos).
- ETAPA 0 HECHA: bugs de dirección (`_looks_like_address`: número o palabra de calle, y que no
  nombre productos ni empiece con «agregale/esperá…») y de pago (`_payment_choice`: un solo medio
  sin negar; cancelar primero; ante la duda vuelve a preguntar con botones); `_wants_confirm` /
  `_wants_cancel` sin Gemini; palabras clave (`_keyword`: catálogo, mis pedidos, ayuda,
  modificar, mi dirección; saludo sin Gemini → bienvenida); textos sin comandos con barra;
  cajas/packs → error "bulto" (parser.BULK_WORDS, también si Gemini pone número); Gemini 8 s y
  1 reintento; WhatsApp leído + «escribiendo»; «lo consulto con el equipo» → aviso "consulta";
  tabla `eventos` (sin texto) + `scripts/metricas.py`; set de prueba `tests/golden/casos.json`
  (56 casos) + `scripts/evaluar.py` (Gemini real, a mano; `--sin-ia`). Base con reglas: 42/56.
  Base con Gemini real (flash-lite): 53/56 (95 %), mediana ~1,5 s. Fallas: simple-09 («un par»
  → 1), bulto-04 (un renglón con duda tira todo el mensaje), cambio-04 («cambiá X por Y» no
  conserva la cantidad).
- ETAPA 1 ACHICADA — PROGRAMADA (OK del usuario: bloquear «sí» con dudas, «un par» = 2, postergar
  operaciones). `Draft.pending` + `_can_wait`/`_pending_from`/`resolve_pending`/`skip_pending`
  (botón sp:) /`_pending_question`; `confirm_draft` y `_place_order` no avanzan con pendientes;
  `_draft_for_ai` los pasa a Gemini. Solo en pedidos nuevos (no en modificaciones). Reglas: 50/62
  (46/56 originales). Con Gemini: simple 11/11, bulto 8/8, cambio 8/8 (centinela cambio-08 OK →
  operaciones siguen postergadas), duda 5/5. Falta correr el resto de los grupos para confirmar
  que no hubo regresión.
- ETAPA 2 (cajas) — PROGRAMADA. Decisión del usuario: a empresas se vende caja cerrada Y unidad
  suelta (25 con caja de 12 = 2 cajas + 1 suelta); cada presentación tiene su caja. Producto:
  `pack_size` (0 = sin caja cargada → el bot pregunta) y `pack_name` (caja/pack/fardo/cajón);
  columnas `unidades_bulto`/`nombre_bulto` (migración ALTER). Stock y precio siguen en UNIDADES.
  `parser.bulk_units` y en Gemini (`_ai_items_a_parsed`): las unidades las calcula el código.
  «sueltas» = unidades. Resumen, comprobante y web muestran «(2 cajas + 1 suelta)»
  (`Product.pack_label`). Web: Productos → unidades por caja y nombre; CSV: UnidadesPorCaja,
  NombreCaja. Catálogo de ejemplo con cajas (SPR1 sin caja, para probar la pregunta). Set: 65
  casos (bulto-01..11); reglas 53/65. Falta: correr bulto con Gemini; ¿la caja tiene otro precio?
  (hoy = unidades × precio unitario). DECIDIDO: la caja cuesta lo mismo que sus unidades.
- ETAPA 3 (parcial) — PAGO EN EL RESUMEN, programado: `Draft.payment` se precarga con el medio del
  último pedido; `confirm_draft` coloca el pedido si hay medio; botón cp: (`change_payment`);
  `_quick_answer` (sí/no/medio escrito/dirección suelta solo sin Gemini) antes de llamar a Gemini;
  palabras «cambiar dirección», «cambiar pago». Primer pedido y modificaciones: como antes. Set:
  68 casos (pago-05..07).
- ETAPA 3 COMPLETA: «lo de siempre» (`_wants_repeat` en service: frase corta sin cambios ni números →
  `repeat_last_order`, precios de hoy, mismo resumen; con cambios → Gemini con la lista completa);
  saludo (GREETINGS, con o sin Gemini, sin pisar un borrador) por nombre con botones
  [📋 Ver catálogo] (ct:) y, si ya compró, [🔁 Lo de siempre] (rp:); `ship_day()` «hoy / mañana /
  el lunes 16/11» por TURNOS (usuario: a la siesta no se reparte): confirmado antes de las 11
  (NEGOCIO_HORA_CORTE_MANANA) → hoy a la mañana (9 a 13); antes de las 19 (NEGOCIO_HORA_CORTE) →
  hoy a la tarde (17 a 21); si no, próximo día (NEGOCIO_DIAS_REPARTO lun-sab) a la mañana; en resumen y
  comprobante; plata sin «,00»; comprobante compacto (fecha «13/11 10:22», sin «Estado» si hay
  pago, «Total · envío incluido»). Horario nuevo (usuario): lunes a sábado 9–13 y 17–21
  (TRAZR, NEGOCIO_HORA_CIERRE=21). Set: 74 casos; reglas 61/74. Con Gemini: siempre 6/6.
  Catálogo: hasta 15 productos va entero; si no, primero categorías (botones cc:<categoría>, en
  WhatsApp lista «Ver categorías», `Outgoing.menu_label`), o escribiendo el nombre de la categoría.
  Un mensaje por paso: `_join` une la pregunta «¿qué dirección dejo guardada?» al comprobante.
  Arreglo: Gemini tomaba «3 fanta» después de un pedido como cambio al anterior → si no hay
  palabras de cambio (`CHANGE_CUES`), «cambio_pedido» sin borrador se trata como pedido nuevo.
  Detalle original: pendientes simples en el Draft
  (se arma con lo entendido, lo dudoso queda en Draft.pending; no se confirma con pendientes salvo
  «Seguir sin eso»; un número suelto completa el único pendiente), «un par» = 2 (parser + prompts),
  regla «cambiar X por Y conserva la cantidad» en CHAT_PROMPT. Operaciones (agregar/fijar/quitar)
  POSTERGADAS; se reabren si falla el caso centinela cambio-08 (pedido largo). Casos nuevos:
  bulto-06/07/08, simple-11, cambio-07, cambio-08. Listo = las 3 fallas + 6 nuevos pasan,
  ≥59/62 con Gemini sin regresión (2 de 3 corridas), reglas ≥42/56.
- Decisiones pendientes del usuario: cómo se venden los bultos; pago dentro del resumen; voz;
  funciones de WhatsApp (sí escribiendo/listas, no Flows/catálogo Meta); handoff; avisos con texto.

### Fase 5: WhatsApp — en curso
1. App de Meta creada (caso de uso WhatsApp) con número de prueba; el «Hello World» llegó. HECHO.
2. Canal programado: `app/whatsapp.py`, `WhatsAppConfig` en config (WHATSAPP_TOKEN,
   WHATSAPP_PHONE_NUMBER_ID, WHATSAPP_APP_SECRET, WHATSAPP_VERIFY_TOKEN), registrado en el
   Notifier y en `server.py`; tests en `tests/test_whatsapp.py`. HECHO.
   Detalles: HTML del bot → *negrita* _cursiva_ `código`; botones → reply buttons (≤3, títulos
   ≤20; más de 3 → lista); resumen >1024 caracteres va aparte de los botones; Argentina: si Meta
   rechaza 549… (131030) se reintenta sin el 9 y se recuerda.
3. Túnel: ngrok con dominio fijo gratis. Si NGROK_DOMAIN está en el .env, `python -m app.server`
   lo abre solo (`app/tunnel.py`, subproceso; Ctrl+C apaga todo). El usuario prefiere UN comando
   en la terminal de VS Code (se descartó iniciar.bat/.ps1). Antes: cloudflared quick tunnel (la URL cambiaba al reiniciarlo) + webhook en Meta (campo
   «messages») + suscripción de la app a la WABA (POST /<WABA_ID>/subscribed_apps). HECHO:
   el bot contesta por WhatsApp. Flujo completo probado por el usuario (pedido → aprobar en la
   web → aviso → pago recibido → aviso): FUNCIONA.
4. Plantillas para avisos fuera de la ventana de 24 h — PROGRAMADO: `Outgoing.template`
   (clave + valores) en approve/reject/confirm_payment/cancel_unpaid; `PLANTILLAS` en whatsapp.py
   (utilidad, es_AR). Si no hubo mensaje del cliente en 23,5 h (memoria) → plantilla directa;
   si no, mensaje normal y, ante 131047 (en la respuesta o en un estado «failed» del webhook),
   plantilla. Se crean con `python -m scripts.plantillas_whatsapp --crear` (WHATSAPP_WABA_ID).
   Creadas por el usuario: en espera de aprobación de Meta.
   Política de privacidad pública en /privacidad (#borrar = instrucciones de borrado); NEGOCIO_CONTACTO.
5. Pendiente (producción): token permanente (usuario del sistema), número propio,
   verificación del negocio, servidor con HTTPS.

### Fase 4: web de administración — HECHA (usuarios reales creados por el usuario)
Decisiones tomadas (usuario + consultor):
- **Revisión del admin solo en la web.** El chat es solo para clientes: se QUITAN los comandos
  de admin del chat (/stock, /setstock, /sumar, /precio, /revision, /pedido, /aprobar,
  /rechazar) y los botones `ap:`/`rj:`. El admin no recibe nada por Telegram.
- **Avisos al admin solo en la web:** lo que hoy manda `_admins()` (pedido en revisión, pedido
  modificado, cambio fuera de término, falla de base) se guarda en una tabla `avisos` y se ve
  en la web (contador + lista), no por chat.
- **Varios admins:** tabla `admins` en SQLite (usuario, nombre, hash scrypt de la contraseña
  con `hashlib`, activo). Script para crear admins. `Revisado_por` = admin logueado.
- **Pantallas:** pedidos (filtro por estado) + detalle con aprobar/rechazar con motivo;
  stock, precios y productos (alta/edición, reemplaza al CSV); clientes (dirección y pedidos);
  tablero (ventas del día/semana, en revisión, poco stock, avisos).
- **Un solo proceso:** FastAPI con el bot de Telegram dentro del lifespan
  (`initialize/start/updater.start_polling`), un worker, sin --reload. Un comando levanta todo.
- **Reutilizar lógica sin duplicar:** `approve/reject` devuelven un resultado (ok, mensaje,
  pedido, avisos para el cliente); `set_stock/add_stock/set_price` reciben valores tipados;
  `Outgoing` suma `channel`; un `Notifier` envía al canal del cliente (Telegram ahora,
  WhatsApp en la fase 5). Validaciones de alta de productos en `BotService`, no en las rutas.
- **Frontend:** FastAPI + Jinja2 server-rendered, formularios clásicos (POST + redirect) y CSS
  propio; animaciones sutiles con CSS (transiciones, View Transitions). HTMX, si hace falta,
  en una segunda pasada y puntual.
- **Seguridad:** sesión con `SessionMiddleware` (cookie firmada, httponly, samesite=lax),
  `SECRET_KEY` en `.env`, token CSRF en formularios. Dependencias nuevas: fastapi, uvicorn,
  jinja2, python-multipart, itsdangerous.
- **Tests:** `TestClient` con base temporal y notificador simulado. Playwright solo para
  capturas/demo, no en la suite.

Diseño:
- Sistema de diseño «Trazr Admin» (Claude Design): https://claude.ai/artifact/DLtCSwjvKvMqwU2A3r53Qt
  Verde `monte` (marca/acción) + amarillo `limon` (identidad, solo como fondo), estados
  ok/warn/danger, Archivo (títulos) + Inter (texto) + IBM Plex Mono (IDs/SKU). Componentes
  como clases CSS `tz-*` (Button, StatusBadge, OrdersTable, StatTile, Field, Notice): su
  `bundle.css` se copia a la web real. Aprobado por el usuario.
- Pantallas (canvas de Claude Design): https://claude.ai/artifact/TxLisYokWRD85qC63RVNH9
  Tablero, Pedidos (filtro por estado), Detalle de pedido (aprobar con precio nuevo / rechazar
  con motivo, historial), Productos y stock (sumar stock en la fila, editar producto),
  Clientes (con detalle y pedidos) e Ingreso. Su `admin.css` (variables + estructura de página)
  es la base del CSS de la web real. Aprobado por el usuario.

Orden de trabajo:
1. Diseño en Claude Design (Design System de Trazr + pantallas) → aprobado. HECHO.
2. Backend: quitar admin del chat, avisos y admins en la base, resultados tipados, Notifier. HECHO
   (admin.py, auth.py, notifier.py, scripts/crear_admin.py; ADMIN_CHAT_IDS eliminado).
3. Web: login, pantallas según el diseño aprobado, tests. HECHO (tests/test_web.py).
4. Capturas con Playwright para revisar. HECHO (para el TFI se pueden repetir a escala 2).
Usuarios reales creados en la PC del usuario: fase 4 cerrada.

Ajustes posteriores (hechos): web que se refresca sola (/cambios), menú hamburguesa y tablas
como tarjetas en el celular, tablero propio de Trazr (remito del día, el día en reparto con el
corte de las 13, cola de decisiones, se está por acabar).

Método de diseño: partir de buenas referencias, skills de diseño y diseño real antes de programar.
- Claude Design (artifacts «Design System» y «Design»); Figma como alternativa (el MCP de Figma
  hoy no conecta: la red del entorno lo bloquea).
- Skills: `frontend-design`; `web-animation-design` y `emil-design-engineering` (audit-suite);
  `css-animations`/`ui-motion` (hzblj-skills). GSAP no hace falta para esta web.
