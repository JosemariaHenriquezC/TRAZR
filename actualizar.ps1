# Baja o actualiza el proyecto Trazr desde GitHub (rama development) e instala dependencias.
#
# Uso (PowerShell, desde la carpeta del proyecto o desde la carpeta donde lo quieras bajar):
#   powershell -ExecutionPolicy Bypass -File actualizar.ps1
#
# - Si no estás dentro del proyecto, lo clona en .\TRAZR.
# - Si ya lo tenés, trae lo último de la rama development.
# - Nunca toca tu .env ni tu base (data/): están fuera de git.

$ErrorActionPreference = "Stop"
$Repo = "https://github.com/JosemariaHenriquezC/TRAZR.git"
$Rama = "development"

function Paso($t) { Write-Host "`n==> $t" -ForegroundColor Green }

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Host "No encuentro git. Instalalo desde https://git-scm.com/download/win y volvé a correr este script." -ForegroundColor Red
    exit 1
}

$dentro = $false
try { git rev-parse --is-inside-work-tree 2>$null | Out-Null; $dentro = ($LASTEXITCODE -eq 0) } catch { $dentro = $false }

if (-not $dentro) {
    if (Test-Path "TRAZR") { Set-Location "TRAZR" }
    else {
        Paso "Clonando el proyecto en .\TRAZR"
        git clone --branch $Rama $Repo TRAZR
        Set-Location "TRAZR"
    }
}

$cambios = git status --porcelain
if ($cambios) {
    Write-Host "Tenés cambios sin guardar en git:" -ForegroundColor Yellow
    Write-Host $cambios
    Write-Host "Guardalos (git commit) o descartalos antes de actualizar. No toqué nada." -ForegroundColor Yellow
    exit 1
}

Paso "Trayendo lo último de $Rama"
git fetch origin $Rama
git checkout $Rama
git pull origin $Rama

Paso "Entorno virtual y dependencias"
if (-not (Test-Path ".venv")) { python -m venv .venv }
& ".\.venv\Scripts\python.exe" -m pip install --upgrade pip --quiet
& ".\.venv\Scripts\python.exe" -m pip install -r requirements.txt --quiet

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Creé .env a partir de .env.example: completá TELEGRAM_BOT_TOKEN, GEMINI_API_KEY y SECRET_KEY." -ForegroundColor Yellow
}

Paso "Listo. Últimos cambios:"
git log --oneline -5
Write-Host "`nPara arrancar:  .\.venv\Scripts\activate ;  python -m app.server" -ForegroundColor Green
