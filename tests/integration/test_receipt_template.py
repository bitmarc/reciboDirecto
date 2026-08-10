"""La plantilla del comprobante (§8) y la captura de firma del cliente.

§3.2 exige **una sola implementación** para página pública, PDF e impresión. La
comprobación de que los tres destinos coinciden vive en `test_public_receipt`;
aquí se prueba el contenido del documento: que las dos plantillas dicen lo que
§8.2 y §8.3 mandan, en el orden que mandan.
"""

import io
import re

import pytest
from httpx import AsyncClient
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession

from receipt.domain.enums import ReceiptTemplate, SignerCapacity
from receipt.infra.blob import BlobStore
from receipt.models import Account, Client
from receipt.repositories import AccountScope
from receipt.services.charging import ChargeRequest, charge
from receipt.services.receipts import cancel_receipt
from tests.conftest import csrf_token_of, document_text, visible_text
from tests.factories import make_client
from tests.unit.test_images import as_data_uri

pytestmark = pytest.mark.db


def a_signature() -> str:
    buffer = io.BytesIO()
    Image.new("RGBA", (400, 150), (20, 24, 29, 255)).save(buffer, format="PNG")
    return as_data_uri(buffer.getvalue())


@pytest.fixture
async def account(signed_in: AsyncClient, db: AsyncSession) -> Account:
    from sqlalchemy import select

    account = await db.scalar(select(Account))
    assert account is not None
    return account


@pytest.fixture
async def scope(db: AsyncSession, account: Account) -> AccountScope:
    return AccountScope(db, account.id)


@pytest.fixture
async def customer(db: AsyncSession, account: Account) -> Client:
    return await make_client(db, account, full_name="Ana Ramírez", phone="5598765432")


async def public_text(client: AsyncClient, receipt) -> str:
    """El comprobante tal como se lee, sin marcado."""
    return document_text(await client.get(f"/r/{receipt.public_token}"))


class TestSection82SingleTemplate:
    """Pago único: una línea de monto más el monto en letra."""

    @pytest.fixture
    async def single(self, scope: AccountScope, account: Account, customer: Client):
        return await charge(
            scope,
            account,
            ChargeRequest(
                client_id=customer.id,
                concept="Cambio de apagador",
                payment_amount_cents=45000,
            ),
        )

    async def test_it_is_the_template_chosen_for_a_single_payment(self, single) -> None:
        assert single.receipt.template is ReceiptTemplate.SINGLE

    async def test_it_states_the_amount_and_spells_it_out(
        self, client: AsyncClient, single
    ) -> None:
        text = await public_text(client, single.receipt)

        assert "Recibí la cantidad de $450.00" in text
        assert "CUATROCIENTOS CINCUENTA PESOS 00/100 M.N." in text

    async def test_it_does_not_show_a_balance_table(self, client: AsyncClient, single) -> None:
        """Sin total acordado no hay saldo que mostrar (RN-15)."""
        text = await public_text(client, single.receipt)

        assert "Saldo pendiente" not in text
        assert "Total acordado" not in text


