"""
Intérprete de mensajes de pedido en lenguaje natural (versión por reglas).

Entiende mensajes como:
    "Hola, quiero 2 coca y 3 agua sin gas"
    "mandame 10 cerveza, 1 docena de jugos"
    "sprite x 4"
    "dos fanta\n1 fernet"

Cómo funciona:
  1. Normaliza (minúsculas, sin tildes) y separa en renglones por coma, ";",
     salto de línea o " y " seguido de una cantidad.
  2. En cada renglón busca la cantidad (número o palabra: "dos", "docena"...).
  3. Busca el producto comparando contra Producto, Alias y SKU del catálogo,
     tolerando plurales y errores de tipeo (difflib).
  4. Si dos productos se parecen casi igual → "ambiguo" (se le pregunta al cliente).

Está aislado en este módulo a propósito: si más adelante se quiere usar un
modelo de lenguaje (LLM) para interpretar, alcanza con reemplazar
`parse_order()` por otra función que devuelva la misma lista de ParsedItem.
"""
from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

from .models import ParsedItem, Product

NUMBER_WORDS = {
    "un": 1, "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5,
    "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10, "once": 11,
    "doce": 12, "docena": 12, "veinte": 20, "treinta": 30, "cincuenta": 50, "cien": 100,
}

# Palabras de relleno que no aportan al nombre del producto.
FILLER = {
    "hola", "buenas", "buen", "buenos", "dia", "dias", "tarde", "tardes", "noche", "noches",
    "quiero", "quisiera", "queria", "necesito", "necesitaria", "mandame", "mandar", "manda",
    "enviame", "envia", "traeme", "pasame", "pedido", "pedir", "pido", "hacer", "haceme",
    "por", "favor", "porfa", "gracias", "me", "te", "de", "del", "la", "las", "el", "los",
    "unidades", "unidad", "u", "uds", "ud", "unid", "cantidad", "porfavor", "tambien",
    "ademas", "mas", "con", "sumale", "agrega", "agregame", "anotame", "mandas", "mandan",
    "podes", "podrias", "pasas", "envias", "traes", "dame", "das", "quiero", "llevo", "llevar",
}

_QTY_RE = re.compile(r"^-?\d+(?:[.,]\d+)?$")


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    # deja letras, números, espacios, separadores y signos útiles
    text = re.sub(r"[^a-z0-9\s,;.\-+/\n]", " ", text)
    return text


def _stem(token: str) -> str:
    """Plural → singular muy simple (yerbas→yerba, panes→pan)."""
    if len(token) > 4 and token.endswith("es"):
        return token[:-2]
    if len(token) > 3 and token.endswith("s"):
        return token[:-1]
    return token


def _key(text: str) -> str:
    tokens = [t for t in re.split(r"[\s\-/]+", normalize(text).replace(",", " ").strip()) if t]
    return " ".join(_stem(t) for t in tokens)


def split_lines(text: str) -> list[str]:
    text = normalize(text)
    numwords = "|".join(NUMBER_WORDS)
    # coma que NO está entre dígitos (para no romper "1,5")
    text = re.sub(r"(?<!\d),|,(?!\d)", "\n", text)
    text = re.sub(r"[;+]", "\n", text)
    # " y " seguido de cantidad → nuevo renglón
    text = re.sub(rf"\s+y\s+(?=(\d|(?:{numwords}|media|medio|par)\b))", "\n", text)
    return [l.strip(" .") for l in text.split("\n") if l.strip(" .")]


def _parse_qty_token(tok: str):
    """Devuelve (cantidad, es_valida) o None si el token no es una cantidad."""
    tok = tok.strip()
    if tok.startswith("x") and _QTY_RE.match(tok[1:] or "-"):
        tok = tok[1:]
    if tok in NUMBER_WORDS:
        return NUMBER_WORDS[tok], True
    if _QTY_RE.match(tok):
        value = float(tok.replace(",", "."))
        if value <= 0 or value != int(value):
            return value, False
        return int(value), True
    return None


