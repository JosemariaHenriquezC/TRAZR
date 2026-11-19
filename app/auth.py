"""
Contraseñas de los admins de la web: hash con scrypt (librería estándar).

Formato guardado: "scrypt$<n>$<r>$<p>$<sal hex>$<hash hex>". Nunca se guarda ni se
loguea la contraseña en texto plano.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets

N, R, P, DKLEN = 2 ** 14, 8, 1, 32
MIN_PASSWORD_LEN = 8


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=N, r=R, p=P, dklen=DKLEN)
    return f"scrypt${N}${R}${P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt, digest = stored.split("$")
        if algo != "scrypt":
            return False
        calc = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r),
                              p=int(p), dklen=len(bytes.fromhex(digest)))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(calc.hex(), digest)
