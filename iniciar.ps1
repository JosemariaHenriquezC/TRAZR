# Arranca Trazr: la web + el bot (ventana 1) y el túnel de ngrok para WhatsApp (ventana 2).
#
# Uso: doble clic en iniciar.bat, o en PowerShell desde la carpeta del proyecto:
#   powershell -ExecutionPolicy Bypass -File iniciar.ps1
#
# Lee del .env:
#   NGROK_DOMAIN  tu dominio fijo de ngrok (ej. palabras-raras-1234.ngrok-free.dev)
#   WEB_PORT      el puerto de la web (si no está, 8000)
# Sin NGROK_DOMAIN arranca solo la web y el bot (Telegram funciona; WhatsApp no recibe mensajes).
# Para apagar todo: cerrá las dos ventanas (o Ctrl+C en cada una).

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

function Paso($t)  { Write-Host "`n==> $t" -ForegroundColor Green }
function Aviso($t) { Write-Host $t -ForegroundColor Yellow }
function Fallar($t) { Write-Host $t -ForegroundColor Red; Read-Host "`nApretá Enter para cerrar"; exit 1 }

# ---- valores del .env (sin comillas ni comentarios)
function Leer-Env($nombre) {
    if (-not (Test-Path ".env")) { return "" }
    foreach ($linea in Get-Content ".env" -Encoding UTF8) {
        if ($linea -match "^\s*$nombre\s*=(.*)$") {
            $valor = $Matches[1] -replace "\s+#.*$", ""
            return $valor.Trim().Trim('"').Trim("'")
        }
    }
    return ""
}

# ---- comprobaciones
$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Fallar "No encuentro el entorno de Python (.venv). Corré primero: powershell -ExecutionPolicy Bypass -File actualizar.ps1"
}
if (-not (Test-Path ".env")) {
    Fallar "No encuentro el archivo .env. Copiá .env.example como .env y completalo."
}

$puerto = Leer-Env "WEB_PORT"
if (-not $puerto) { $puerto = "8000" }
$dominio = Leer-Env "NGROK_DOMAIN"
$dominio = $dominio -replace "^https?://", "" -replace "/.*$", ""

$ngrok = $null
if ($dominio) {
    $cmd = Get-Command ngrok -ErrorAction SilentlyContinue
    if ($cmd) { $ngrok = $cmd.Source }
    elseif (Test-Path "$HOME\ngrok\ngrok.exe") { $ngrok = "$HOME\ngrok\ngrok.exe" }
    else { Aviso "No encuentro ngrok (ni instalado ni en $HOME\ngrok\ngrok.exe). Arranco solo la web y el bot." }
} else {
    Aviso "No hay NGROK_DOMAIN en el .env: arranco solo la web y el bot (WhatsApp no va a recibir mensajes)."
}

# ---- ¿ya está corriendo?
function Web-Responde {
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:$puerto/salud" -UseBasicParsing -TimeoutSec 2
        return ($r.StatusCode -eq 200)
    } catch { return $false }
}
if (Web-Responde) {
    Aviso "La web ya está corriendo en el puerto ${puerto}: no abro otra (dos bots a la vez chocan en Telegram)."
} else {
    Paso "Ventana 1: web y bot"
    $orden = "`$host.UI.RawUI.WindowTitle = 'Trazr - web y bot'; & '$python' -m app.server"
    Start-Process powershell -WorkingDirectory $PSScriptRoot -ArgumentList "-NoExit", "-Command", $orden

    Write-Host "Esperando que arranque" -NoNewline
    $listo = $false
    for ($i = 0; $i -lt 40; $i++) {
        Start-Sleep -Milliseconds 750
        Write-Host "." -NoNewline
        if (Web-Responde) { $listo = $true; break }
    }
    Write-Host ""
    if (-not $listo) {
        Fallar "La web no respondió en 30 segundos. Mirá la ventana 'Trazr - web y bot': ahí está el error."
    }
}

if ($ngrok) {
    Paso "Ventana 2: túnel de ngrok ($dominio)"
    $orden = "`$host.UI.RawUI.WindowTitle = 'Trazr - ngrok'; & '$ngrok' http --url=$dominio $puerto"
    Start-Process powershell -WorkingDirectory $PSScriptRoot -ArgumentList "-NoExit", "-Command", $orden
}

Paso "Listo"
Write-Host "Web de administración:  http://127.0.0.1:$puerto   (usala siempre por acá, no por ngrok)"
if ($ngrok) {
    Write-Host "Webhook para Meta:      https://$dominio/whatsapp/webhook"
}
Write-Host "Para apagar: cerrá las ventanas 'Trazr - web y bot' y 'Trazr - ngrok'."
Start-Process "http://127.0.0.1:$puerto"
Start-Sleep -Seconds 4
