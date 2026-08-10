"""HTML → PDF con WeasyPrint (§3.2).

**No se usa un navegador headless.** Playwright o Puppeteer consumen 300–500 MB
de RAM por instancia y obligan a subir el tier de App Service, lo que multiplica
el costo mensual (§13). WeasyPrint renderiza en el mismo proceso y el recibo es
un documento simple: texto, tablas y tres imágenes.

El HTML que entra aquí es **autocontenido**: sin `base_url`, sin hojas de estilo
externas y con las imágenes ya como `data:` URI. Cualquier referencia a la red
desde el renderizador sería una petición saliente disparada por contenido de
usuario, y además no resolvería, porque el contenedor de blobs es privado.
"""

import io

from weasyprint import HTML


def render_pdf(html: str) -> bytes:
    """Convierte el documento del recibo en bytes de PDF.

    Es CPU-bound y bloquea el bucle de eventos mientras corre. Para un recibo de
    una página son decenas de milisegundos, así que no se manda a un hilo: el
    salto de contexto costaría más que el render. Si algún día el documento
    crece, aquí va un `run_in_executor`.
    """
    buffer = io.BytesIO()
    HTML(string=html).write_pdf(buffer)
    return buffer.getvalue()
