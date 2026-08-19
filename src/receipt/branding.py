"""El nombre con el que el producto se presenta.

Vive en un módulo propio y no en `web/labels.py`, que sería el sitio obvio junto
a `leyenda_legal`: la marca la necesitan tres capas —el título de la aplicación,
los asuntos de correo en `services/` y las plantillas—, y `services` no puede
importar de `web` sin invertir la dirección de dependencias del proyecto
(`domain ← services ← web`).

Tampoco va en `config.py`, que es configuración leída del entorno: el nombre del
producto no es algo que se ajuste por despliegue.

**«Recibo» a secas no es la marca.** Es el sustantivo que nombra el documento que
esto produce, y aparece cientos de veces con ese sentido —«RECIBO DE PAGO»,
«Recibí de {cliente}», `/recibos`—. Solo lo que se refiere al *producto* sale de
aquí.
"""

APP_NAME = "ReciboDirecto"
