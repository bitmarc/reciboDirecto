"""Adaptador de correo contra Azure Communication Services.

Espeja la forma de `infra/blob.py`: el cliente se construye por llamada dentro
de un `async with`, sin conexión persistente. Un correo se manda cada varios
minutos en el mejor de los casos; sostener un cliente vivo para eso solo añade
una conexión que se puede quedar colgada.

**Cuota.** Con dominio gestionado por Azure el tope es de 5 correos por minuto y
10 por hora, *por suscripción*, sin posibilidad de ampliarlo. Recibos, ligas de
recuperación y confirmaciones de alta comparten ese cubo. Agotarlo no es un
imprevisto: es lo que pasa en una mañana con trabajo, y por eso el fallo de
envío está tratado como camino normal en las cuatro rutas que llaman aquí, no
como excepción rara.
"""

import base64
import logging
from collections.abc import Sequence
from typing import Any

from azure.communication.email.aio import EmailClient
from azure.core.exceptions import AzureError

from receipt.infra.mail import Attachment, MailDeliveryError, Message

logger = logging.getLogger("receipt.mail")

# Azure corta la petición en 10 MB, adjuntos incluidos, y base64 infla los bytes
# un tercio: 7 MB de contenido real son ~9.3 MB de petición. Se comprueba aquí
# para fallar con un mensaje que dice qué pasa, en vez de con el error del
# servicio, que no distingue «pesa demasiado» de «está mal formado».
#
# Un recibo real no se acerca: el PDF de una página con firma y logo ronda los
# 200 KB. Esto protege contra un logo patológico, no contra el uso normal.
MAX_ATTACHMENT_BYTES = 7 * 1024 * 1024


def to_payload(message: Message, *, sender: str) -> dict[str, Any]:
    """Traduce el `Message` del puerto a la forma que espera el SDK.

    Función pura y separada del cliente a propósito: el mapeo es lo único que
    puede equivocarse en silencio —un adjunto mal codificado llega como un PDF
    corrupto, no como un error— y así se prueba entero sin tocar la red.
    """
    payload: dict[str, Any] = {
        "senderAddress": sender,
        "recipients": {"to": [{"address": message.to}]},
        "content": {"subject": message.subject, "plainText": message.body},
    }
    if message.attachments:
        payload["attachments"] = [
            {
                "name": attachment.filename,
                "contentType": attachment.content_type,
                "contentInBase64": base64.b64encode(attachment.content).decode("ascii"),
            }
            for attachment in message.attachments
        ]
    return payload


def _check_size(attachments: Sequence[Attachment]) -> None:
    total = sum(len(attachment.content) for attachment in attachments)
    if total > MAX_ATTACHMENT_BYTES:
        raise MailDeliveryError("El archivo adjunto pesa demasiado para enviarlo por correo.")


class AzureMailer:
    """Implementa el puerto `Mailer` contra Communication Services."""

    def __init__(self, connection_string: str, sender: str) -> None:
        self._connection_string = connection_string
        self._sender = sender

    async def send(self, message: Message) -> None:
        """Encola el mensaje. Lanza `MailDeliveryError` si Azure lo rechaza.

        **Se espera al POST inicial, no a la entrega.** `begin_send` es una
        operación de larga duración: hace la petición, Azure responde 202 con un
        identificador, y el *poller* consulta después hasta que el reparto
        termina. En ese POST aparecen todos los errores sobre los que se puede
        hacer algo —remitente no autorizado, credencial mala, cuota agotada,
        adjunto excesivo— y ninguno de los que aparecen luego, que son rebotes y
        van a las métricas de Azure, no a una pantalla.

        Esperar el reparto añadiría segundos a la petición HTTP a cambio de
        información que el emisor no puede usar, y §1.4 da un minuto al flujo de
        cobro completo.
        """
        _check_size(message.attachments)
        try:
            async with EmailClient.from_connection_string(self._connection_string) as client:
                await client.begin_send(to_payload(message, sender=self._sender))
        except AzureError as exc:
            # El destinatario no entra al log: la dirección del cliente de un
            # emisor es dato personal (§10) y el log de App Service no es sitio
            # para ella. El asunto basta para saber qué flujo falló.
            logger.exception("Azure rechazó un envío (asunto: %s)", message.subject)
            raise MailDeliveryError("No pudimos enviar el correo en este momento.") from exc
