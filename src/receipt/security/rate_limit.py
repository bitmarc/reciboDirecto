"""Límite de intentos, en memoria (§10).

Ventana deslizante por clave. Se usa en login y en la recuperación de
contraseña, que son los dos formularios donde un atacante puede probar sin
límite.

**Limitación consciente:** el estado vive en el proceso. Se pierde al reiniciar
y no se comparte entre instancias. Con una sola instancia de App Service B1
(§13) es suficiente, y evita añadir Redis, que sería infraestructura pagada para
un contador. El día que se escale horizontalmente hay que moverlo a un almacén
compartido: hasta entonces, un atacante que reparta sus intentos entre réplicas
multiplicaría el límite por el número de réplicas.
"""

import time
from collections import deque
from dataclasses import dataclass, field

_LIMITERS: list["RateLimiter"] = []


def clear_all() -> None:
    """Olvida los intentos de **todos** los limitadores del proceso.

    Existe para la suite: el estado es global y, sin limpiarlo entre pruebas,
    unas hacen fallar a las siguientes por haber agotado el cupo. El registro
    evita el fallo clásico de agregar un limitador nuevo y descubrir semanas
    después que la suite se volvió intermitente por no haberlo añadido a mano.
    """
    for limiter in _LIMITERS:
        limiter.clear()


@dataclass
class RateLimiter:
    max_attempts: int
    window_seconds: int
    _attempts: dict[str, deque[float]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _LIMITERS.append(self)

    def _purge(self, key: str, now: float) -> deque[float]:
        window = self._attempts.setdefault(key, deque())
        cutoff = now - self.window_seconds
        while window and window[0] < cutoff:
            window.popleft()
        return window

    def is_blocked(self, key: str) -> bool:
        """`True` si la clave agotó su cupo. No consume intento."""
        return len(self._purge(key, time.monotonic())) >= self.max_attempts

    def record_attempt(self, key: str) -> None:
        now = time.monotonic()
        self._purge(key, now).append(now)

    def reset(self, key: str) -> None:
        """Olvida los intentos de una clave.

        Se llama tras un login correcto: quien acierta la contraseña no debe
        arrastrar el castigo de sus tecleos previos.
        """
        self._attempts.pop(key, None)

    def clear(self) -> None:
        self._attempts.clear()


def client_key(prefix: str, ip: str | None, identifier: str | None = None) -> str:
    """Clave de limitación.

    Se limita por IP **y** por identificador para cubrir los dos ataques: uno
    que prueba muchas contraseñas contra una cuenta, y otro que prueba una
    contraseña común contra muchas cuentas desde la misma IP.
    """
    return f"{prefix}:{ip or 'sin-ip'}:{(identifier or '').lower()}"
