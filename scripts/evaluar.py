"""
Evalúa al bot con el set de conversaciones de prueba (tests/golden/casos.json).

    python -m scripts.evaluar              # con Gemini real (necesita GEMINI_API_KEY en el .env)
    python -m scripts.evaluar --sin-ia     # solo con el intérprete por reglas (no llama a nada)
    python -m scripts.evaluar --solo duda-03 -v
    python -m scripts.evaluar --desde cambio-03     # seguir desde un caso (ej. después de un corte)

El plan gratis de Gemini limita las consultas por minuto y por día: por eso va despacio
(--pausa, 6 s por defecto entre casos). Si Gemini no contesta (cuota agotada), ese caso no
cuenta como falla: se marca aparte, y tras 3 seguidos se frena y te dice desde dónde seguir.

Cada caso se corre de cero, en una base temporal con el catálogo de ejemplo: no toca tu
base real. Al final muestra cuántos casos pasaron (por tema) y cuánto tardó Gemini. Sirve
para saber si un cambio en el bot lo mejora o lo empeora: corrélo antes y después.

No corre en los tests automáticos (que no llaman a APIs reales): se corre a mano.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path

from app.config import ConfigError, Rules, load_settings
from app.models import Customer
from app.sample_data import sample_products
from app.service import BotService, User
from app.storage.sqlite import SQLiteRepository

CASOS = Path(__file__).resolve().parent.parent / "tests" / "golden" / "casos.json"
ADDRESS = "Av. Mate de Luna 1234, San Miguel de Tucumán"
STAMP = "2026-01-01 00:00:00"
SIN_IA = "SIN_IA: Gemini no contestó (¿cuota agotada?)"


def load_cases(path: Path = CASOS) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["casos"]


def _plain(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text).lower()


def run_case(case: dict, settings, ai_client=None) -> tuple[bool, list[str], list[str], list[int]]:
    """Corre un caso. Devuelve (pasó, motivos de falla, respuestas del bot, demoras de Gemini en ms).
    Si Gemini no contestó en algún turno, los motivos empiezan con SIN_IA."""
    with tempfile.TemporaryDirectory() as tmp:
        s = replace(settings, database_path=str(Path(tmp) / "eval.db"),
                    rules=replace(settings.rules, review_new_customers=False, max_order_total=0.0),
                    ai_retry_delays=())
        repo = SQLiteRepository(s.database_path)
        try:
            for p in sample_products():
                repo.upsert_product(p, STAMP)
            user = User(9000, "Cliente Prueba", "telegram")
            if not case.get("nuevo"):
                repo.save_customer(Customer(user.channel, str(user.id), user.name, ADDRESS), STAMP)
            svc = BotService(repo, s, ai_client=ai_client)
            replies: list[str] = []
            last = []
            for turn in case["turnos"]:
                if turn.startswith(":"):
                    data = next((d for m in last for _, d in m.buttons if d.startswith(turn[1:] + ":")), None)
                    if data is None:
                        return False, [f"no apareció el botón {turn}"], replies, []
                    last = svc.handle_callback(user, data)
                else:
                    last = svc.handle_message(user, turn)
                replies += [_plain(m.text) for m in last if m.chat_id == user.id]
            events = repo.list_events()
            latencies = [e["latencia_ms"] for e in events if e["tipo"] == "ia" and e["detalle"] != "sin_respuesta"]
            if any(e["tipo"] == "ia" and e["detalle"] == "sin_respuesta" for e in events):
                return False, [SIN_IA], replies, latencies
            return (*_check(case.get("espera", {}), svc, repo, user, replies, last), replies, latencies)
        finally:
            repo.close()


def _check(expect: dict, svc, repo, user, replies, last) -> tuple[bool, list[str]]:
    fails = []
    draft = svc._get_draft(user)
    items = {sku: qty for sku, qty in (draft.items or [])} if draft else {}
    orders = repo.list_orders()
    if "borrador" in expect and items != expect["borrador"]:
        fails.append(f"borrador {items or '—'}, se esperaba {expect['borrador']}")
    if expect.get("pregunta"):
        if draft is not None and draft.waiting == "confirmacion" and \
                not any("?" in r for r in replies[-1:]):
            fails.append("armó el pedido sin preguntar")
    for sku, qty in (expect.get("conserva") or {}).items():
        if items.get(sku) != qty:
            fails.append(f"se perdió {qty} × {sku} (quedó {items or '—'})")
    if expect.get("sin_pedido") and (draft is not None or orders):
        fails.append(f"quedó un {'pedido registrado' if orders else 'borrador'} y no debía")
    if "pedidos" in expect and len(orders) != expect["pedidos"]:
        fails.append(f"quedaron {len(orders)} pedidos registrados, se esperaban {expect['pedidos']}")
    if "ultimo_pago" in expect and (not orders or orders[-1].payment_method != expect["ultimo_pago"]):
        fails.append(f"el último pedido se pagó con «{orders[-1].payment_method if orders else '—'}», "
                     f"se esperaba «{expect['ultimo_pago']}»")
    if "registrado" in expect and bool(orders) != expect["registrado"]:
        fails.append("se registró un pedido y no debía" if orders else "no se registró el pedido")
    if "esperando" in expect:
        waiting = draft.waiting if draft else "nada"
        if waiting != expect["esperando"]:
            fails.append(f"espera «{waiting}», se esperaba «{expect['esperando']}»")
    for text in expect.get("no_dice", []):
        if any(text in r for r in replies):
            fails.append(f"dijo «{text}»")
    for text in expect.get("dice", []):
        if not any(text in _plain(m.text) for m in last):
            fails.append(f"no dijo «{text}»")
    return not fails, fails


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sin-ia", action="store_true", help="usar solo el intérprete por reglas")
    ap.add_argument("--solo", help="correr solo el caso con este id (o los que empiezan así)")
    ap.add_argument("--desde", help="empezar desde el caso con este id")
    ap.add_argument("--pausa", type=float, default=6.0, help="segundos de espera entre casos (cuota de Gemini)")
    ap.add_argument("-v", "--verbose", action="store_true", help="mostrar las respuestas del bot")
    args = ap.parse_args()
    try:
        settings = load_settings(require_telegram=False)
    except ConfigError as exc:
        sys.exit(f"Error de configuración: {exc}")
    if args.sin_ia:
        settings = replace(settings, ai=replace(settings.ai, enabled=False))
    elif not settings.ai.usable:
        sys.exit("Gemini no está disponible (falta GEMINI_API_KEY o AI_PARSER_ENABLED=false). "
                 "Para probar solo las reglas: python -m scripts.evaluar --sin-ia")

    cases = [c for c in load_cases() if not args.solo or c["id"].startswith(args.solo)]
    if args.desde:
        ids = [c["id"] for c in cases]
        if args.desde not in ids:
            sys.exit(f"No existe el caso «{args.desde}».")
        cases = cases[ids.index(args.desde):]
    pause = 0.0 if args.sin_ia else args.pausa
    if not cases:
        sys.exit(f"No hay casos que empiecen con «{args.solo}».")
    print(f"Evaluando {len(cases)} casos {'solo con reglas' if args.sin_ia else 'con Gemini ' + settings.ai.model}…\n")

    by_topic: dict[str, list[bool]] = {}
    latencies: list[int] = []
    no_ai: list[str] = []
    streak = 0
    for n, case in enumerate(cases):
        if n and pause:
            time.sleep(pause)
        ok, fails, replies, lat = run_case(case, settings)
        latencies += lat
        if fails == [SIN_IA]:
            no_ai.append(case["id"])
            streak += 1
            print(f"· {case['id']:<14} Gemini no contestó: no se cuenta")
            if streak >= 3:
                print(f"\nGemini dejó de contestar 3 veces seguidas: seguramente se agotó la cuota. "
                      f"Esperá un rato (o hasta mañana si es la cuota diaria) y seguí con:\n"
                      f"  python -m scripts.evaluar --desde {no_ai[-3]}")
                break
            continue
        streak = 0
        by_topic.setdefault(case["tema"], []).append(ok)
        print(f"{'✔' if ok else '✘'} {case['id']:<14} {' / '.join(case['turnos'])[:60]}")
        for f in fails:
            print(f"     · {f}")
        if args.verbose:
            for r in replies:
                print("       bot: " + r.replace("\n", " | ")[:200])

    total = sum(sum(v) for v in by_topic.values())
    counted = sum(len(v) for v in by_topic.values())
    if not counted:
        sys.exit("\nNingún caso llegó a evaluarse.")
    print(f"\nPasaron {total} de {counted} evaluados ({total / counted:.0%})"
          + (f"; {len(no_ai)} sin respuesta de Gemini, no contados" if no_ai else ""))
    for topic, results in by_topic.items():
        print(f"  {topic:<28} {sum(results)}/{len(results)}")
    if latencies:
        latencies.sort()
        p95 = latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))]
        print(f"Gemini: {len(latencies)} consultas, mediana {statistics.median(latencies):.0f} ms, "
              f"95 % por debajo de {p95} ms")


if __name__ == "__main__":
    main()
