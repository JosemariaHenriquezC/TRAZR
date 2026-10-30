# Bot de pedidos y stock para Telegram (Google Sheets)

Bot de Telegram que recibe pedidos en lenguaje natural, consulta stock y precio
vigente en tiempo real, confirma solo lo que está dentro de los parámetros,
deriva el resto al administrador y registra todo en una hoja de pedidos.

> **Qué está listo y qué falta de tu lado.** El código está completo y probado
> (17 tests de punta a punta pasan, incluido el adaptador de Google Sheets contra
> una hoja simulada). Lo único que no puedo hacer por vos es crear **tu** bot en
> Telegram ni **tu** cuenta de servicio de Google: son credenciales tuyas. Seguí
> los pasos 1 a 6 y queda funcionando. Si querés probarlo antes de tocar Google,
> el modo `excel` corre con dos archivos locales (paso 0).

---

## 1. Decisiones técnicas

| Elección | Por qué |
|---|---|
| **Python 3.10+** | Ecosistema maduro para bots y hojas de cálculo; fácil de leer y modificar. |
| **python-telegram-bot 21** | Librería oficialmente recomendada por Telegram, asíncrona, con botones inline. Usa *long polling*: no necesitás dominio, HTTPS ni abrir puertos. |
| **Google Sheets + gspread** | Hoja en la nube con API gratuita, editable a mano desde el navegador o el celular, compartible con tu equipo y con historial de versiones. gspread es la librería de referencia. |
| **Cuenta de servicio de Google** | El bot se autentica con un archivo JSON, sin pedirte login cada vez. Solo ve las hojas que le compartas. |
| **Excel local (opcional)** | Mismo esquema de columnas, para probar sin cuenta de Google. No es para producción. |
| **Intérprete por reglas** (no LLM) | Funciona sin claves de terceros ni costo por mensaje. Tolera tildes, plurales, errores de tipeo, «x 3», «media docena», números en palabra. Está aislado en `app/parser.py` para cambiarlo por un modelo de lenguaje si lo necesitás. |

## 2. Regla «dentro de los parámetros»

Un pedido se **confirma automáticamente** solo si **todos** sus renglones cumplen:

1. **Producto existente**: está en la hoja de stock y `Activo` = SI.
2. **Precio vigente**: `Precio` > 0 y `Vigente_hasta` vacío o igual/posterior a hoy.
3. **Stock suficiente**: `Stock` ≥ cantidad pedida.
4. *(Opcional)* el total no supera `MAX_ORDER_TOTAL`.

| Situación | Qué pasa |
|---|---|
| Cumple todo | **CONFIRMADO**, se descuenta el stock, el cliente recibe el detalle. |
| Falta stock, precio vencido o supera el total máximo | **EN REVISION**, no se toca el stock, el admin recibe el pedido con botones Aprobar / Rechazar. |
| Producto inexistente | Por defecto se le pide al cliente que corrija (no se registra). Con `ON_UNKNOWN_PRODUCT=review` se registra en revisión. |
| Mensaje ambiguo, sin cantidad, cantidad inválida (decimal, negativa, 0 o > `MAX_QTY_PER_ITEM`) | Se pide aclaración. No se registra nada. |
| Falla de acceso a la hoja | Mensaje claro al cliente, alerta al admin con el error. No queda nada a medias (si falla el registro después de descontar, el stock se devuelve). |

Todo se ajusta en el `.env` (`RULE_REQUIRE_PRICE_VALID`, `RULE_REQUIRE_STOCK`,
`MAX_ORDER_TOTAL`, `MAX_QTY_PER_ITEM`, `ON_UNKNOWN_PRODUCT`, `MATCH_THRESHOLD`).

**Al aprobar** un pedido en revisión, el bot vuelve a verificar stock y precio
**en ese momento**. Si todavía falta stock o el precio sigue vencido, te dice qué
comando usar (`/sumar`, `/precio`) y no aprueba. Al aprobar toma el precio vigente
actual y descuenta el stock.

## 3. Estructura de las hojas