def _extract_qty(words: list[str]):
    """Busca la cantidad al principio o al final del renglón.
    Devuelve (cantidad|None, valida, palabras_restantes)."""
    if not words:
        return None, True, words
    # "un par" / "par" = 2
    if len(words) >= 2 and words[0] in {"un", "1"} and words[1] == "par":
        return 2, True, words[2:]
    if words and words[0] == "par":
        return 2, True, words[1:]
    # "media docena"
    if len(words) >= 2 and words[0] in {"media", "medio"} and words[1] == "docena":
        return 6, True, words[2:]
    first = _parse_qty_token(words[0])
    if first is not None:
        qty, ok = first
        rest = words[1:]
        if rest and rest[0] == "docena" and ok:  # "2 docena(s)"
            qty, rest = qty * 12, rest[1:]
        return qty, ok, rest
    # "yerba x 3", "yerba x3", "yerba 3"
    if len(words) >= 2 and words[-2] == "x":
        last = _parse_qty_token(words[-1])
        if last is not None:
            return last[0], last[1], words[:-2]
    last = _parse_qty_token(words[-1])
    if last is not None:
        return last[0], last[1], words[:-1]
    # Último recurso: un único número en el medio ("me mandas 4 arroz porfa")
    found = [(i, q) for i, w in enumerate(words) if (q := _parse_qty_token(w)) is not None
             and w not in NUMBER_WORDS]
    if len(found) == 1:
        i, (qty, ok) = found[0]
        return qty, ok, words[:i] + words[i + 1:]
    return None, True, words


def _score(query: str, product: Product) -> float:
    q_tokens = set(query.split())
    best = 0.0
    for raw in [product.name, product.sku, *product.aliases]:
        key = _key(raw)
        if not key:
            continue
        if query == key:
            return 1.0
        k_tokens = set(key.split())
        if q_tokens and (q_tokens <= k_tokens or k_tokens <= q_tokens):
            best = max(best, 0.9)
            continue
        best = max(best, SequenceMatcher(None, query, key).ratio())
    return best


def match_product(query: str, products: list[Product], threshold: float):
    """Devuelve (producto|None, candidatos_ambiguos)."""
    query = _key(query)
    if not query:
        return None, []
    scored = sorted(((_score(query, p), p) for p in products), key=lambda t: t[0], reverse=True)
    scored = [(s, p) for s, p in scored if s >= threshold]
    if not scored:
        return None, []
    top_score, top = scored[0]
    if top_score == 1.0:
        return top, []
    close = [p for s, p in scored if top_score - s < 0.05]
    if len(close) > 1:
        return None, close
    return top, []


# Presentaciones mayoristas: todavía no se sabe cuántas unidades trae cada una (ver el plan
# de mejora, etapa 2). Si el cliente las nombra, NO se asume nada: se le pregunta.
BULK_WORDS = {"caja", "cajas", "cajon", "cajones", "pack", "packs", "bulto", "bultos", "fardo", "fardos",
              "sixpack", "sixpacks", "plancha", "planchas", "paquete", "paquetes", "jaula", "jaulas"}


def mentions_bulk(text: str) -> bool:
    """¿El renglón pide por caja, pack, bulto o fardo? («2 cajas de coca», «un six pack»)"""
    t = normalize(text)
    return bool(BULK_WORDS & set(t.split())) or "six pack" in t


def parse_order(text: str, products: list[Product], threshold: float = 0.72,
                max_qty: int = 1000) -> list[ParsedItem]:
    """Interpreta un mensaje y devuelve un ParsedItem por renglón reconocido."""
    items: list[ParsedItem] = []
    for line in split_lines(text):
        # Se quitan las palabras de relleno ANTES de buscar la cantidad
        # ("quiero 2 yerba" → "2 yerba").
        words = [w for w in line.split() if w and w not in FILLER]
        bulk = bool(BULK_WORDS & set(words)) or "six pack" in line
        words = [w for w in words if w not in BULK_WORDS and w != "six"]
        qty, valid, rest = _extract_qty(words)
        name_words = [w for w in rest if w not in ("x", "de")]
        if not name_words and qty is None:
            continue  # renglón de puro relleno ("hola", "gracias")
        item = ParsedItem(raw=line)
        if not name_words:
            item.error, item.unknown_name = "sin_producto", line
            items.append(item)
            continue
        query = " ".join(name_words)
        product, candidates = match_product(query, products, threshold)
        if candidates:
            item.error, item.candidates = "ambiguo", candidates
        elif product is None:
            item.error, item.unknown_name = "no_existe", query
        else:
            item.product = product
        if bulk:   # «2 cajas»: no son 2 unidades; se pregunta cuántas son
            item.error = item.error or "bulto"
        elif qty is None:
            item.error = item.error or "sin_cantidad"
        elif not valid or qty > max_qty:
            item.qty = qty
            item.error = item.error or "cantidad_invalida"
        else:
            item.qty = int(qty)
        items.append(item)
    return items
