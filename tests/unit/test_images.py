"""Validación de imágenes subidas por el emisor.

Un `data:` URI del navegador es entrada no confiable: aquí se comprueba que lo
que no es una imagen se rechaza, y que lo que sí lo es sale limpio de metadatos.
"""

import base64
import io
import os

import pytest
from PIL import Image, ImageFile

from receipt.domain.images import (
    LOGO_LIMITS,
    SIGNATURE_LIMITS,
    ImageRejected,
    decode_data_uri,
    normalize_to_png,
    signature_png_from_data_uri,
)


def make_image(
    fmt: str = "PNG", size: tuple[int, int] = (400, 150), mode: str = "RGBA", **save: object
) -> bytes:
    image = Image.new(mode, size, (0, 0, 0, 0) if mode == "RGBA" else (255, 255, 255))
    buffer = io.BytesIO()
    image.save(buffer, format=fmt, **save)
    return buffer.getvalue()


def as_data_uri(raw: bytes, subtype: str = "png") -> str:
    return f"data:image/{subtype};base64,{base64.b64encode(raw).decode()}"


def truncated_png() -> bytes:
    """PNG de ruido, cortado a la mitad.

    El ruido lo hace incompresible: así el corte pierde datos de verdad, cosa
    que no ocurre con una imagen de color uniforme.
    """
    noisy = Image.frombytes("RGBA", (200, 200), os.urandom(200 * 200 * 4))
    buffer = io.BytesIO()
    noisy.save(buffer, format="PNG")
    raw = buffer.getvalue()
    assert len(raw) > 10_000, "la imagen de prueba debe ser incompresible"
    return raw[: len(raw) // 2]


class TestDataUriParsing:
    def test_a_valid_png_data_uri_is_decoded(self) -> None:
        raw = make_image()
        assert decode_data_uri(as_data_uri(raw)) == raw

    @pytest.mark.parametrize(
        "value",
        [
            "",
            "no es un data uri",
            "data:text/html;base64,PHNjcmlwdD4=",
            "data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=",
            "https://ejemplo.mx/firma.png",
            "javascript:alert(1)",
        ],
    )
    def test_anything_that_is_not_an_image_data_uri_is_rejected(self, value: str) -> None:
        with pytest.raises(ImageRejected):
            decode_data_uri(value)

    def test_svg_is_rejected_because_it_can_carry_script(self) -> None:
        svg = b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>"
        with pytest.raises(ImageRejected):
            decode_data_uri(as_data_uri(svg, "svg+xml"))


class TestNormalization:
    def test_a_png_comes_out_as_a_png(self) -> None:
        result = normalize_to_png(make_image(), SIGNATURE_LIMITS)
        with Image.open(io.BytesIO(result)) as image:
            assert image.format == "PNG"

    def test_a_jpeg_logo_is_converted_to_png(self) -> None:
        result = normalize_to_png(make_image("JPEG", mode="RGB"), LOGO_LIMITS)
        with Image.open(io.BytesIO(result)) as image:
            assert image.format == "PNG"

    def test_the_signature_keeps_its_alpha_channel(self) -> None:
        """§3.1: fondo transparente para estamparse sobre el recibo."""
        result = signature_png_from_data_uri(as_data_uri(make_image()))
        with Image.open(io.BytesIO(result)) as image:
            assert image.mode == "RGBA"

    def test_metadata_does_not_survive(self) -> None:
        """Un logo exportado desde un celular puede traer GPS en el EXIF."""
        with_exif = make_image(
            "JPEG", mode="RGB", exif=Image.Exif().tobytes(), comment=b"ubicacion secreta"
        )
        result = normalize_to_png(with_exif, LOGO_LIMITS)

        with Image.open(io.BytesIO(result)) as image:
            assert not image.getexif()
            assert "comment" not in image.info
            assert "exif" not in image.info

    def test_dimensions_are_preserved(self) -> None:
        result = normalize_to_png(make_image(size=(640, 200)), SIGNATURE_LIMITS)
        with Image.open(io.BytesIO(result)) as image:
            assert image.size == (640, 200)


class TestLimits:
    def test_empty_input_is_rejected(self) -> None:
        with pytest.raises(ImageRejected, match="ninguna imagen"):
            normalize_to_png(b"", SIGNATURE_LIMITS)

    def test_something_that_is_not_an_image_is_rejected(self) -> None:
        with pytest.raises(ImageRejected):
            normalize_to_png(b"<html><body>no soy una imagen</body></html>", SIGNATURE_LIMITS)

    def test_a_truncated_png_is_rejected(self) -> None:
        """La imagen tiene ruido a propósito.

        Un PNG de color uniforme comprime tanto que cabe entero en los primeros
        bytes, y truncarlo no lo rompe: la prueba pasaría sin comprobar nada.
        """
        with pytest.raises(ImageRejected):
            normalize_to_png(truncated_png(), SIGNATURE_LIMITS)

    def test_a_truncated_png_is_still_rejected_after_weasyprint_is_imported(
        self,
    ) -> None:
        """Regresión de un fallo silencioso.

        WeasyPrint pone `ImageFile.LOAD_TRUNCATED_IMAGES = True` al importarse.
        Desde el hito 3 estará siempre importado, así que sin aislar la bandera
        una firma a medias se aceptaría rellenada con lo que Pillow alcanzara a
        decodificar. Esta prueba falla si alguien quita ese aislamiento.
        """
        import weasyprint  # noqa: F401  (el efecto es el import en sí)

        assert ImageFile.LOAD_TRUNCATED_IMAGES is True, (
            "WeasyPrint ya no cambia la bandera; el aislamiento puede simplificarse"
        )

        with pytest.raises(ImageRejected):
            normalize_to_png(truncated_png(), SIGNATURE_LIMITS)

        assert ImageFile.LOAD_TRUNCATED_IMAGES is True, (
            "la bandera debe restaurarse: WeasyPrint la necesita en True para renderizar"
        )

    def test_an_oversized_payload_is_rejected_before_decoding(self) -> None:
        with pytest.raises(ImageRejected, match="pesa más"):
            normalize_to_png(
                b"\x89PNG\r\n\x1a\n" + b"x" * SIGNATURE_LIMITS.max_bytes, SIGNATURE_LIMITS
            )

    def test_an_image_larger_than_the_limit_is_rejected(self) -> None:
        too_wide = make_image(size=(SIGNATURE_LIMITS.max_width + 10, 100))
        with pytest.raises(ImageRejected, match="demasiado grande"):
            normalize_to_png(too_wide, SIGNATURE_LIMITS)

    def test_a_stray_click_is_rejected_as_too_small(self) -> None:
        with pytest.raises(ImageRejected, match="demasiado pequeña"):
            normalize_to_png(make_image(size=(4, 4)), SIGNATURE_LIMITS)

    def test_a_jpeg_disguised_as_a_png_data_uri_is_rejected_for_signatures(self) -> None:
        """El `data:` URI puede mentir sobre el tipo; los bytes no.

        La firma solo acepta PNG porque un JPEG no tiene canal alfa y llegaría
        al recibo con un rectángulo opaco alrededor del trazo.
        """
        jpeg_pretending_to_be_png = as_data_uri(make_image("JPEG", mode="RGB"), "png")
        with pytest.raises(ImageRejected, match="formato"):
            signature_png_from_data_uri(jpeg_pretending_to_be_png)

    def test_the_logo_does_accept_jpeg(self) -> None:
        """Lo que sale de la galería de un celular casi siempre es JPEG."""
        assert normalize_to_png(make_image("JPEG", mode="RGB"), LOGO_LIMITS)