Son **dos archivos separados** de Google Sheets. La fila 1 es el encabezado, con
estos nombres exactos (el orden de columnas puede variar). El script del paso 5 los
escribe por vos.

### Hoja de stock (pestaña `Stock`)

| Columna | Tipo | Ejemplo | Notas |
|---|---|---|---|
| `SKU` | texto | `YER1` | Código único. Lo usa el admin en los comandos. |
| `Producto` | texto | `Yerba Mate 1kg` | Nombre que ve el cliente. |
| `Alias` | texto | `yerba, mate` | Otras formas de nombrarlo, separadas por coma. |
| `Stock` | número | `50` | Unidades disponibles. El bot lo descuenta. |
| `Precio` | número | `4500` | Precio unitario. |
| `Vigente_hasta` | fecha | `2026-12-31` | Vacío = sin vencimiento. Acepta `AAAA-MM-DD` o `DD/MM/AAAA`. |
| `Activo` | SI/NO | `SI` | NO = oculto y no se puede pedir. |
| `Actualizado` | fecha-hora | | La completa el bot. |

### Hoja de pedidos (pestaña `Pedidos`)

| Columna | Contenido |
|---|---|
| `Pedido_ID` | Ej. `P261029-A3F1` |
| `Fecha` | Fecha y hora del pedido (zona `TIMEZONE`) |
| `Cliente_ID` | ID de Telegram del cliente (para avisarle) |
| `Cliente_nombre` | Nombre en Telegram |
| `Mensaje_original` | Lo que escribió el cliente |
| `Detalle` | `2 x Yerba Mate 1kg (YER1) @ 4500.00 = 9000.00; ...` |
| `Items_JSON` | Mismo detalle en formato máquina (no editar a mano) |
| `Total` | Importe total |
| `Estado` | `CONFIRMADO`, `EN REVISION` o `RECHAZADO` |
| `Motivo` | Por qué fue a revisión / motivo de rechazo |
| `Actualizado` | Último cambio de estado |
| `Revisado_por` | Admin que aprobó o rechazó |

## 4. Puesta en marcha paso a paso

### Paso 0 (opcional): probar sin Telegram ni Google

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # Windows: copy .env.example .env
# en .env poné STORAGE_BACKEND=excel
python -m scripts.init_sheets --ejemplo
python -m scripts.simulate         # chateás con el bot en la consola
python -m pytest -q                # corre la prueba de punta a punta
```

### Paso 1: crear el bot en Telegram

1. En Telegram abrí **@BotFather** → `/newbot`.
2. Elegí un nombre visible y un usuario terminado en `bot` (ej. `MiNegocioPedidosBot`).
3. BotFather te da un **token**. Pegalo en `.env` como `TELEGRAM_BOT_TOKEN=...`.
   Es secreto: si se filtra, en BotFather usá `/revoke`.

### Paso 2: proyecto de Google Cloud

1. Entrá a <https://console.cloud.google.com/> y creá un proyecto (ej. «bot-pedidos»).
2. **APIs y servicios → Biblioteca** → habilitá **Google Sheets API** (y **Google Drive API**).

### Paso 3: cuenta de servicio (credenciales)

1. **APIs y servicios → Credenciales → Crear credenciales → Cuenta de servicio**. Nombre libre, sin roles.
2. Abrí la cuenta creada → pestaña **Claves → Agregar clave → JSON**. Se descarga un archivo.
3. Guardalo en la carpeta del proyecto como `credentials.json` (o indicá la ruta en `GOOGLE_CREDENTIALS_FILE`).
4. Copiá el **email** de la cuenta (termina en `iam.gserviceaccount.com`).

### Paso 4: crear y compartir las dos hojas

1. Creá dos hojas en <https://sheets.google.com>: por ejemplo «Stock» y «Pedidos».
2. En cada una: **Compartir** → pegá el email de la cuenta de servicio → **Editor**.
3. Copiá el ID de cada una desde la URL (`https://docs.google.com/spreadsheets/d/`**`ID`**`/edit`) y pegalos en `.env`:
   ```
   STOCK_SPREADSHEET_ID=...
   ORDERS_SPREADSHEET_ID=...
   ```