class TestSection83DetailedTemplate:
    """La tabla de montos, en el orden exacto que fija §8.3."""

    @pytest.fixture
    async def detailed(self, scope: AccountScope, account: Account, customer: Client):
        first = await charge(
            scope,
            account,
            ChargeRequest(
                client_id=customer.id,
                concept="Instalación de contactos",
                agreed_total_cents=230000,
                payment_amount_cents=180000,
            ),
        )
        return await charge(
            scope,
            account,
            ChargeRequest(
                client_id=customer.id,
                service_id=first.service.id,
                adjustment_amount_cents=10000,
                adjustment_reason="Cambio de 2 contactos en recámara",
                payment_amount_cents=60000,
            ),
        )

    async def test_every_line_of_the_table_is_present(self, client: AsyncClient, detailed) -> None:
        text = await public_text(client, detailed.receipt)

        assert "Total acordado" in text
        assert "Trabajo adicional" in text
        assert "Cambio de 2 contactos en recámara" in text
        assert "Total vigente" in text
        assert "Pagos anteriores" in text
        assert "Pago recibido hoy" in text
        assert "Pagado acumulado" in text
        assert "Saldo pendiente" in text

    async def test_the_lines_appear_in_the_documented_order(
        self, client: AsyncClient, detailed
    ) -> None:
        """El orden no es estético: es lo que hace legible el recibo detallado."""
        text = await public_text(client, detailed.receipt)

        expected = [
            "Total acordado",
            "Trabajo adicional",
            "Total vigente",
            "Pagos anteriores",
            "Pago recibido hoy",
            "Pagado acumulado",
            "Saldo pendiente",
        ]
        positions = [text.index(line) for line in expected]
        assert positions == sorted(positions), f"orden inesperado: {expected}"

    async def test_the_amounts_are_the_ones_of_ac05(self, client: AsyncClient, detailed) -> None:
        text = await public_text(client, detailed.receipt)

        assert "$2,300.00" in text, "total acordado"
        assert "$100.00" in text, "trabajo adicional"
        assert "$2,400.00" in text, "total vigente y pagado acumulado"
        assert "$1,800.00" in text, "pagos anteriores"
        assert "$600.00" in text, "pago recibido hoy"

    async def test_a_zero_balance_is_labelled_as_settled(
        self, client: AsyncClient, detailed
    ) -> None:
        """§8.3: `$0.00` con la etiqueta "Liquidado" cuando corresponde."""
        text = await public_text(client, detailed.receipt)
        assert re.search(r"\$0\.00\s*·?\s*Liquidado", text)

    async def test_a_pending_balance_is_shown_as_a_number(
        self, client: AsyncClient, scope: AccountScope, account: Account, customer: Client
    ) -> None:
        partial = await charge(
            scope,
            account,
            ChargeRequest(
                client_id=customer.id,
                concept="Instalación",
                agreed_total_cents=230000,
                payment_amount_cents=180000,
            ),
        )
        text = await public_text(client, partial.receipt)

        assert "$500.00" in text
        assert "Liquidado" not in text

    async def test_the_payment_method_is_named_in_spanish(
        self, client: AsyncClient, detailed
    ) -> None:
        text = await public_text(client, detailed.receipt)
        assert "Pago recibido hoy (efectivo)" in text


class TestSection81CommonStructure:
    @pytest.fixture
    async def cobro(self, scope: AccountScope, account: Account, customer: Client):
        return await charge(
            scope,
            account,
            ChargeRequest(
                client_id=customer.id,
                concept="Instalación de contactos",
                location_alias="Casa Ramírez",
                location_address="Av. Coyoacán 123, Narvarte",
                agreed_total_cents=230000,
                payment_amount_cents=180000,
            ),
        )

    async def test_the_header_carries_issuer_city_and_date(
        self, client: AsyncClient, cobro
    ) -> None:
        text = await public_text(client, cobro.receipt)

        assert "Juan Pérez" in text
        assert "Electricista" in text
        assert "5512345678" in text
        assert "Ciudad de México," in text

    async def test_it_identifies_the_receipt_and_the_job(self, client: AsyncClient, cobro) -> None:
        text = await public_text(client, cobro.receipt)

        assert "RB-0001" in text
        assert "SRV-0001" in text

    async def test_the_body_names_who_paid_and_for_what(self, client: AsyncClient, cobro) -> None:
        text = await public_text(client, cobro.receipt)

        assert "Recibí de Ana Ramírez" in text
        assert "Instalación de contactos" in text

    async def test_it_shows_where_the_job_was_done(self, client: AsyncClient, cobro) -> None:
        text = await public_text(client, cobro.receipt)

        assert "Casa Ramírez" in text
        assert "Av. Coyoacán 123, Narvarte" in text


