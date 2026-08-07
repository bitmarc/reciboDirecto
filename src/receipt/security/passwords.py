"""Contraseñas con Argon2id (§3.1, §10)."""

import contextlib
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

# Parámetros por omisión de argon2-cffi, que ya son Argon2id y siguen la
# recomendación de OWASP. Se mantienen explícitos para que un cambio sea una
# decisión visible en el diff y no un efecto de actualizar la librería.
_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)

MIN_PASSWORD_LENGTH = 8


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    """`True` si el hash se generó con parámetros más débiles que los actuales."""
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


def dummy_verify() -> None:
    """Consume tiempo equivalente a una verificación real.

    Se llama cuando el correo no existe, para que el tiempo de respuesta del
    login no revele qué cuentas están dadas de alta.
    """
    with contextlib.suppress(VerifyMismatchError, InvalidHashError):
        _hasher.verify(_PLACEHOLDER_HASH, secrets.token_urlsafe(16))


_PLACEHOLDER_HASH = _hasher.hash("placeholder-para-igualar-el-tiempo-de-respuesta")
