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
            # Gana el que comparte más palabras: «fernet buhero» es Buhero, no Branca (alias «fernet»).
            best = max(best, 0.75 + 0.15 * len(q_tokens & k_tokens) / len(q_tokens))
            continue
        best = max(best, SequenceMatcher(None, query, key).ratio())
    return best


def match_product(query: str, products: list[Product], threshold: float, sizes: bool = True):
    """Devuelve (producto|None, candidatos_ambiguos). Con sizes=True tiene en cuenta el tamaño
    y el envase: si el producto viene en varios y no se dijo cuál, devuelve los candidatos."""
    if sizes:
        size, container, rest = pull_size(normalize(query).split(), allow_first=True)
        if not _key(" ".join(rest)):
            if size is None and container is None:
                return None, []
            # Solo el tamaño («750», «la de litro»): se elige entre los productos dados.
            return pick_size(products, products, size, container) if products else (None, [])
        product, candidates = match_product(" ".join(rest), products, threshold, sizes=False)
        pool = [product] if product else candidates
        if not pool:
            return None, []
        if candidates and len({family(p) for p in candidates}) > 1 and size is None and container is None:
            return None, candidates          # productos distintos: «¿cuál de estos?»
        return pick_size(pool, products, size, container)
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


# ------------------------------------------------------------------ tamaños y envases
# El mismo producto viene en varios tamaños (Branca 750ml / 1L / 450ml / miniatura) o envases
# (Heineken lata / porrón). Los productos de la misma «familia» (el nombre sin tamaño ni envase)
# se distinguen por lo que diga el cliente: «de litro», «750», «2.25», «lata», «latón», «chico».
# Si no dice nada, es una duda: se le pregunta el tamaño.
CONTAINERS = {"lata": "lata", "latas": "lata", "latita": "lata", "latitas": "lata", "laton": "laton",
              "latones": "laton", "porron": "porron", "porrones": "porron", "botella": "botella",
              "botellas": "botella", "botellita": "botella", "bidon": "bidon", "bidones": "bidon"}
SMALL_WORDS = {"chico", "chica", "chicos", "chicas", "chiquito", "chiquita", "petaca"}
BIG_WORDS = {"grande", "grandes"}
MINI_WORDS = {"miniatura", "miniaturas", "mini", "minis"}
# Tamaños comunes (ml): un número suelto así, después del producto, es el tamaño y no la cantidad.
KNOWN_SIZES = {187, 200, 250, 269, 300, 330, 340, 350, 354, 355, 375, 410, 450, 473, 500, 600, 700, 710,
               750, 930, 950, 970, 1000, 1250, 1500, 2000, 2250, 2500, 3000, 6000}
_UNIT_RE = re.compile(r"^(\d+(?:[.,]\d+)?)(ml|cc|cm3|l|lt|lts|ltr|litro|litros)$")
_LITER_WORDS = {"l", "lt", "lts", "ltr", "litro", "litros"}
_ML_WORDS = {"ml", "cc", "cm3"}


def _ml(value: float, unit: str) -> int:
    return int(round(value * 1000)) if unit in _LITER_WORDS else int(round(value))


def pull_size(words: list[str], allow_first: bool = False):
    """Saca de las palabras el tamaño y el envase que nombró el cliente.
    Devuelve (ml | "chico" | "grande" | None, envase | None, palabras restantes).
    Con allow_first=False, un número al principio se deja: es la cantidad («2 branca 750»)."""
    size, container, rest = None, None, []
    i = 0
    while i < len(words):
        w, nxt = words[i], words[i + 1] if i + 1 < len(words) else ""
        first = not rest and not allow_first and size is None and i == 0
        if w in CONTAINERS:
            container = CONTAINERS[w]
        elif w in MINI_WORDS:
            size = 50
        elif w in SMALL_WORDS:
            size = "chico"
        elif w in BIG_WORDS:
            size = "grande"
        elif w == "medio" and nxt in ("litro", "l", "lt"):
            size, i = 500, i + 1
        elif w in ("3/4", "tres/cuartos") or (w == "tres" and nxt == "cuartos"):
            size, i = 750, i + (1 if w == "tres" else 0)
        elif w in ("litro", "lt", "ltr") and size is None:
            size = 1000
        elif (m := _UNIT_RE.match(w)):
            size = _ml(float(m.group(1).replace(",", ".")), m.group(2))
        elif _QTY_RE.match(w) and not first and nxt in _LITER_WORDS | _ML_WORDS:
            size, i = _ml(float(w.replace(",", ".")), nxt), i + 1
            if nxt in _LITER_WORDS and i + 2 < len(words) + 1 and words[i + 1:i + 2] == ["1/4"]:
                size, i = size + 250, i + 1
        elif _QTY_RE.match(w) and not first and nxt == "1/4":     # «2 1/4» = 2,25 L
            size, i = int(w) * 1000 + 250 if w.isdigit() else None, i + 1
        elif _QTY_RE.match(w) and not first and "." in w.replace(",", "."):   # «coca 2.25», «1,5»
            v = float(w.replace(",", "."))
            size = _ml(v, "l") if v < 10 else int(v)
        elif w.isdigit() and not first and int(w) in KNOWN_SIZES and (rest or i > 0 or allow_first):
            size = int(w)
        else:
            rest.append(w)
        i += 1
    return size, container, rest


