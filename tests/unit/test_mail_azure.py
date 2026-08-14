"""Adaptador de correo de Azure — traducción del mensaje y trato de los fallos.

Nada aquí toca la red. Lo que se prueba es el mapeo, que es lo único capaz de
equivocarse en silencio: un adjunto mal codificado no da error, llega como un
PDF corrupto que el cliente no puede abrir y nadie se entera.
"""

import base64

import pytest
from azure.core.exceptions import HttpResponseError, ServiceRequestError

from receipt.infra.mail import Attachment, MailDeliveryError, Message
from receipt.infra.mail_azure import MAX_ATTACHMENT_BYTES, AzureMailer, to_payload

SENDER = "DoNotReply@abc123.azurecomm.net"


def _message(**overrides: object) -> Message:
    base = {
        "to": "cliente@example.mx",
        "subject": "Tu comprobante de pago A-0001",
        "body": "Hola, Ana:\n\nAquí está tu comprobante.",
    }
    return Message(**(base | overrides))  # type: ignore[arg-type]


class TestElMapeoDelMensaje:
    def test_it_puts_subject_body_recipient_and_sender_where_the_sdk_expects_them(self) -> None:
        payload = to_payload(_message(), sender=SENDER)

        assert payload["senderAddress"] == SENDER
        assert payload["recipients"]["to"] == [{"address": "cliente@example.mx"}]
        assert payload["content"]["subject"] == "Tu comprobante de pago A-0001"
        assert payload["content"]["plainText"].startswith("Hola, Ana:")

    def test_an_attachment_survives_the_round_trip_through_base64(self) -> None:
        """Lo que importa no es que haya base64: es que vuelvan los mismos bytes.

        El PDF de un recibo es el único artefacto que el cliente conserva. Una
        codificación mal hecha no lanza nada, llega rota.
        """
        original = b"%PDF-1.7\n\x00\x01\x02 bytes que no son texto \xff\xfe"
        payload = to_payload(
            _message(
                attachments=(
                    Attachment(
                        filename="recibo-A-0001.pdf",
                        content=original,
                        content_type="application/pdf",
                    ),
                )
            ),
            sender=SENDER,
        )

        (adjunto,) = payload["attachments"]
        assert adjunto["name"] == "recibo-A-0001.pdf"
        assert adjunto["contentType"] == "application/pdf"
        assert base64.b64decode(adjunto["contentInBase64"]) == original

    def test_without_attachments_the_key_is_absent(self) -> None:
        """No basta con que vaya vacía: Azure rechaza una lista vacía."""
        assert "attachments" not in to_payload(_message(), sender=SENDER)

    def test_several_attachments_keep_their_order(self) -> None:
        payload = to_payload(
            _message(
                attachments=(
                    Attachment(filename="uno.pdf", content=b"1"),
                    Attachment(filename="dos.pdf", content=b"2"),
                )
            ),
            sender=SENDER,
        )

        assert [a["name"] for a in payload["attachments"]] == ["uno.pdf", "dos.pdf"]


class TestElLimiteDeTamano:
    async def test_an_oversized_attachment_is_refused_before_touching_the_network(self) -> None:
        """Con una cadena de conexión inválida: si llegara a la red, fallaría distinto."""
        mailer = AzureMailer("esto-no-es-una-cadena-de-conexion", SENDER)
        enorme = Attachment(filename="gigante.pdf", content=b"x" * (MAX_ATTACHMENT_BYTES + 1))

        with pytest.raises(MailDeliveryError) as exc:
            await mailer.send(_message(attachments=(enorme,)))

        assert "pesa demasiado" in str(exc.value)

    async def test_the_size_counts_every_attachment_together(self) -> None:
        """El límite de Azure es por petición, no por archivo."""
        mailer = AzureMailer("esto-no-es-una-cadena-de-conexion", SENDER)
        mitad = MAX_ATTACHMENT_BYTES // 2 + 1
        adjuntos = (
            Attachment(filename="a.pdf", content=b"x" * mitad),
            Attachment(filename="b.pdf", content=b"x" * mitad),
        )

        with pytest.raises(MailDeliveryError):
            await mailer.send(_message(attachments=adjuntos))


class TestLosFallosDelProveedor:
    """Ningún error del SDK debe escapar hacia las rutas.

    Las rutas atrapan `MailDeliveryError`. Si se colara un `HttpResponseError`,
    subiría hasta el manejador genérico y el emisor vería un 500 en vez de la
    pantalla que le dice que comparta por WhatsApp.
    """

    @pytest.mark.parametrize(
        "fallo",
        [
            # Cuota agotada: con dominio gestionado, 10 correos por hora.
            HttpResponseError("(TooManyRequests) Rate limit exceeded"),
            # Remitente sin enlazar al recurso de Communication Services.
            HttpResponseError("(Unauthorized) sender domain not linked"),
            # La red se cayó a mitad.
            ServiceRequestError("connection reset"),
        ],
    )
    async def test_an_sdk_error_becomes_a_delivery_error(
        self, monkeypatch: pytest.MonkeyPatch, fallo: Exception
    ) -> None:
        import receipt.infra.mail_azure as adapter

        class ClienteQueFalla:
            async def __aenter__(self) -> "ClienteQueFalla":
                return self

            async def __aexit__(self, *exc: object) -> None:
                return None

            async def begin_send(self, payload: dict[str, object]) -> None:
                raise fallo

        monkeypatch.setattr(
            adapter.EmailClient, "from_connection_string", lambda _: ClienteQueFalla()
        )

        with pytest.raises(MailDeliveryError) as capturado:
            await AzureMailer("cadena", SENDER).send(_message())

        # La causa original se conserva para que el log sirva de algo.
        assert capturado.value.__cause__ is fallo

    async def test_a_message_that_goes_through_returns_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import receipt.infra.mail_azure as adapter

        enviados: list[dict[str, object]] = []

        class ClienteQueAcepta:
            async def __aenter__(self) -> "ClienteQueAcepta":
                return self

            async def __aexit__(self, *exc: object) -> None:
                return None

            async def begin_send(self, payload: dict[str, object]) -> object:
                enviados.append(payload)
                return object()  # el poller, que se abandona a propósito

        monkeypatch.setattr(
            adapter.EmailClient, "from_connection_string", lambda _: ClienteQueAcepta()
        )

        assert await AzureMailer("cadena", SENDER).send(_message()) is None
        assert enviados[0]["senderAddress"] == SENDER
