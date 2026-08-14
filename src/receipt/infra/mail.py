"""Envío de correo.

§3.1 sugiere Azure Communication Services o Resend/SendGrid, pero no decide. Se
eligió Azure Communication Services; el adaptador vive aparte, en `mail_azure`,
para que este módulo siga siendo el puerto sin dependencias de proveedor.

Este archivo define **el contrato**, y una parte del contrato es que `send()`
puede fallar: ver `MailDeliveryError`. `ConsoleMailer` y `RecordingMailer` no
fallan nunca, así que las pruebas que usan solo esos dos no ejercitan la mitad
interesante de las rutas que mandan correo.
"""

import logging
from dataclasses import dataclass, field
from typing import Protocol

from receipt.config import get_settings

logger = logging.getLogger("receipt.mail")


class MailDeliveryError(RuntimeError):
    """El proveedor no aceptó el mensaje.

    **No hereda de `DomainError`, y es deliberado.** `DomainError` significa
    «el usuario capturó algo mal» y las rutas lo traducen a un 400 con el
    mensaje puesto junto al campo. Una cuota agotada o una credencial caducada
    no son eso: no hay nada que el emisor pueda corregir en el formulario, y
    contestarle 400 le echaría la culpa de una avería nuestra.

    Que sea `RuntimeError` obliga además a atraparlo explícitamente en cada
    punto de envío. Son cuatro y cada uno decide distinto: el alta de cuenta se
    completa igual, la recuperación de contraseña calla por seguridad y el
    envío del recibo lo grita. Un padre común con los errores de dominio
    invitaría a tratarlos juntos, que es justo lo que no hay que hacer.
    """


@dataclass(frozen=True)
class Attachment:
    """Archivo que viaja con el mensaje.

    Existe por el envío del recibo (§7.2): el cliente se queda con su copia sin
    tener que hacer clic, que es exactamente lo que le pide la leyenda de §7.3.
    Una liga suelta depende de que la abra, y un correo que solo trae un enlace
    se parece demasiado a un intento de phishing.
    """

    filename: str
    content: bytes
    content_type: str = "application/octet-stream"


@dataclass(frozen=True)
class Message:
    to: str
    subject: str
    body: str
    attachments: tuple[Attachment, ...] = ()


class Mailer(Protocol):
    async def send(self, message: Message) -> None: ...


class ConsoleMailer:
    """Escribe el correo al log en lugar de enviarlo.

    Es el adaptador de desarrollo: la liga de recuperación aparece en
    `docker compose logs -f app` y el flujo completo se puede recorrer sin
    proveedor.
    """

    async def send(self, message: Message) -> None:
        adjuntos = (
            "\n".join(
                f"Adjunto: {a.filename} ({len(a.content)} bytes, {a.content_type})"
                for a in message.attachments
            )
            or "—"
        )
        logger.info(
            "\n"
            "─── CORREO (no enviado, adaptador de consola) ───\n"
            "Para:    %s\n"
            "Asunto:  %s\n"
            "%s\n"
            "%s\n"
            "────────────────────────────────────────────────",
            message.to,
            message.subject,
            message.body,
            adjuntos,
        )


@dataclass
class RecordingMailer:
    """Guarda los mensajes en memoria. Para las pruebas."""

    sent: list[Message] = field(default_factory=list)

    async def send(self, message: Message) -> None:
        self.sent.append(message)

    def last_to(self, address: str) -> Message | None:
        for message in reversed(self.sent):
            if message.to == address:
                return message
        return None


_mailer: Mailer | None = None


def get_mailer() -> Mailer:
    global _mailer
    if _mailer is None:
        settings = get_settings()
        if settings.mail_backend == "azure":
            # Importación diferida: quien desarrolla en local con `console` no
            # tiene por qué cargar el SDK de Azure para arrancar la aplicación.
            from receipt.infra.mail_azure import AzureMailer

            _mailer = AzureMailer(settings.acs_connection_string, settings.acs_sender_address)
        elif settings.mail_backend == "console":
            _mailer = ConsoleMailer()
        else:
            _mailer = RecordingMailer()
    return _mailer


def set_mailer(mailer: Mailer | None) -> None:
    """Sustituye el adaptador. Lo usan las pruebas."""
    global _mailer
    _mailer = mailer