class TestCapturingTheClientSignature:
    @pytest.fixture
    async def cobro(self, scope: AccountScope, account: Account, customer: Client):
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

    async def test_rn21_a_receipt_is_valid_without_a_client_signature(
        self, client: AsyncClient, cobro
    ) -> None:
        """Es opcional y la decide el emisor recibo por recibo."""
        response = await client.get(f"/r/{cobro.receipt.public_token}")

        assert response.status_code == 200
        assert cobro.receipt.client_signature_blob_path is None

    async def test_the_holder_signs_without_typing_a_name(
        self, signed_in: AsyncClient, cobro
    ) -> None:
        """§1.4: un campo más frente a una puerta es un campo de más."""
        response = await signed_in.post(
            f"/recibos/{cobro.receipt.id}/firma",
            data={
                "signature_data": a_signature(),
                "signer_name": "",
                "signer_capacity": SignerCapacity.HOLDER.value,
                "csrf_token": csrf_token_of(signed_in),
            },
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert cobro.receipt.client_signature_signer_name == "Ana Ramírez"
        assert cobro.receipt.client_signature_signer_capacity is SignerCapacity.HOLDER
        assert cobro.receipt.client_signature_signed_at is not None

    async def test_a_third_party_must_be_named(self, signed_in: AsyncClient, cobro) -> None:
        """§14.1: sin el nombre se reproduce el "yo no fui"."""
        response = await signed_in.post(
            f"/recibos/{cobro.receipt.id}/firma",
            data={
                "signature_data": a_signature(),
                "signer_name": "  ",
                "signer_capacity": SignerCapacity.MANAGER.value,
                "csrf_token": csrf_token_of(signed_in),
            },
        )

        assert response.status_code == 400
        assert "nombre de quien está firmando" in visible_text(response)
        assert cobro.receipt.client_signature_blob_path is None

    async def test_signing_invalidates_the_cached_pdf(
        self, signed_in: AsyncClient, client: AsyncClient, cobro
    ) -> None:
        """El PDF ya generado no lleva la firma; la próxima descarga regenera."""
        await client.get(f"/r/{cobro.receipt.public_token}/pdf")
        assert cobro.receipt.pdf_blob_path is not None

        await signed_in.post(
            f"/recibos/{cobro.receipt.id}/firma",
            data={
                "signature_data": a_signature(),
                "signer_capacity": SignerCapacity.HOLDER.value,
                "csrf_token": csrf_token_of(signed_in),
            },
            follow_redirects=False,
        )

        assert cobro.receipt.pdf_blob_path is None

    async def test_resigning_writes_a_new_blob(self, signed_in: AsyncClient, cobro) -> None:
        """Una captura sale mal a menudo; la anterior no se sobrescribe."""
        payload = {
            "signature_data": a_signature(),
            "signer_capacity": SignerCapacity.HOLDER.value,
            "csrf_token": csrf_token_of(signed_in),
        }
        await signed_in.post(
            f"/recibos/{cobro.receipt.id}/firma", data=payload, follow_redirects=False
        )
        first = cobro.receipt.client_signature_blob_path

        await signed_in.post(
            f"/recibos/{cobro.receipt.id}/firma", data=payload, follow_redirects=False
        )

        assert cobro.receipt.client_signature_blob_path != first

    async def test_a_cancelled_receipt_cannot_be_signed(
        self, signed_in: AsyncClient, scope: AccountScope, cobro
    ) -> None:
        await cancel_receipt(scope, cobro.receipt, reason="Monto equivocado")

        response = await signed_in.post(
            f"/recibos/{cobro.receipt.id}/firma",
            data={
                "signature_data": a_signature(),
                "signer_capacity": SignerCapacity.HOLDER.value,
                "csrf_token": csrf_token_of(signed_in),
            },
        )

        assert response.status_code == 400
        assert cobro.receipt.client_signature_blob_path is None

    async def test_it_requires_the_csrf_token(self, signed_in: AsyncClient, cobro) -> None:
        response = await signed_in.post(
            f"/recibos/{cobro.receipt.id}/firma",
            data={"signature_data": a_signature()},
        )
        assert response.status_code == 403


class TestTheDocumentFitsOnOnePage:
    """Un recibo de dos hojas es un defecto real que ninguna prueba de texto ve.

    La segunda página llevaría solo el pie, y el cliente que lo imprime en la
    puerta de su casa gastaría el doble de papel para nada. Se cuenta con el
    propio WeasyPrint, que es quien decide dónde cae el salto.
    """

    async def _pages(self, blobs: BlobStore, receipt) -> int:
        from weasyprint import HTML

        from receipt.services.documents import invalidate_pdf  # noqa: F401
        from receipt.services.rendering import build_document
        from receipt.web.templating import render_receipt_pdf_html

        html = render_receipt_pdf_html(await build_document(blobs, receipt))
        return len(HTML(string=html).render().pages)

    async def test_a_detailed_receipt_with_adjustments_fits(
        self, blobs: BlobStore, scope: AccountScope, account: Account, customer: Client
    ) -> None:
        first = await charge(
            scope,
            account,
            ChargeRequest(
                client_id=customer.id,
                concept="Instalación de contactos en toda la casa",
                location_alias="Casa Ramírez",
                location_address="Av. Coyoacán 123, Col. Narvarte Poniente, Benito Juárez",
                agreed_total_cents=230000,
                payment_amount_cents=180000,
            ),
        )
        for reason in (
            "Cambio de 2 contactos en recámara",
            "Se agregó una salida para el boiler",
            "Reposición de pastilla termomagnética",
        ):
            await charge(
                scope,
                account,
                ChargeRequest(
                    client_id=customer.id,
                    service_id=first.service.id,
                    adjustment_amount_cents=10000,
                    adjustment_reason=reason,
                    payment_amount_cents=1000,
                ),
            )

        last = await charge(
            scope,
            account,
            ChargeRequest(
                client_id=customer.id, service_id=first.service.id, payment_amount_cents=1000
            ),
        )

        assert await self._pages(blobs, last.receipt) == 1

    async def test_a_single_receipt_fits(
        self, blobs: BlobStore, scope: AccountScope, account: Account, customer: Client
    ) -> None:
        result = await charge(
            scope,
            account,
            ChargeRequest(
                client_id=customer.id, concept="Cambio de apagador", payment_amount_cents=45000
            ),
        )
        assert await self._pages(blobs, result.receipt) == 1


class TestTheIssuerSeesTheRealDocument:
    """§3.2: la ficha del emisor no puede ser una segunda plantilla."""

    @pytest.fixture
    async def cobro(self, scope: AccountScope, account: Account, customer: Client):
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

    async def test_the_detail_page_shows_the_same_document_as_the_public_page(
        self, signed_in: AsyncClient, client: AsyncClient, cobro
    ) -> None:
        private = document_text(await signed_in.get(f"/recibos/{cobro.receipt.id}"))
        public = document_text(await client.get(f"/r/{cobro.receipt.public_token}"))

        for fragment in (
            "Recibí de Ana Ramírez",
            "RB-0001",
            "$2,300.00",
            "$1,800.00",
            "$500.00",
            "Comprobante interno sin efectos fiscales",
        ):
            assert fragment in private
            assert fragment in public

    async def test_it_offers_the_public_link_and_the_pdf(
        self, signed_in: AsyncClient, cobro
    ) -> None:
        response = await signed_in.get(f"/recibos/{cobro.receipt.id}")

        assert f"/r/{cobro.receipt.public_token}" in response.text
        assert f"/recibos/{cobro.receipt.id}/pdf" in response.text

    async def test_the_authenticated_download_returns_a_pdf(
        self, signed_in: AsyncClient, cobro
    ) -> None:
        response = await signed_in.get(f"/recibos/{cobro.receipt.id}/pdf")

        assert response.status_code == 200
        assert response.content.startswith(b"%PDF-")

    async def test_a_receipt_of_another_account_has_no_downloadable_pdf(
        self, signed_in: AsyncClient, db: AsyncSession, blobs: BlobStore
    ) -> None:
        """`AC-13`, en la ruta que el hito 3 agrega."""
        from tests.factories import make_account

        other = await make_account(db, display_name="Cuenta ajena")
        other_client = await make_client(db, other, full_name="Cliente Ajeno")
        theirs = await charge(
            AccountScope(db, other.id),
            other,
            ChargeRequest(
                client_id=other_client.id, concept="Trabajo ajeno", payment_amount_cents=10000
            ),
        )

        response = await signed_in.get(f"/recibos/{theirs.receipt.id}/pdf")
        assert response.status_code == 404
