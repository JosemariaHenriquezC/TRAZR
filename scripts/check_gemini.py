import os, sys, time
from pathlib import Path

print("Iniciando prueba de Gemini...", flush=True)

for linea in Path(".env").read_text(encoding="utf-8-sig").splitlines():
    for nombre in ("GEMINI_API_KEY", "GEMINI_MODEL"):
        if linea.startswith(nombre + "="):
            os.environ[nombre] = linea.split("=", 1)[1].strip().strip('"')

from google import genai

clave = os.environ.get("GEMINI_API_KEY")
if not clave:
    sys.exit("Falta GEMINI_API_KEY en el .env")
modelo = os.environ.get("GEMINI_MODEL", "gemini-1.5-flash-8b")
print("Modelo:", modelo, flush=True)

client = genai.Client(api_key=clave)
try:
    r = client.models.generate_content(
        model=modelo, contents="Respondé solo: OK"
    )
    print(f"Funcionó con {modelo}: {r.text}")
except Exception as e:
    print("Falló:", str(e)[:300])
    if "404" in str(e) or "NOT_FOUND" in str(e):
        print("El modelo no existe en tu cuenta (404).")