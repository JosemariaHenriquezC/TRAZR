#!/usr/bin/env bash
# Instala Trazr en un servidor Ubuntu 24.04 (x64 o Arm64) y lo deja corriendo solo.
#
#   curl -fsSL https://raw.githubusercontent.com/JosemariaHenriquezC/TRAZR/development/scripts/instalar_servidor.sh | sudo bash -s -- trazr.bot.nu
#
# Qué hace:
#   1. Instala lo necesario (git, Caddy para HTTPS y uv para Python 3.13).
#   2. Baja el proyecto en /opt/trazr (rama development) y sus dependencias.
#   3. Crea el servicio «trazr» (arranca solo con la máquina y se reinicia si se cae).
#   4. Configura Caddy: https://<dominio> → la web y el webhook (certificado HTTPS gratis y automático).
# Lo que NO hace: copiar tu .env ni tu base (data/). Eso lo subís vos (son secretos).
# Se puede correr de nuevo para actualizar: baja los últimos cambios y reinicia.
set -euo pipefail

DOMINIO="${1:-}"
if [[ -z "$DOMINIO" ]]; then
  echo "Uso: sudo bash instalar_servidor.sh <dominio>   (ej. trazr.bot.nu)" >&2
  exit 1
fi
if [[ $EUID -ne 0 ]]; then
  echo "Corrélo con sudo." >&2
  exit 1
fi

REPO="https://github.com/JosemariaHenriquezC/TRAZR.git"
RAMA="development"
DIR="/opt/trazr"
USUARIO="trazr"

echo "==> 1/5 Paquetes del sistema"
apt-get update -y
apt-get install -y git curl ca-certificates debian-keyring debian-archive-keyring apt-transport-https gnupg

if ! command -v caddy >/dev/null; then
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -y
  apt-get install -y caddy
fi

echo "==> 2/5 Usuario y proyecto"
id -u "$USUARIO" >/dev/null 2>&1 || useradd --system --create-home --shell /usr/sbin/nologin "$USUARIO"
if [[ -d "$DIR/.git" ]]; then
  sudo -u "$USUARIO" -H git -C "$DIR" fetch origin "$RAMA"
  sudo -u "$USUARIO" -H git -C "$DIR" checkout "$RAMA"
  sudo -u "$USUARIO" -H git -C "$DIR" reset --hard "origin/$RAMA"
else
  mkdir -p "$DIR"
  chown "$USUARIO:$USUARIO" "$DIR"
  sudo -u "$USUARIO" -H git clone --branch "$RAMA" "$REPO" "$DIR"
fi
mkdir -p "$DIR/data"
chown -R "$USUARIO:$USUARIO" "$DIR"

echo "==> 3/5 Python 3.13 y dependencias"
UV_HOME="/home/$USUARIO/.local/bin"
if [[ ! -x "$UV_HOME/uv" ]]; then
  sudo -u "$USUARIO" -H bash -c 'curl -LsSf https://astral.sh/uv/install.sh | sh'
fi
if [[ ! -x "$DIR/.venv/bin/python" ]]; then
  sudo -u "$USUARIO" -H bash -c "cd '$DIR' && '$UV_HOME/uv' venv --python 3.13 .venv"
fi
sudo -u "$USUARIO" -H bash -c "cd '$DIR' && '$UV_HOME/uv' pip install --python .venv/bin/python -r requirements.txt"

echo "==> 4/5 Servicio «trazr»"
cat > /etc/systemd/system/trazr.service <<EOF
[Unit]
Description=Trazr (bot de pedidos + web)
After=network-online.target
Wants=network-online.target

[Service]
User=$USUARIO
WorkingDirectory=$DIR
ExecStart=$DIR/.venv/bin/python -m app.server
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable trazr >/dev/null

echo "==> 5/5 Caddy (HTTPS) para $DOMINIO"
cat > /etc/caddy/Caddyfile <<EOF
$DOMINIO {
    encode gzip
    reverse_proxy 127.0.0.1:8000
}
EOF
systemctl reload caddy || systemctl restart caddy

if [[ -f "$DIR/.env" ]]; then
  systemctl restart trazr
  echo
  echo "Listo: Trazr corriendo. Mirá el registro con:  sudo journalctl -u trazr -f"
else
  echo
  echo "Falta tu .env: subilo a $DIR/.env (sin NGROK_DOMAIN) y después:"
  echo "  sudo chown $USUARIO:$USUARIO $DIR/.env && sudo systemctl restart trazr"
fi
echo "Sitio: https://$DOMINIO   ·   Panel: https://$DOMINIO/admin"
