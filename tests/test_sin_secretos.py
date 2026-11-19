"""Ninguna clave real puede quedar en archivos del repositorio (el repo es público).
En octubre se filtró el token de Telegram por .env.example y alguien usó el bot."""
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PATTERNS = {
    "token de Telegram": re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b"),
    "clave de Google/Gemini": re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    "token de Meta/WhatsApp": re.compile(r"\bEAA[A-Za-z0-9]{40,}"),
    "token de ngrok": re.compile(r"\b[0-9][A-Za-z0-9]{26}_[A-Za-z0-9]{20,}\b"),
}


def tracked_files():
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [ROOT / f for f in out.splitlines() if not f.startswith("app/web/landing/assets/")]


def test_no_hay_claves_en_el_repositorio():
    found = []
    for path in tracked_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for kind, pattern in PATTERNS.items():
            if pattern.search(text):
                found.append(f"{path.relative_to(ROOT)}: {kind}")
    assert not found, "Hay claves en archivos del repo (van solo en .env): " + "; ".join(found)


def test_env_example_no_tiene_valores_secretos():
    env = (ROOT / ".env.example").read_text(encoding="utf-8")
    for key in ("TELEGRAM_BOT_TOKEN", "GEMINI_API_KEY", "WHATSAPP_TOKEN", "WHATSAPP_APP_SECRET", "SECRET_KEY"):
        m = re.search(rf"^{key}=(.*)$", env, re.M)
        if m:
            value = m.group(1).split("#")[0].strip()
            assert value == "" or "tu_" in value.lower(), f"{key} tiene un valor en .env.example"
