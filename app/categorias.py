"""
Categorías del catálogo (la división de un mayorista de bebidas de Tucumán) y cómo se
agrupan en el chat.

En la web y en la base, cada producto lleva una de las CATEGORIAS. En el chat, una lista de
WhatsApp admite como mucho 10 opciones, así que el catálogo se muestra por GRUPOS: al tocar
uno llegan sus productos ordenados por categoría. Una categoría que no está en ningún grupo
(cargada a mano) aparece como grupo propio.
"""
from __future__ import annotations

import unicodedata

CATEGORIAS = [
    "Vinos", "Vinos Blancos/Rosados", "Espumante", "Whisky", "Gin", "Vodka", "Ron", "Cervezas",
    "Fernet", "Aperitivos", "Licores", "Sin Alcohol", "Energizantes", "Combos", "Regalería",
]

# (nombre en el chat — hasta 24 letras, el límite de WhatsApp —, categorías que agrupa)
GRUPOS: list[tuple[str, list[str]]] = [
    ("Vinos y espumantes", ["Vinos", "Vinos Blancos/Rosados", "Espumante"]),
    ("Cervezas", ["Cervezas"]),
    ("Fernet y aperitivos", ["Fernet", "Aperitivos"]),
    ("Whisky, gin, vodka, ron", ["Whisky", "Gin", "Vodka", "Ron"]),
    ("Licores", ["Licores"]),
    ("Sin alcohol", ["Sin Alcohol", "Energizantes"]),
    ("Combos y regalería", ["Combos", "Regalería"]),
]


def clave(texto: str) -> str:
    """Para comparar sin tildes, mayúsculas ni signos: «Regalería» = «regaleria»."""
    t = unicodedata.normalize("NFKD", (texto or "").lower())
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    t = "".join(ch if ch.isalnum() else " " for ch in t)
    return " ".join(t.split())


def grupo_de(categoria: str) -> str:
    """El grupo del chat de una categoría (o la categoría misma si no está en ninguno)."""
    k = clave(categoria)
    for nombre, cats in GRUPOS:
        if k in {clave(c) for c in cats}:
            return nombre
    return categoria or "Otras"


def orden_de(categoria: str) -> tuple[int, str]:
    """Para ordenar: primero en el orden de los grupos y de CATEGORIAS, después alfabético."""
    k = clave(categoria)
    for i, c in enumerate(CATEGORIAS):
        if clave(c) == k:
            return i, ""
    return len(CATEGORIAS), k
