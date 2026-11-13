# Trazr — bot de pedidos de bebidas para Telegram

Bot de Telegram que recibe pedidos de bebidas en lenguaje natural, consulta stock
y precio en tiempo real, confirma solo lo que está dentro de los
parámetros, deriva el resto al administrador y registra todo en una base de datos
SQLite. El envío está **siempre incluido, sin costo**, y cada pedido lleva la
dirección de entrega del cliente.

TFI de Administración de Recursos — UTN-FRT, comisión 4K1.

---

## 1. Decisiones técnicas

| Elección | Por qué |
|---|---|
| **Python 3.10+** | Ecosistema maduro para bots; fácil de leer y modificar. |
| **python-telegram-bot 21** | Librería recomendada por Telegram, asíncrona, con botones inline. Usa *long polling*: no hace falta dominio ni abrir puertos. |
| **SQLite** | Toda la información (catálogo, clientes, pedidos) en un solo archivo, sin servidor ni credenciales. Transacciones reales: un pedido se registra completo o no se registra. La propia base impide stock negativo y pedidos sin dirección. Viene incluida en Python. |
| **Gemini conversacional** | Entiende jerga y el contexto de la charla («agregale una más», «con gas»), distingue pedidos, consultas y pedidos de cambio, y redacta las respuestas con tono humano. **Límite:** precios, stock, totales y estados los pone el bot desde la base; si Gemini escribe un precio o da un pedido por confirmado, su texto se descarta. Si Gemini falla, el bot sigue con el parser por reglas y sus mensajes de siempre. Necesita `GEMINI_API_KEY`; al arrancar, el bot avisa si quedó apagado. |
| **Parser por reglas** (respaldo) | Funciona sin claves ni costo. Tolera tildes, plurales, errores de tipeo, «x 3», «media docena», números en palabra. |

## 2. Regla «dentro de los parámetros»

**Stock:** se verifica **antes** de armar el pedido. Si algo no alcanza, el cliente **no puede
confirmarlo**: el bot le dice cuánto queda de cada producto («Cerveza Rubia 1L: pediste 10, me
quedan 5») y le ofrece **🔧 Ajustar a lo que hay**, o mandar otra lista. Un producto sin stock
no se puede pedir. Al confirmar se vuelve a mirar (otro cliente pudo llevárselo).

Con stock suficiente, un pedido se **confirma automáticamente** si además:

1. **Producto existente**: está en el catálogo, activo y con precio cargado.
2. **Cliente verificado**: la empresa ya tiene al menos un pedido pagado. El **primer pedido** de
   cada empresa nueva va a revisión para verificarla una vez (`REVISAR_CLIENTES_NUEVOS=true`);
   desde que paga uno, los siguientes se confirman solos, sin importar el monto.
3. *(Opcional)* el total no supera `MAX_ORDER_TOTAL` (0 = sin límite, el valor por defecto: los
   pedidos de empresas son grandes).

Los precios **no vencen**: se cambian desde la web cuando hace falta.

| Situación | Qué pasa |
|---|---|
| Cumple todo | **CONFIRMADO**, se descuenta el stock, el cliente recibe el detalle. |
| No alcanza el stock | No se puede confirmar: el bot dice cuánto queda y ofrece ajustar. No se registra nada. |
| Primer pedido de una empresa nueva (o supera el total máximo, si está activado) | **EN REVISION**, no se toca el stock, queda un aviso para el admin en la web, donde lo aprueba o rechaza. |
| Producto inexistente | Por defecto se le pide al cliente que corrija (no se registra). Con `ON_UNKNOWN_PRODUCT=review` se registra en revisión. |
| Mensaje ambiguo, sin cantidad, cantidad inválida | Se pide aclaración. No se registra nada. |
| El cliente no tiene dirección guardada | El pedido queda **retenido** y se le pide la dirección (ver §3). |
| Falla de la base de datos | Mensaje claro al cliente y aviso con el error para el admin en la web. No queda nada a medias. |

Todo se ajusta en el `.env` (`RULE_REQUIRE_STOCK`,
`MAX_ORDER_TOTAL`, `MAX_QTY_PER_ITEM`, `ON_UNKNOWN_PRODUCT`, `MATCH_THRESHOLD`).

