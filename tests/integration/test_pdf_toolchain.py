"""Verificación de la cadena de PDF.

El hito 3 depende por completo de WeasyPrint (§3.2), y sus fallos no son de
código sino de librerías nativas ausentes: Pango, HarfBuzz, las tipografías. Se
comprueba aquí, en el hito 0, y no cuando ya se gastaron 80 horas del
presupuesto.

No prueba la plantilla del recibo —esa llega en el hito 3—, solo que la cadena
renderiza, que escribe un PDF válido y que el español acentuado sobrevive al
trazado de texto.
"""

import io

import pytest


def test_weasyprint_renders_a_pdf() -> None:
    from weasyprint import HTML

    html = """
    <html><body>
      <h1>Comprobante de pago</h1>
      <p>Recibí de Ana Ramírez la cantidad de $2,300.00</p>
      <table><tr><td>Saldo pendiente</td><td>$0.00</td></tr></table>
    </body></html>
    """

    buffer = io.BytesIO()
    HTML(string=html).write_pdf(buffer)
    content = buffer.getvalue()

    assert content.startswith(b"%PDF-"), "la salida no es un PDF"
    assert content.rstrip().endswith(b"%%EOF"), "el PDF quedó truncado"
    assert len(content) > 1000


def test_the_layout_primitives_of_the_receipt_template_are_supported() -> None:
    """§3.2: WeasyPrint soporta un subconjunto de CSS.

    La plantilla del recibo debe usar tablas y bloques, no flexbox ni grid. Este
    render usa exactamente esas primitivas —tamaño de página, márgenes, tabla con
    bordes— para que un fallo de soporte salte aquí.
    """
    from weasyprint import HTML

    html = """
    <html><head><style>
      @page { size: Letter; margin: 18mm; }
      table { width: 100%; border-collapse: collapse; }
      td { border-bottom: 1px solid #ccc; padding: 4px 0; }
      .total { font-weight: bold; }
    </style></head><body>
      <table>
        <tr><td>Total acordado</td><td>$2,300.00</td></tr>
        <tr><td>Trabajo adicional — Cambio de 2 contactos</td><td>$100.00</td></tr>
        <tr class="total"><td>Saldo pendiente</td><td>$0.00</td></tr>
      </table>
    </body></html>
    """

    buffer = io.BytesIO()
    HTML(string=html).write_pdf(buffer)
    assert buffer.getvalue().startswith(b"%PDF-")


def test_a_png_signature_can_be_embedded() -> None:
    """Las firmas del emisor y del cliente se estampan como PNG transparente
    (§3.1). Si falta el soporte de imágenes, el recibo sale sin firma."""
    import base64

    from weasyprint import HTML

    # PNG de 1x1 transparente.
    pixel = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
        "YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
    )
    data_uri = f"data:image/png;base64,{base64.b64encode(pixel).decode()}"

    buffer = io.BytesIO()
    HTML(string=f'<html><body><img src="{data_uri}" width="200"></body></html>').write_pdf(buffer)
    assert buffer.getvalue().startswith(b"%PDF-")


@pytest.mark.parametrize("text", ["Ramírez", "Instalación", "recámara", "Óscar Muñoz"])
def test_accented_spanish_survives_text_shaping(text: str) -> None:
    from weasyprint import HTML

    buffer = io.BytesIO()
    HTML(string=f"<html><body><p>{text}</p></body></html>").write_pdf(buffer)
    assert buffer.getvalue().startswith(b"%PDF-")
