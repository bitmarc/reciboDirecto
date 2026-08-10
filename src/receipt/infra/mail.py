"""Envío de correo.

§3.1 sugiere Azure Communication Services o Resend/SendGrid, pero no decide, y
elegir proveedor obliga a dar de alta un servicio y verificar un dominio antes
de tener un solo usuario. Se define el puerto ahora y se enchufa el adaptador
real al desplegar, que es también cuando el hito 4 necesitará enviar recibos por
correo (§6, `POST /receipts/{id}/send-email`).
"""

import logging
from dataclasses import dataclass, field
from typing import Protocol

from receipt.config import get_settings

logger = logging.getLogger("receipt.mail")


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
        backend = get_settings().mail_backend
        _mailer = ConsoleMailer() if backend == "console" else RecordingMailer()
    return _mailer


def set_mailer(mailer: Mailer | None) -> None:
    """Sustituye el adaptador. Lo usan las pruebas."""
    global _mailer
    _mailer = mailer