**El chat es solo para clientes.** Todo lo del administrador (avisos, revisar, aprobar,
rechazar, stock, precios, productos) se hace en la **web de administración** (fase 4). El admin
no recibe nada por Telegram: los avisos quedan guardados y se ven en la web.

**Al aprobar** un pedido en revisión se toma el precio actual de cada producto y se vuelve a
verificar el stock **en ese momento**; si falta, no aprueba y dice qué resolver. Al rechazar,
el motivo es obligatorio y le llega al cliente.

## 3. Confirmación, dirección y modificación de pedidos

**Nada se registra sin que el cliente lo confirme.** El flujo (`app/checkout.py`):

1. El cliente escribe su pedido. El bot arma un **borrador**: no registra nada ni toca el stock.
   Si no alcanza el stock, se lo dice con la cantidad que queda y le ofrece ajustar (ver §2).
2. Si el cliente no tiene dirección guardada, se la pide. Ningún pedido se registra sin
   dirección (lo valida el bot **y** la base de datos).
3. Le muestra un **resumen** con los precios actuales, el total, el envío (incluido, sin
   costo) y la dirección guardada. Si el pedido va a quedar en revisión (supera el total
   máximo), se lo avisa antes. Debajo, tres botones:
   **✅ Confirmar** · **📍 Cambiar dirección** · **❌ Cancelar**.
4. Antes de confirmar puede cambiar lo que quiera: mandar la lista nueva («mejor 3 cocas y
   una sprite»), otra dirección, o cancelar. Cada cambio genera un resumen nuevo (los botones
   de resúmenes viejos dejan de funcionar).
5. Recién al confirmar (botón, «sí» o `/confirmar`) se evalúa la regla con el stock y precio
   de ese momento y se registra **CONFIRMADO** o **EN REVISION**.

**Modificar un pedido ya hecho** (productos y/o dirección): durante `MODIFICACION_MINUTOS`
(60 por defecto) desde que se hizo, si no fue rechazado. Con `/modificar`, o diciéndolo
(«cambiame la fanta por una sprite», «mandalo a otra dirección»). El bot arma un resumen de
cómo quedaría y pide confirmación. Al confirmar devuelve el stock del pedido viejo, evalúa el
nuevo y descuenta lo que corresponde; el pedido conserva su ID. Si el cambio lo deja fuera de
los parámetros, vuelve a revisión y queda el aviso en la web. Fuera de término, el bot le
avisa al admin para que lo resuelva a mano.

**Pago.** Después de confirmar el resumen, el bot pregunta **cómo paga**: 💵 efectivo al recibir
o 🏦 transferencia (le pasa el alias `NEGOCIO_ALIAS` y el monto). El pedido queda **«pendiente de
pago»** con el stock reservado y el bot responde *«¡Pedido completado! Queda pendiente de pago»*.
En la web, el admin toca **«Confirmar pago recibido»** (en el detalle o directo en el tablero,
sección «Por cobrar») y el bot le avisa al cliente *«¡Recibimos tu pago!»*. Una transferencia que
no se acredita en `PAGO_HORAS` (24 h) se **cancela sola**: vuelve el stock y se le avisa al cliente;
también se puede cancelar a mano desde la web. El efectivo no vence: se marca pagado al cobrarlo.
Un pedido pagado ya no se modifica desde el chat.

**Lo que no hay no se nombra.** El `/catalogo` y Gemini solo ven lo que tiene stock. Si alguien
pide algo agotado (o que no vendemos), el bot sigue con lo que sí hay y dice *«Del resto que
pediste no tengo ahora»*; si no hay nada, *«No tengo eso ahora»*. De lo que queda poco, sí dice la
cantidad («pediste 10, me quedan 5»).

**Dirección guardada:** la primera que da el cliente se guarda sola. Si después un pedido (o
una modificación) va a otra dirección, la guardada **no** cambia: una vez registrado el pedido,
el bot pregunta cuál dejar para la próxima, con los botones **🏠 La de siempre** · **📍 La nueva**
(también se puede contestar escribiendo «la nueva» o «la de siempre»; si el cliente no contesta,
queda la de siempre). `/direccion nueva dirección` la cambia directamente.

