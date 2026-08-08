"""Validación y normalización de imágenes subidas por el emisor.

Un `data:` URI que llega del navegador es entrada no confiable: puede traer un
SVG con script, un HTML disfrazado de PNG, o una imagen de 40.000 × 40.000 px
cuya descompresión agota la memoria del proceso.

Todo lo que entra sale **reencodificado a PNG**, aunque haya entrado como PNG.
Es lo que elimina metadatos por construcción —el logo que alguien exporte desde
su celular puede traer coordenadas GPS en el EXIF— y deja un solo formato aguas
abajo, que es lo que WeasyPrint necesita para el PDF del hito 3.

Módulo de dominio: no toca red, ni disco, ni base de datos. Recibe bytes y
devuelve bytes.
"""

import base64
import binascii
import io
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from PIL import Image, ImageFile, UnidentifiedImageError

# SVG queda deliberadamente fuera de cualquier lista: es un documento XML que
# puede contener <script>, no un mapa de bits.
PNG_ONLY = frozenset({"PNG"})
PNG_OR_JPEG = frozenset({"PNG", "JPEG"})

_DATA_URI = re.compile(
    r"^data:image/(?P<subtype>png|jpeg|jpg);base64,(?P<payload>[A-Za-z0-9+/=\s]+)$",
    re.IGNORECASE,
)


class ImageRejected(ValueError):
    """La imagen no pasa la validación. El mensaje es apto para el usuario."""


@contextmanager
def _reject_truncated_images() -> Iterator[None]:
    """Fuerza que Pillow falle ante una imagen incompleta.

    No basta con confiar en el valor por omisión: **WeasyPrint pone
    `ImageFile.LOAD_TRUNCATED_IMAGES = True` al importarse**
    (`weasyprint/images.py`), y desde el hito 3 WeasyPrint estará siempre
    importado. Sin este ajuste, una firma a medias se aceptaría en silencio,
    rellenada con lo que Pillow alcance a decodificar, y se estamparía así en
    todos los recibos.

    Se restaura el valor anterior porque WeasyPrint lo necesita en `True` para
    tolerar imágenes imperfectas al renderizar. La bandera es global del módulo,
    así que esto solo es seguro mientras la validación ocurra en un único hilo,
    que es el caso: corre dentro del bucle de eventos.
    """
    previous = ImageFile.LOAD_TRUNCATED_IMAGES
    ImageFile.LOAD_TRUNCATED_IMAGES = False
    try:
        yield
    finally:
        ImageFile.LOAD_TRUNCATED_IMAGES = previous


@dataclass(frozen=True)
class ImageLimits:
    """Límites de una clase de imagen."""

    max_bytes: int
    max_width: int
    max_height: int
    min_width: int
    min_height: int
    accepted_formats: frozenset[str]


# La firma se estampa sobre el recibo, así que **solo PNG**: un JPEG no tiene
# canal alfa y llegaría con un rectángulo opaco alrededor del trazo (§3.1). Un
# PNG de canvas ronda los 10–30 KB; 512 KB deja margen de sobra.
SIGNATURE_LIMITS = ImageLimits(
    max_bytes=512 * 1024,
    max_width=2000,
    max_height=1000,
    min_width=50,
    min_height=20,
    accepted_formats=PNG_ONLY,
)

# El logo llega de la galería del emisor, donde lo normal es un JPEG.
LOGO_LIMITS = ImageLimits(
    max_bytes=2 * 1024 * 1024,
    max_width=3000,
    max_height=3000,
    min_width=32,
    min_height=32,
    accepted_formats=PNG_OR_JPEG,
)


def decode_data_uri(value: str) -> bytes:
    """Extrae los bytes de un `data:image/...;base64,...`.

    Rechaza cualquier otra cosa antes de intentar decodificar: es más barato y
    no deja pasar un `data:text/html` con el prefijo maquillado.
    """
    match = _DATA_URI.match(value.strip())
    if match is None:
        raise ImageRejected("La imagen no llegó en un formato que podamos leer.")

    try:
        return base64.b64decode(match.group("payload"), validate=False)
    except (binascii.Error, ValueError) as exc:
        raise ImageRejected("La imagen llegó incompleta. Vuelve a intentarlo.") from exc


def normalize_to_png(raw: bytes, limits: ImageLimits) -> bytes:
    """Valida `raw` contra `limits` y devuelve un PNG limpio.

    El orden importa: se comprueba el tamaño en bytes, luego las dimensiones
    declaradas en la cabecera y solo al final se decodifican los píxeles. Abrir
    y medir no materializa el mapa de bits; `load()` sí. Invertir el orden es
    exactamente lo que convierte una imagen maliciosa en una caída del proceso.
    """
    if not raw:
        raise ImageRejected("No se recibió ninguna imagen.")

    if len(raw) > limits.max_bytes:
        raise ImageRejected(
            f"La imagen pesa más de {limits.max_bytes // 1024} KB. Usa una más ligera."
        )

    try:
        with _reject_truncated_images(), Image.open(io.BytesIO(raw)) as image:
            # El tipo declarado en el `data:` URI puede mentir; el formato que
            # Pillow deduce de los bytes, no.
            if image.format not in limits.accepted_formats:
                expected = " o ".join(sorted(limits.accepted_formats))
                raise ImageRejected(f"El formato no sirve aquí. Se esperaba {expected}.")

            width, height = image.size  # de la cabecera, sin decodificar píxeles
            if width > limits.max_width or height > limits.max_height:
                raise ImageRejected(
                    f"La imagen es demasiado grande "
                    f"({width}×{height} px). El máximo es "
                    f"{limits.max_width}×{limits.max_height} px."
                )
            if width < limits.min_width or height < limits.min_height:
                raise ImageRejected("La imagen es demasiado pequeña para usarse.")

            # Todo sale en RGBA: la firma necesita el alfa y, para el logo, un
            # PNG con canal alfa deja que un logo recortado se estampe sin caja
            # blanca alrededor.
            converted = image.convert("RGBA")

            # Se escribe en una imagen nueva y no se copia `info`, que es donde
            # viven EXIF, ICC y comentarios. Los metadatos se pierden porque
            # nunca se transfieren, no porque se borren después.
            clean = Image.new("RGBA", converted.size)
            clean.paste(converted)

            buffer = io.BytesIO()
            clean.save(buffer, format="PNG", optimize=True)
            return buffer.getvalue()

    except ImageRejected:
        raise
    except UnidentifiedImageError as exc:
        raise ImageRejected("El archivo no es una imagen válida.") from exc
    except (OSError, ValueError) as exc:
        # Pillow levanta OSError tanto en archivos truncados como al superar
        # MAX_IMAGE_PIXELS, su propia guarda contra bombas de descompresión.
        raise ImageRejected("No pudimos procesar la imagen. Prueba con otra.") from exc


def signature_png_from_data_uri(value: str) -> bytes:
    """PNG con transparencia listo para estamparse en el recibo (RN-20)."""
    return normalize_to_png(decode_data_uri(value), SIGNATURE_LIMITS)


def logo_png(raw: bytes) -> bytes:
    """PNG del logo del emisor, solo utilizable en plan de pago (RN-34)."""
    return normalize_to_png(raw, LOGO_LIMITS)