### Paso 5: completar `.env` e inicializar

```bash
cp .env.example .env     # si no lo hiciste
# completá TELEGRAM_BOT_TOKEN, los dos IDs, STORAGE_BACKEND=gsheets y TIMEZONE
python -m scripts.init_sheets --ejemplo   # crea pestañas, encabezados y 10 productos de ejemplo
```

Sin `--ejemplo` solo escribe los encabezados y cargás tus productos a mano.
El script nunca borra datos existentes. Los productos de ejemplo incluyen uno con
precio vencido (`CAF1`) y uno sin stock (`LEC1`) para probar las revisiones;
borralos cuando cargues los tuyos.

### Paso 6: ejecutar y hacerte administrador

```bash
python -m app.bot
```

1. En Telegram buscá tu bot y mandale `/miid`. Te responde tu ID.
2. Ponelo en `.env`: `ADMIN_CHAT_IDS=123456789` (varios admins: separados por coma).
3. Cortá el bot (Ctrl+C) y volvé a ejecutarlo. Ya sos admin: `/ayuda` muestra los comandos de gestión.

El bot funciona **mientras el proceso esté corriendo**. Para 24/7 necesitás dejarlo
en una PC siempre prendida o en un servidor (VPS, Railway, Render, etc.). Solo
necesita salida a internet; no hace falta abrir puertos.

## 5. Comandos

**Clientes:** escribir el pedido en texto libre · `/catalogo` · `/mispedidos` · `/miid` · `/ayuda`

**Administrador:**

| Comando | Qué hace |
|---|---|
| `/stock [texto o SKU]` | Consulta stock, precio y vigencia (todo o filtrado) |
| `/setstock SKU cantidad` | Fija el stock |
| `/sumar SKU cantidad` | Suma (o resta con negativo) al stock; no permite negativos |
| `/precio SKU valor [AAAA-MM-DD]` | Precio y vigencia (sin fecha = sin vencimiento) |
| `/revision` | Lista pedidos en revisión con botones |
| `/pedido ID` | Ver un pedido |
| `/aprobar ID` o botón ✅ | Aprueba: verifica, descuenta stock y avisa al cliente |
| `/rechazar ID [motivo]` o botón ❌ | Rechaza y avisa al cliente con el motivo |

Como admin también podés mandar pedidos en texto libre, así probás los dos roles
con una sola cuenta.

## 6. Prueba de punta a punta

Con los productos de ejemplo y `TIMEZONE` configurada. Todos estos casos están
automatizados en `tests/test_e2e.py` (`python -m pytest -q`).

