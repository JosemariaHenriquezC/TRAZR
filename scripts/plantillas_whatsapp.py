"""
Plantillas de WhatsApp para los avisos fuera de la ventana de 24 h.

WhatsApp solo deja escribirle a un cliente libremente hasta 24 h después de su último
mensaje. Pasado ese plazo, los avisos de la web (pedido aprobado o rechazado, pago recibido,
pedido cancelado) tienen que salir con una plantilla aprobada por Meta. Este script las crea
en tu cuenta de WhatsApp y muestra si ya están aprobadas.

    python -m scripts.plantillas_whatsapp --ver      # el texto de cada plantilla (no llama a Meta)
    python -m scripts.plantillas_whatsapp --crear    # las manda a Meta para que las apruebe
    python -m scripts.plantillas_whatsapp --estado   # si están aprobadas, pendientes o rechazadas

Necesita en el .env WHATSAPP_TOKEN y WHATSAPP_WABA_ID (el «Identificador de la cuenta de
mensajes» de la pantalla «Paso 1. Pruébalo»). Son de categoría «utilidad»: Meta solo las cobra
cuando se mandan fuera de la ventana de 24 h.
"""
from __future__ import annotations

import argparse
import sys

import httpx

from app.config import ConfigError, load_settings
from app.whatsapp import GRAPH_URL, PLANTILLAS


def definitions(shop: str, lang: str) -> list[dict]:
    """Lo que se le manda a Meta para crear cada plantilla."""
    out = []
    for name, body, example in PLANTILLAS.values():
        out.append({"name": name, "language": lang, "category": "UTILITY",
                    "components": [{"type": "BODY", "text": body.replace("{shop}", shop),
                                    "example": {"body_text": [example]}}]})
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--ver", action="store_true", help="mostrar el texto de las plantillas")
    group.add_argument("--crear", action="store_true", help="crear las plantillas en Meta")
    group.add_argument("--estado", action="store_true", help="ver si Meta ya las aprobó")
    args = ap.parse_args()
    try:
        s = load_settings(require_telegram=False)
    except ConfigError as exc:
        sys.exit(f"Error de configuración: {exc}")
    wa = s.whatsapp
    defs = definitions(s.business.name or "Trazr", wa.template_lang)

    if args.ver:
        for d in defs:
            print(f"{d['name']}  ({d['language']}, utilidad)\n  {d['components'][0]['text']}\n")
        return

    missing = [k for k, v in {"WHATSAPP_TOKEN": wa.token, "WHATSAPP_WABA_ID": wa.waba_id}.items() if not v]
    if missing:
        sys.exit("Faltan en el .env: " + ", ".join(missing))
    url = f"{GRAPH_URL}/{wa.api_version}/{wa.waba_id}/message_templates"
    headers = {"Authorization": f"Bearer {wa.token}"}

    with httpx.Client(timeout=20) as http:
        if args.crear:
            for d in defs:
                r = http.post(url, json=d, headers=headers)
                data = r.json()
                if r.status_code < 400:
                    print(f"✔ {d['name']}: enviada a Meta (estado: {data.get('status', '?')})")
                else:
                    err = data.get("error", {})
                    msg = err.get("error_user_msg") or err.get("message") or r.text[:200]
                    print(f"✘ {d['name']}: {msg}")
            print("\nMeta suele aprobarlas en minutos u horas. Revisá con --estado.")
            return

        names = {d["name"] for d in defs}
        r = http.get(url, params={"fields": "name,status,language,category", "limit": 100}, headers=headers)
        data = r.json()
        if r.status_code >= 400:
            sys.exit(f"Meta respondió con un error: {data.get('error', {}).get('message', r.text[:200])}")
        found = {t["name"]: t for t in data.get("data", []) if t.get("name") in names}
        estados = {"APPROVED": "aprobada", "PENDING": "pendiente", "REJECTED": "rechazada", "PAUSED": "pausada"}
        for name in sorted(names):
            t = found.get(name)
            if t is None:
                print(f"· {name}: no existe todavía (creala con --crear)")
            else:
                print(f"· {name}: {estados.get(t.get('status'), t.get('status'))} "
                      f"({t.get('language')}, {str(t.get('category', '')).lower()})")


if __name__ == "__main__":
    main()
