#!/usr/bin/env bash
# Baja o actualiza el proyecto Trazr desde GitHub (rama development) e instala dependencias.
#   bash actualizar.sh
# - Si no estás dentro del proyecto, lo clona en ./TRAZR; si ya lo tenés, trae lo último.
# - Nunca toca tu .env ni tu base (data/): están fuera de git.
set -euo pipefail
REPO="https://github.com/JosemariaHenriquezC/TRAZR.git"
RAMA="development"
paso() { printf '\n==> %s\n' "$1"; }

command -v git >/dev/null || { echo "No encuentro git: instalalo y volvé a correr este script."; exit 1; }

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  if [ -d TRAZR ]; then cd TRAZR
  else paso "Clonando el proyecto en ./TRAZR"; git clone --branch "$RAMA" "$REPO" TRAZR; cd TRAZR; fi
fi

if [ -n "$(git status --porcelain)" ]; then
  echo "Tenés cambios sin guardar en git:"; git status --short
  echo "Guardalos (git commit) o descartalos antes de actualizar. No toqué nada."; exit 1
fi

paso "Trayendo lo último de $RAMA"
git fetch origin "$RAMA"; git checkout "$RAMA"; git pull origin "$RAMA"

paso "Entorno virtual y dependencias"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip --quiet
.venv/bin/python -m pip install -r requirements.txt --quiet
[ -f .env ] || { cp .env.example .env; echo "Creé .env: completá TELEGRAM_BOT_TOKEN, GEMINI_API_KEY y SECRET_KEY."; }

paso "Listo. Últimos cambios:"; git log --oneline -5
echo; echo "Para arrancar:  source .venv/bin/activate && python -m app.server"