| # | Quién | Mensaje | Resultado esperado |
|---|---|---|---|
| 1 | Cliente | `Hola, quiero 2 yerba y 3 azúcar` | ✅ *¡Pedido confirmado!* con ID, fecha, `2 × Yerba Mate 1kg — $ 4.500,00 c/u = $ 9.000,00`, `3 × Azúcar 1kg … = $ 3.600,00`, **Total $ 12.600,00**. Hoja de pedidos: fila `CONFIRMADO`. Stock: YER1 50→48, AZU1 30→27. |
| 2 | Cliente | `mandame 10 aceites` | 🟡 *Recibimos tu pedido… en revisión*. Al admin le llega el pedido con motivo `Stock insuficiente: Aceite de Girasol 1.5L (pedido 10, disponible 5)` y botones. Hoja: `EN REVISION`. Stock ACE1 sigue en 5. |
| 2b | Admin | ✅ Aprobar (o `/aprobar ID`) | *No puedo aprobar todavía… Sumá stock con /sumar ACE1 cantidad*. |
| 2c | Admin | `/sumar ACE1 10` | *Stock de Aceite… ahora: 15 (+10)*. |
| 2d | Admin | ✅ Aprobar | Admin: *Pedido … aprobado*. Cliente: *¡Tu pedido fue confirmado!* Total $ 38.000,00. Hoja: `CONFIRMADO`, `Revisado_por` = admin. ACE1 15→5. |
| 3 | Cliente | `1 cafe` | En revisión por `Precio no vigente: Café Molido 250g`. Admin: `/precio CAF1 5500` y luego `/aprobar ID` → confirmado a $ 5.500,00. |
| 4 | Cliente | `2 leche` | En revisión (stock 0). Admin: `/rechazar ID Sin stock hasta el lunes` → al cliente le llega ❌ con ese motivo. Hoja: `RECHAZADO`. |
| 5 | Admin | `/stock yerba` | `YER1 Yerba Mate 1kg: 48 u. · $ 4.500,00 (vence …)` |
| 6 | Admin | `/setstock YER1 120` | *Stock de Yerba Mate 1kg (YER1) actualizado a 120*. La hoja se actualiza al instante. |
| 7 | Cliente | `/setstock YER1 0` | ⛔ *Ese comando es solo para el administrador.* |
| 8 | Cliente | `3 galletitas` | *¿cuál de estos? Galletitas Dulces 300g, Galletitas Saladas 300g*. No se registra. |
| 9 | Cliente | `2 caviar` | *«caviar»: no está en nuestro catálogo (mirá /catalogo)*. |
| 10 | Cliente | `2,5 yerba` / `-3 arroz` | *la cantidad debe ser un número entero entre 1 y 1000*. |
| 11 | Cliente | `quiero yerba` | *¿cuántas unidades de Yerba Mate 1kg?* |
| 12 | Cliente | `hola` | *No encontré un pedido en tu mensaje…* |
| 13 | — | Hoja sin compartir / ID incorrecto | Cliente: *No pude acceder a la planilla…*; admin: 🚨 con el error (ej. «compartila con la cuenta de servicio como Editor»). |

## 7. Estructura del código

```
app/
  bot.py          capa de Telegram (handlers, botones, arranque)
  service.py      lógica: pedidos, revisión, comandos de stock
  parser.py       interpretación del texto del cliente
  rules.py        regla «dentro de los parámetros»
  config.py       variables de entorno y reglas configurables
  models.py       columnas de las hojas y estructuras de datos
  storage/
    gsheets.py    Google Sheets (reintentos, errores traducidos)
    excel.py      archivos .xlsx locales (pruebas)
scripts/
  init_sheets.py  crea encabezados y datos de ejemplo
  simulate.py     chat por consola sin Telegram
tests/            prueba de punta a punta (17 casos)
```

## 8. Suposiciones

1. Interpreté «si **no** hay faltante de stock, se deriva a revisión» como un error de tipeo: lo que se deriva es el pedido **con** faltante.
2. El canal es **Telegram** para clientes y administrador. La etapa «el cliente envía su pedido por WhatsApp e interpreta Trazr» no está integrada: no tengo acceso a Trazr ni a una cuenta de WhatsApp Business. La lógica está separada de Telegram (`service.py`) para poder conectar otro canal después.
3. La interpretación es por reglas, no con un modelo de lenguaje, para no requerir claves ni costos extra. Pide aclaración en vez de adivinar.
4. Las cantidades son unidades enteras (no kilos fraccionados).
5. Un pedido en revisión **no reserva stock**; se descuenta recién al aprobar, con una nueva verificación.
6. Al aprobar se usa el precio vigente en ese momento (por si lo corregiste con `/precio`).
7. Cualquiera que escriba al bot puede pedir; no hay lista de clientes autorizados. Solo los IDs de `ADMIN_CHAT_IDS` gestionan.
8. El bot corre como **un solo proceso**. Un candado interno evita descontar dos veces el mismo stock. Si editás el stock a mano en la hoja justo mientras entra un pedido, gana la última escritura.
9. Moneda: solo el símbolo es configurable (`CURRENCY_SYMBOL`); los importes se muestran con formato `1.234,50`.
10. Zona horaria: `UTC` por defecto. No la asumí; configurá `TIMEZONE` en el `.env`.
11. Los productos de ejemplo son inventados, solo para probar.