Otros detalles: cada pedido guarda su **copia** de la dirección; un borrador sin confirmar
vence a las 24 h.

**Saludo y «lo de siempre».** Un saludo («hola», «buenas») recibe la bienvenida por el nombre con
[📋 Ver catálogo]; si ya compró, también [🔁 Lo de siempre], que arma su último pedido con los
precios de hoy (también escribiendo «lo de siempre» o «lo mismo que la otra vez»). El resumen y el
comprobante dicen en qué turno sale (a la siesta no se reparte): confirmado antes de las 11
(`NEGOCIO_HORA_CORTE_MANANA`) sale hoy a la mañana (9 a 13); antes de las 19 (`NEGOCIO_HORA_CORTE`),
hoy a la tarde (17 a 21); si no, el próximo día de reparto (`NEGOCIO_DIAS_REPARTO`, lun-sab) a la
mañana: «mañana a la mañana (9 a 13)» o «el lunes 16/11 a la mañana».

**Medio de pago en el resumen.** En el primer pedido, el bot pregunta cómo paga después de que el
cliente confirma. Desde el segundo, el resumen ya muestra su medio de siempre («Pago: efectivo al
recibir, como la última vez») con [✅ Confirmar] [💳 Cambiar pago] [❌ Cancelar]: confirmar deja
el pedido hecho en un paso. Escribir «por transferencia» en el resumen confirma con ese medio.

## 4. Cómo conversa el bot (Gemini)

Con cada mensaje, Gemini recibe: el catálogo (código, nombre, alias, categoría y si hay o
no stock, **sin precios**), los datos del negocio del `.env` (`NEGOCIO_*`), los últimos
pedidos del cliente y la conversación de las últimas 12 horas. Esa conversación vive **solo
en memoria**: los mensajes del chat **no se guardan en la base** y se olvidan al reiniciar.
Devuelve la intención y un texto corto.

**Si Gemini no contesta** (fallan los dos modelos), el bot le dice al cliente «Dame un momento
que estoy procesando tu mensaje», reintenta una vez (`AI_RETRY_DELAYS`, por defecto a los 3 s; cada intento espera hasta `AI_TIMEOUT_SECONDS`, 8 s) y,
si sigue sin contestar, le pide que escriba de nuevo en unos minutos y deja un aviso en la web.
Sin clave de Gemini (`GEMINI_API_KEY` vacía) interpreta el parser por reglas.

| Intención | Qué hace el bot |

| Intención | Qué hace el bot |
|---|---|
| pedido | Valida los productos y cantidades y sigue el flujo normal; el texto de Gemini va arriba del detalle. |
| consulta | Muestra el texto de Gemini + precio y disponibilidad **tomados de la base**. |
| cambio_pedido | Responde y le avisa al admin con el mensaje y el último pedido del cliente. |
| charla | Responde el texto de Gemini (saludos, preguntas para aclarar, horarios, etc.). |

### Medir al bot

- **Set de conversaciones de prueba** (`tests/golden/casos.json`, 56 casos: pedidos simples,
  jerga, ambigüedades, cajas y packs, cambios, confirmación, dirección, pago, consultas, stock y
  seguridad). Se corre a mano, contra Gemini real, antes y después de cada cambio:
  `python -m scripts.evaluar` (o `--sin-ia` para probar solo las reglas, `--solo duda -v` para
  ver las respuestas). Muestra cuántos casos pasan por tema y cuánto tarda Gemini.
- **Eventos** (tabla `eventos`): mensajes, botones, resúmenes, pedidos registrados o
  descartados, aclaraciones y demoras de Gemini. Nunca guardan el texto de los mensajes.
  Resumen: `python -m scripts.metricas` (o `--dias 30`).

## 5. Base de datos

