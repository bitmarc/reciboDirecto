"""Configuración de registro.

Uvicorn configura sus propios loggers y deja el raíz sin manejador, así que un
`logger.info` de la aplicación no aparece por ningún lado si nadie lo engancha.
Eso deja mudo al adaptador de correo de consola, que existe justamente para que
la liga de recuperación se pueda leer durante el desarrollo.
"""

import logging
import sys

ROOT_LOGGER = "receipt"


def configure_logging(*, debug: bool = False) -> None:
    """Engancha el logger de la aplicación a la salida estándar.

    Idempotente: `create_app()` se llama muchas veces en la suite y duplicar
    manejadores multiplicaría cada línea.
    """
    logger = logging.getLogger(ROOT_LOGGER)
    if logger.handlers:
        return

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG if debug else logging.INFO)

    # Sin propagar: uvicorn ya imprime lo suyo y no hace falta duplicar.
    logger.propagate = False