def size_of(product: Product):
    """(ml | None, envase | None) del producto, sacados de su nombre."""
    size, container, _ = pull_size(normalize(product.name).split(), allow_first=True)
    return (size if isinstance(size, int) else None), container


def family(product: Product) -> str:
    """El nombre sin tamaño ni envase: «Fernet Branca 750ml» y «Fernet Branca 1L» → «fernet branca»."""
    _, _, rest = pull_size(normalize(product.name).split(), allow_first=True)
    return _key(" ".join(rest))


def size_label(product: Product, siblings: list[Product]) -> str:
    """Lo que distingue a un producto de sus hermanos: «750ml», «1L», «Lata 375ml», «Miniatura»."""
    if len({family(p) for p in siblings}) > 1:
        return product.name                  # productos distintos: el nombre entero
    fam = set(family(product).split())
    words = [w for w in product.name.split() if _key(w) not in fam or not _key(w)]
    return " ".join(words) or product.name


def _fits(product: Product, size, container) -> bool:
    p_size, p_cont = size_of(product)
    if isinstance(size, int) and p_size is not None and p_size != size:
        return False
    if container:
        if container in ("lata", "laton"):
            if p_cont not in ("lata", "laton") or (container == "laton" and (p_size or 0) < 700):
                return False
        elif container in ("botella", "porron"):
            if p_cont in ("lata", "laton", "bidon"):
                return False
        elif p_cont != container:
            return False
    return True


def _by_size(products: list[Product]) -> list[Product]:
    return sorted(products, key=lambda p: -(size_of(p)[0] or 0))


def pick_size(pool: list[Product], products: list[Product], size, container) -> tuple[Product | None, list[Product]]:
    """Entre los productos de la familia de `pool`, el del tamaño pedido. Sin tamaño, si hay
    varios, es una duda (se devuelven los candidatos ordenados de mayor a menor)."""
    fams = {family(p) for p in pool}
    siblings = _by_size([p for p in products if family(p) in fams]) or pool
    if size is None and container is None:
        return (siblings[0], []) if len(siblings) == 1 else (None, siblings)
    fit = [p for p in siblings if _fits(p, size if isinstance(size, int) else None, container)]
    if size in ("chico", "grande") and fit:
        # «chico» es la botella más chica, no la miniatura (salvo que no haya otra)
        sized = [p for p in fit if (size_of(p)[0] or 0) > 50] or [p for p in fit if size_of(p)[0]]
        if sized:
            fit = [sized[-1] if size == "chico" else sized[0]]
    if len(fit) == 1:
        return fit[0], []
    if not fit and len(siblings) == 1 and not isinstance(size, int):
        return siblings[0], []     # «latón de corona» y solo hay una Corona: es esa
    return None, fit or siblings


def mentions_size(text: str) -> bool:
    size, container, _ = pull_size(normalize(text).split())
    return size is not None or container is not None


# Presentaciones mayoristas: todavía no se sabe cuántas unidades trae cada una (ver el plan
# de mejora, etapa 2). Si el cliente las nombra, NO se asume nada: se le pregunta.
BULK_WORDS = {"caja", "cajas", "cajon", "cajones", "pack", "packs", "bulto", "bultos", "fardo", "fardos",
              "sixpack", "sixpacks", "plancha", "planchas", "paquete", "paquetes", "jaula", "jaulas"}


LOOSE_WORDS = {"suelta", "sueltas", "suelto", "sueltos"}   # «3 cocas sueltas» = 3 unidades


def bulk_units(text: str, product: Product, max_qty: int = 1000) -> int | None:
    """«2 cajas de coca» → 2 × unidades por caja del producto. None si no se puede calcular
    (no hay caja cargada, falta la cantidad de cajas o es inválida)."""
    if product.pack_size <= 0:
        return None
    words = [w for w in normalize(text).split()
             if w not in FILLER and w not in BULK_WORDS and w not in LOOSE_WORDS and w != "six"]
    qty, valid, _ = _extract_qty(words)
    if qty is None or not valid or qty * product.pack_size > max_qty:
        return None
    return int(qty) * product.pack_size


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
        words = [w for w in line.split() if w and w not in FILLER and w not in LOOSE_WORDS]
        bulk = bool(BULK_WORDS & set(words)) or "six pack" in line
        words = [w for w in words if w not in BULK_WORDS and w != "six"]
        size, container, words = pull_size(words)      # «branca 750 x 2»: 750 es el tamaño
        qty, valid, rest = _extract_qty(words)
        name_words = [w for w in rest if w not in ("x", "de")]
        if name_words:
            name_words += [f"{size}ml" if isinstance(size, int) else size or "", container or ""]
            name_words = [w for w in name_words if w]
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
        if bulk and item.product is not None and item.product.pack_size > 0 and not item.error:
            # «2 cajas de coca» y la caja trae 6 → 12 unidades. «cajas de coca» → ¿cuántas?
            if qty is None:
                item.error = "sin_cantidad"
            elif not valid or qty * item.product.pack_size > max_qty:
                item.qty, item.error = qty, "cantidad_invalida"
            else:
                item.qty = int(qty) * item.product.pack_size
        elif bulk:   # sin caja cargada: no son 2 unidades; se pregunta cuántas son
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