Un archivo SQLite (`DATABASE_PATH`, por defecto `data/trazr.db`) que el bot crea
solo. Para verlo o editarlo a mano: [DB Browser for SQLite](https://sqlitebrowser.org/).

| Tabla | Contenido |
|---|---|
| `productos` | `sku` (PK), `producto`, `categoria` (Gaseosas, Aguas, Cervezas, Vinos, Aperitivos, Jugos, Energizantes), `alias` (separados por coma), `stock` (≥ 0, en unidades), `precio` (por unidad), `activo`, `actualizado`, `unidades_bulto` (unidades por caja; 0 = no se vende por caja), `nombre_bulto` (caja, pack, fardo, cajón) |
| `clientes` | `canal` + `externo_id` (únicos; ej. `telegram` + ID del chat), `nombre`, `direccion`, `creado`, `actualizado` |
| `pedidos` | `pedido_id` (PK, ej. `P261112-A3F1`), `fecha`, `canal`, `cliente_id`, `cliente_nombre`, `mensaje_original`, `direccion_envio` (obligatoria), `costo_envio`, `total`, `estado` (`CONFIRMADO` / `EN REVISION` / `RECHAZADO` / `CANCELADO`), `motivo`, `actualizado`, `revisado_por`, `pago_medio` (efectivo / transferencia), `pago_estado` (pendiente / pagado), `pago_fecha`, `pago_por` |
| `pedido_items` | renglones de cada pedido: `sku`, `producto`, `cantidad`, `precio_unitario`, `subtotal` |
| `pedidos_pendientes` | pedido retenido (ya interpretado) esperando la dirección del cliente |
| `avisos` | avisos para el admin (pedido en revisión, modificado, cambio fuera de término, falla); se ven en la web |
| `admins` | usuarios de la web: usuario, nombre, hash de la contraseña, activo |
| `eventos` | qué pasó en cada conversación (mensaje, botón, resumen, pedido registrado…), con demora; nunca el texto |

**Cajas, packs y fardos.** Cada presentación es un producto con su propia caja (unidades por caja
y cómo se llama), que se carga en la web (Productos → Editar) o en el CSV. Stock y precio siguen en
unidades. Si el cliente pide «2 packs de coca» y el pack trae 6, el bot anota 12; si pide 25 y la
caja trae 12, el resumen y la web muestran «25 u. (2 cajas + 1 suelta)» para armar el pedido. Si
un producto no tiene caja cargada y el cliente pide cajas, el bot pregunta cuántas unidades son.

### Cargar el catálogo

```bash
python -m scripts.init_db --ejemplo           # 10 bebidas de ejemplo (si el catálogo está vacío)
python -m scripts.init_db --csv catalogo.csv  # alta/actualización desde un CSV
```

El CSV lleva el encabezado `SKU,Producto,Categoria,Alias,Stock,Precio,Activo`
(coma o punto y coma; se puede exportar desde Excel), y opcionalmente `UnidadesPorCaja` y
`NombreCaja` para vender por caja. Si un SKU ya existe, se
actualiza. Nunca se borra nada. Los ejemplos incluyen uno con poco stock (`CER1`) y uno sin
stock (`VIN1`).

## 6. Puesta en marcha

### Bajar o actualizar el proyecto

En Windows (PowerShell), desde la carpeta del proyecto (o donde lo quieras bajar):

```powershell
powershell -ExecutionPolicy Bypass -File actualizar.ps1
```

En Mac o Linux: `bash actualizar.sh`. Clonan el repo si no está, traen lo último de la rama
`development`, crean el entorno virtual e instalan dependencias. No tocan tu `.env` ni tu base,
y si tenés cambios sin guardar en git no hacen nada.

### Paso 0: probar sin Telegram

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # Windows: copy .env.example .env
python -m scripts.init_db --ejemplo
python -m scripts.simulate         # chateás con el bot en la consola
python -m pytest -q                # corre todos los tests
```

### Paso 1: crear el bot en Telegram

1. En Telegram abrí **@BotFather** → `/newbot`.
2. Elegí un nombre visible y un usuario terminado en `bot`.
3. BotFather te da un **token**. Pegalo en `.env` como `TELEGRAM_BOT_TOKEN=...`.
   Es secreto: si se filtra, en BotFather usá `/revoke`.

### Paso 2: crear los usuarios de la web de administración

```bash
python -m scripts.crear_admin lucia "Lucía Fernández"
```

Pide la contraseña sin mostrarla (mínimo 8 caracteres) y la guarda con hash, nunca en texto
plano. Se pueden crear varios admins; en cada pedido queda quién lo aprobó o rechazó.
Las contraseñas no se pueden ver (solo se guarda el hash): para cambiar una, se vuelve a crear
el mismo usuario. Otras opciones: `--listar` (ver usuarios), `--desactivar usuario` (dar de
baja sin borrar) y `--activar usuario`.

### Paso 3: ejecutar (web + bot juntos)

```bash
python -m app.server
```

Abrí **http://127.0.0.1:8000** e ingresá con el usuario del paso 2. El bot de Telegram corre en
el mismo proceso: cuando aprobás o rechazás un pedido en la web, el aviso le llega al cliente por
el chat. Sin `TELEGRAM_BOT_TOKEN` arranca solo la web. Poné una `SECRET_KEY` en el `.env` (ver
`.env.example`) para no tener que volver a ingresar cada vez que reiniciás.

(`python -m app.bot` sigue funcionando para correr solo el bot, sin web.)

### Paso 4: WhatsApp (Cloud API de Meta)

El mismo bot atiende también por WhatsApp: mismos pedidos, mismos botones, misma base. Los
clientes de WhatsApp se identifican por su número; los avisos de la web (pedido aprobado, pago
confirmado) les llegan por WhatsApp.

1. En **developers.facebook.com** creá una app con el caso de uso «Conectarte con clientes a
   través de WhatsApp» y, en **Paso 1. Pruébalo**, agregá tu celular como destinatario
   (+54 sin 0 ni 15) y mandá el mensaje de prueba.
2. Completá en el `.env` los cuatro `WHATSAPP_*` (ver `.env.example`): el token, el
   *Phone Number ID*, la clave secreta de la app (Configuración de la app → Básica) y una
   palabra de verificación que inventás vos.
3. Meta tiene que poder llegar a tu web: un túnel HTTPS hacia `127.0.0.1:8000`. Con
   **ngrok** la dirección es fija (gratis, un dominio `*.ngrok-free.dev` por cuenta): instalalo
   (`winget install ngrok.ngrok`), conectalo con `ngrok config add-authtoken TU_TOKEN`, copiá tu
   dominio del panel (Domains) a `NGROK_DOMAIN` en el `.env`, y listo: `python -m app.server`
   abre el túnel solo (un comando levanta web + bot + túnel; Ctrl+C apaga todo). Límites del plan gratis:
   20.000 pedidos y 1 GB por mes; usá la web de admin por `http://127.0.0.1:8000`, no por ngrok.
4. En Meta, **Configuración → Webhook**: URL `https://<tu-túnel>/whatsapp/webhook`, el mismo
   token de verificación, y suscribite al campo **messages**.
5. Si el webhook se verifica pero los mensajes reales no llegan, la app no quedó suscripta a la
   cuenta de WhatsApp. Se suscribe una vez, con el «Identificador de la cuenta de mensajes»
   (WABA) de «Paso 1. Pruébalo» (en PowerShell, desde la carpeta del proyecto):

   ```powershell
   $token = (Select-String -Path .env -Pattern '^WHATSAPP_TOKEN=(.*)$').Matches[0].Groups[1].Value.Trim()
   Invoke-RestMethod -Method Post -Uri "https://graph.facebook.com/v25.0/<WABA_ID>/subscribed_apps" -Headers @{ Authorization = "Bearer $token" }
   ```

**Avisos después de 24 h (plantillas).** WhatsApp solo deja escribirle libremente a un cliente
hasta 24 h después de su último mensaje. Para los avisos de la web (pedido aprobado o rechazado,
pago recibido, pedido cancelado) el bot tiene plantillas de respaldo: si el cliente escribió hace
poco va el mensaje normal; si no, o si Meta lo rechaza, va la plantilla. Se crean una sola vez
(hace falta `WHATSAPP_WABA_ID` en el `.env`):

```bash
python -m scripts.plantillas_whatsapp --ver      # el texto de cada una
python -m scripts.plantillas_whatsapp --crear    # las manda a Meta para aprobar
python -m scripts.plantillas_whatsapp --estado   # aprobadas / pendientes / rechazadas
```

Son de categoría «utilidad»: Meta solo las cobra cuando salen fuera de la ventana de 24 h.

**Política de privacidad.** Meta la pide para publicar la app: está en `/privacidad` (pública,
sin login), con las instrucciones para borrar datos en `/privacidad#borrar`. El contacto que
muestra sale de `NEGOCIO_CONTACTO`.

Cómo funciona: Meta avisa cada mensaje con un POST firmado; el bot comprueba la firma (clave
secreta de la app), contesta enseguida y procesa el mensaje aparte. Si Meta repite un aviso, se
contesta una sola vez. Los botones pasan a botones de respuesta de WhatsApp (hasta 3).
Limitaciones: con el número de prueba solo se puede escribir a 5 números cargados en Meta; el
token temporal vence a las 24 h.

### La web de administración

| Pantalla | Qué hacés ahí |
|---|---|
| **Tablero** | Remito del día (vendido, pedidos, ticket promedio, en revisión), el día en reparto (pedidos por hora con el corte de las 13: qué sale hoy y qué el próximo día hábil), los pedidos que esperan tu decisión, lo que se está por acabar y los avisos. |
| **Pedidos** | Todos los pedidos con filtro por estado y buscador. |
| **Detalle** | Por qué quedó en revisión, stock de hoy, cliente y dirección. **Aprobar** o **rechazar** con motivo: al cliente le llega por el chat. |
| **Productos y stock** | Sumar stock en la fila, editar precio, alias y visibilidad, dar de alta productos. Lo que cambiás acá lo ve el bot al instante en el `/catalogo`. |
| **Clientes** | Dirección guardada, pedidos, total comprado y compra típica de cada cliente. |

La web **se refresca sola**: cada 10 segundos pregunta si cambió algo (entró un pedido,
cambió el stock, llegó un aviso) y recarga; si estás escribiendo en un formulario, en vez de
recargar muestra «Hay novedades». En el celular el menú va en un botón hamburguesa y las tablas
se muestran como tarjetas.

Seguridad: contraseñas con hash, sesión en cookie firmada (`httponly`, `samesite=lax`, 12 h),
token CSRF en cada formulario y todo pide ingresar (salvo `/whatsapp/webhook`, que en vez de
sesión exige la firma de Meta). Corre en tu PC (`127.0.0.1`); para publicarla en internet hace
falta HTTPS.

El simulador de consola también tiene las acciones de admin
(`:avisos`, `:revision`, `:aprobar ID [SKU=precio]`, `:rechazar ID motivo`, `:sumar SKU cantidad`).

El bot funciona **mientras el proceso esté corriendo**. Para 24/7 necesitás una PC
siempre prendida o un servidor. Hacé copia del archivo `data/trazr.db` con regularidad.

## 7. Comandos

**Clientes:** escribir el pedido en texto libre · `/catalogo` · `/confirmar` · `/cancelar` ·
`/modificar` · `/direccion [nueva]` · `/mispedidos` · `/ayuda`

**Administrador:** no hay comandos en el chat. Todo se hace en la web de administración.

## 8. Prueba de punta a punta

Con las bebidas de ejemplo. Todo está automatizado en `tests/` (`python -m pytest -q`).

| # | Quién | Mensaje | Resultado esperado |
|---|---|---|---|
| 1 | Cliente nuevo | `2 coca y 1 sprite` | 📍 *¿A qué dirección te lo enviamos? El envío está incluido, sin costo.* No se registra nada. |
| 1b | Cliente nuevo | `Av. Sarmiento 450, Yerba Buena` | 🧾 *Revisá tu pedido* con precios, envío incluido, la dirección y Total $ 9.400,00, y los botones ✅ 📍 ❌. Todavía no se registra. |
| 1c | Cliente nuevo | ✅ Confirmar (o `sí`) | ✅ *¡Pedido confirmado!* COC1 50→48. |
| 2 | Cliente | `2 coca` → 📍 Cambiar dirección → `Belgrano 300, Tafí Viejo` → `sí` | El resumen se actualiza con la dirección nueva y el pedido sale a esa dirección. Después: *¿Qué dirección dejo guardada para tus próximos pedidos?* [🏠 La de siempre] [📍 La nueva]. |
| 2b | Cliente | `/modificar` → `3 coca, 1 sprite` → `sí` | ✏️ *¡Pedido modificado y confirmado!* Mismo ID, stock reajustado. Queda el aviso en la web. |
| 3 | Cliente | `mandame 10 cervezas` | ⚠️ *No me alcanza el stock: Cerveza Rubia 1L: pediste 10, me quedan 5.* Botones 🔧 Ajustar / ❌ Cancelar, sin Confirmar. No se registra nada. |
| 3b | Cliente | `sí` (o 🔧) y después `sí` | Ajusta a 5, muestra el resumen y confirma. CER1 5→0. |
| 3c | Cliente | `2 vino` | *Vino Tinto Malbec 750ml: pediste 2, no me queda.* No se puede pedir. |
| 4 | Cliente | un pedido de más de $ 150.000 → `sí` | En revisión: «El total ($ …) supera el máximo automático ($ 150.000,00)». En la web: aprobar → al cliente le llega ✅. O rechazar con motivo → le llega ❌ con el motivo. |
| 6 | Cliente | `3 agua` | *¿cuál de estos? Agua Mineral con Gas 2L, Agua Mineral sin Gas 2L*. No se registra. |
| 7 | Cliente | `2 whisky` | *«whisky»: no está en nuestro catálogo*. |
| 8 | Cliente | `2,5 coca` / `-3 sprite` | *la cantidad debe ser un número entero entre 1 y 1000*. |
| 9 | Cliente | `/aprobar P1` | *No conozco ese comando.* El chat no tiene comandos de admin. |

## 9. Estructura del código

```
app/
  server.py       arranca la web y el bot juntos (un proceso)
  bot.py          capa de Telegram (handlers, botones, arranque); sin lógica de negocio
  whatsapp.py     canal WhatsApp: webhook de Meta (firma, sin duplicados), envío por la Graph API
  tunnel.py       túnel de ngrok que abre el servidor si hay NGROK_DOMAIN
  web/            web de administración: main.py (rutas FastAPI), templates/ (Jinja2), static/ (CSS del diseño)
  service.py      BotService: entrada de mensajes, comandos y revisión del admin (independiente del canal)
  checkout.py     borrador, resumen, confirmación, dirección y modificación de pedidos
  admin.py        acciones del admin para la web: aprobar, rechazar, stock, precios, productos, avisos, tablero
  auth.py         contraseñas de los admins (hash scrypt)
  notifier.py     manda los avisos a cada cliente por su canal (Telegram, WhatsApp)
  messages.py     mensaje de salida genérico (texto + botones)
  ai_parser.py    Gemini: intérprete (parse_order) y conversación (conversar)
  parser.py       intérprete por reglas (respaldo)
  rules.py        regla «dentro de los parámetros»
  config.py       variables de entorno y reglas configurables
  models.py       estructuras de datos (Product, Customer, Order, ...)
  sample_data.py  bebidas de ejemplo
  storage/
    base.py       interfaz Repository
    sqlite.py     base de datos SQLite (esquema y consultas)
scripts/
  init_db.py      crea la base y carga el catálogo (ejemplo o CSV)
  crear_admin.py  crea o actualiza un usuario de la web de administración
  plantillas_whatsapp.py  crea en Meta las plantillas de los avisos fuera de las 24 h
  simulate.py     chat por consola sin Telegram
  probar_ia.py    prueba el intérprete contra Gemini real
tests/            pruebas de punta a punta, dirección, base de datos e IA (sin APIs reales)
```

## 10. Suposiciones

1. El catálogo es **solo de bebidas**.
2. El envío está siempre incluido en el precio: no se cobra aparte.
3. Un pedido en revisión **no reserva stock**; se descuenta recién al aprobar (y al aprobar
   se vuelve a verificar).
4. El bot le muestra al cliente la cantidad exacta que queda de un producto cuando no le alcanza.
5. Los precios no vencen; al aprobar se usa el precio actual del producto.
6. Cualquiera que escriba al bot puede pedir. Solo los admins de la web (usuario y contraseña) gestionan.
7. El bot corre como **un solo proceso**; un candado interno evita descontar dos veces el mismo stock.
8. Los clientes se identifican por canal + ID, así el mismo número en Telegram y WhatsApp (fase 5) no se mezcla.
9. Moneda: solo el símbolo es configurable (`CURRENCY_SYMBOL`); los importes se muestran con formato `1.234,50`.
