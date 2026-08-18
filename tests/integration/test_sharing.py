"""Compartir el recibo — copiar liga, WhatsApp y correo (E6, RN-32).

**El envío es siempre manual.** Nada de esto dispara un mensaje por su cuenta:
el emisor toca un botón cada vez. §2.2 declara no-objetivo permanente la
WhatsApp Business API y los recordatorios automáticos, así que "compartir por
WhatsApp" es necesariamente una liga que abre la aplicación *del emisor*.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from receipt.infra.mail import RecordingMailer
from receipt.models import Account, Client
from receipt.repositories import AccountScope
from receipt.services.charging import ChargeRequest, charge
from receipt.services.receipts import cancel_receipt
from receipt.services.sharing import public_url, share_text, whatsapp_url
from tests.conftest import csrf_token_of, document_text
from tests.factories import make_client

pytestmark = pytest.mark.db


def receipt_mails(mailer: RecordingMailer) -> list:
    """Los correos que llevan un comprobante.

    El buzón nunca está vacío: dar de alta la cuenta ya manda su correo de
    verificación. Lo que hay que mirar es si salió un recibo, y el recibo es el
    único mensaje que viaja con adjunto.
    """
    return [message for message in mailer.sent if message.attachments]


@pytest.fixture
async def account(signed_in: AsyncClient, db: AsyncSession) -> Account:
    account = await db.scalar(select(Account))
    assert account is not None
    return account


@pytest.fixture
async def scope(db: AsyncSession, account: Account) -> AccountScope:
    return AccountScope(db, account.id)


@pytest.fixture
async def customer(db: AsyncSession, account: Account) -> Client:
    return await make_client(
        db, account, full_name="Ana Ramírez", phone="5598765432", email="ana@example.mx"
    )


@pytest.fixture
async def cobro(scope: AccountScope, account: Account, customer: Client):
    return await charge(
        scope,
        account,
        ChargeRequest(
            client_id=customer.id,
            concept="Instalación de contactos",
            agreed_total_cents=230000,
            payment_amount_cents=180000,
        ),
    )


class TestTheShareMessage:
    async def test_it_carries_the_amount_the_folio_and_the_link(
        self, cobro, account: Account
    ) -> None:
        """El cliente lo lee en la lista de chats sin abrir nada, y una liga
        pelada parece un fraude."""
        text = share_text(cobro.receipt, issuer_name=account.issuer_name)

        assert "$1,800.00" in text
        assert "RB-0001" in text
        assert cobro.receipt.public_token in text
        assert account.issuer_name in text

    async def test_the_public_url_is_absolute(self, cobro) -> None:
        """Va en un mensaje de WhatsApp: una ruta relativa no es pulsable."""
        assert public_url(cobro.receipt).startswith("http")
        assert public_url(cobro.receipt).endswith(f"/r/{cobro.receipt.public_token}")


class TestWhatsApp:
    async def test_it_builds_a_wa_me_link_with_the_country_code(
        self, cobro, customer: Client, account: Account
    ) -> None:
        url = whatsapp_url(cobro.receipt, customer, issuer_name=account.issuer_name)

        assert url is not None
        assert url.startswith("https://wa.me/525598765432?text=")

    @pytest.mark.parametrize(
        "typed",
        ["5598765432", "55 9876 5432", "+52 55 9876 5432", "(55) 9876-5432", "525598765432"],
    )
    async def test_it_accepts_the_ways_people_actually_write_a_phone(
        self, db: AsyncSession, account: Account, cobro, typed: str
    ) -> None:
        client = await make_client(db, account, full_name="Luis Mendoza", phone=typed)
        url = whatsapp_url(cobro.receipt, client, issuer_name=account.issuer_name)

        assert url is not None
        assert url.startswith("https://wa.me/525598765432") or url.startswith(
            "https://wa.me/525511112222"
        ), url

    async def test_an_unusable_phone_yields_no_link(
        self, db: AsyncSession, account: Account, cobro
    ) -> None:
        """Mejor un botón que explica que un chat abierto con un desconocido."""
        client = await make_client(db, account, full_name="Luis Mendoza", phone="123")
        assert whatsapp_url(cobro.receipt, client, issuer_name=account.issuer_name) is None

    async def test_the_screen_explains_it_instead_of_offering_a_broken_button(
        self, signed_in: AsyncClient, db: AsyncSession, account: Account, scope: AccountScope
    ) -> None:
        client = await make_client(db, account, full_name="Luis Mendoza", phone="123")
        result = await charge(
            scope,
            account,
            ChargeRequest(client_id=client.id, concept="Cableado", payment_amount_cents=50000),
        )

        response = await signed_in.get(f"/recibos/{result.receipt.id}")

        assert "no se entiende como número de 10 dígitos" in document_text(response)


class TestSendingByEmail:
    async def test_it_sends_the_link_and_attaches_the_pdf(
        self, signed_in: AsyncClient, mailer: RecordingMailer, cobro
    ) -> None:
        response = await signed_in.post(
            f"/recibos/{cobro.receipt.id}/enviar",
            data={"csrf_token": csrf_token_of(signed_in)},
            follow_redirects=False,
        )

        assert response.status_code == 303
        message = mailer.last_to("ana@example.mx")
        assert message is not None
        assert cobro.receipt.public_token in message.body
        assert "RB-0001" in message.subject

        assert len(message.attachments) == 1
        pdf = message.attachments[0]
        assert pdf.filename == "recibo-RB-0001.pdf"
        assert pdf.content_type == "application/pdf"
        assert pdf.content.startswith(b"%PDF-")

    async def test_the_body_says_it_is_not_a_cfdi(
        self, signed_in: AsyncClient, mailer: RecordingMailer, cobro
    ) -> None:
        """§8.1: la leyenda va también en el correo, no solo en el documento."""
        await signed_in.post(
            f"/recibos/{cobro.receipt.id}/enviar",
            data={"csrf_token": csrf_token_of(signed_in)},
            follow_redirects=False,
        )

        message = mailer.last_to("ana@example.mx")
        assert message is not None
        assert "No es un CFDI" in message.body

    async def test_a_client_without_an_address_gets_a_readable_refusal(
        self,
        signed_in: AsyncClient,
        db: AsyncSession,
        account: Account,
        scope: AccountScope,
        mailer: RecordingMailer,
    ) -> None:
        client = await make_client(db, account, full_name="Luis Mendoza", phone="5511112222")
        result = await charge(
            scope,
            account,
            ChargeRequest(client_id=client.id, concept="Cableado", payment_amount_cents=50000),
        )

        response = await signed_in.post(
            f"/recibos/{result.receipt.id}/enviar",
            data={"csrf_token": csrf_token_of(signed_in)},
        )

        assert response.status_code == 400
        assert "no tiene correo registrado" in document_text(response)
        assert receipt_mails(mailer) == []

    async def test_a_cancelled_receipt_is_not_sent(
        self, signed_in: AsyncClient, scope: AccountScope, mailer: RecordingMailer, cobro
    ) -> None:
        """Mandar un comprobante anulado solo puede causar un malentendido."""
        await cancel_receipt(scope, cobro.receipt, reason="Monto equivocado")

        response = await signed_in.post(
            f"/recibos/{cobro.receipt.id}/enviar",
            data={"csrf_token": csrf_token_of(signed_in)},
        )

        assert response.status_code == 400
        assert receipt_mails(mailer) == []

    async def test_nothing_is_sent_without_the_emitter_asking(
        self, signed_in: AsyncClient, mailer: RecordingMailer, cobro
    ) -> None:
        """RN-32: el envío es siempre manual. Emitir no manda nada."""
        assert receipt_mails(mailer) == [], "emitir no dispara ningún envío"

        await signed_in.get(f"/recibos/{cobro.receipt.id}?listo=1")
        assert receipt_mails(mailer) == [], "abrir la pantalla de resultado, tampoco"

    async def test_it_requires_the_csrf_token(self, signed_in: AsyncClient, cobro) -> None:
        response = await signed_in.post(f"/recibos/{cobro.receipt.id}/enviar", data={})
        assert response.status_code == 403

    async def test_a_receipt_of_another_account_cannot_be_sent(
        self, signed_in: AsyncClient, db: AsyncSession, mailer: RecordingMailer
    ) -> None:
        """`AC-13`, en la ruta que el hito 4 agrega."""
        from tests.factories import make_account

        other = await make_account(db, person_name="Cuenta ajena")
        other_client = await make_client(
            db, other, full_name="Cliente Ajeno", email="ajeno@example.mx"
        )
        theirs = await charge(
            AccountScope(db, other.id),
            other,
            ChargeRequest(
                client_id=other_client.id, concept="Trabajo ajeno", payment_amount_cents=10000
            ),
        )

        response = await signed_in.post(
            f"/recibos/{theirs.receipt.id}/enviar",
            data={"csrf_token": csrf_token_of(signed_in)},
        )

        assert response.status_code == 404
        assert receipt_mails(mailer) == []
