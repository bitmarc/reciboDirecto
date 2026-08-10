"""Liga pública, PDF y permanencia — `AC-10`, `AC-12` y `AC-14`.

Es la única superficie del producto para el cliente del emisor (§1.5). También
es la única ruta que consulta sin `AccountScope`, así que aquí se concentra lo
que hay que probar del acceso por token.
"""

import io

import pytest
from httpx import AsyncClient
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession

from receipt.domain.enums import SignerCapacity
from receipt.infra.blob import BlobStore
from receipt.models import Account, Client
from receipt.repositories import AccountScope
from receipt.services.charging import ChargeRequest, charge
from receipt.services.media import store_client_signature, store_signature
from receipt.services.receipts import cancel_receipt
from tests.conftest import visible_text
from tests.factories import make_account, make_client
from tests.unit.test_images import as_data_uri

pytestmark = pytest.mark.db


def a_signature(color: tuple[int, int, int, int] = (20, 24, 29, 255)) -> str:
    """Firma dibujable, del mismo tamaño que sale de un canvas real."""
    buffer = io.BytesIO()
    Image.new("RGBA", (400, 150), color).save(buffer, format="PNG")
    return as_data_uri(buffer.getvalue())


SIGNATURE_DATA_URI = a_signature()


@pytest.fixture
async def account(db: AsyncSession) -> Account:
    return await make_account(db)


@pytest.fixture
async def scope(db: AsyncSession, account: Account) -> AccountScope:
    return AccountScope(db, account.id)


@pytest.fixture
async def customer(db: AsyncSession, account: Account) -> Client:
    return await make_client(db, account, full_name="Ana Ramírez", phone="5598765432")


@pytest.fixture
async def cobro(scope: AccountScope, account: Account, customer: Client):
    """El caso de `AC-04`: trabajo de $2,300 con un abono de $1,800."""
    return await charge(
        scope,
        account,
        ChargeRequest(
            client_id=customer.id,
            concept="Instalación de contactos",
            location_alias="Casa Ramírez",
            agreed_total_cents=230000,
            payment_amount_cents=180000,
        ),
    )


class TestThePublicPageRendersFromTheSnapshot:
    async def test_it_opens_without_a_session(self, client: AsyncClient, cobro) -> None:
        """§1.5: el cliente no instala nada, no crea cuenta y no inicia sesión."""
        response = await client.get(f"/r/{cobro.receipt.public_token}")

        assert response.status_code == 200
        text = visible_text(response)
        assert "Ana Ramírez" in text
        assert "Instalación de contactos" in text
        assert "RB-0001" in text
        assert "$1,800.00" in text
        assert "$500.00" in text, "el saldo pendiente es la memoria del acuerdo (§1.2)"

    async def test_it_carries_the_legal_notice(self, client: AsyncClient, cobro) -> None:
        """§8.1: sin esta leyenda el comprobante se confunde con un CFDI."""
        response = await client.get(f"/r/{cobro.receipt.public_token}")
        assert "Comprobante interno sin efectos fiscales" in visible_text(response)

    async def test_it_tells_the_client_to_keep_a_copy(self, client: AsyncClient, cobro) -> None:
        """§7.3, textual."""
        response = await client.get(f"/r/{cobro.receipt.public_token}")
        assert "te recomendamos descargarlo" in visible_text(response)

    async def test_it_is_marked_noindex_in_the_page_and_in_the_header(
        self, client: AsyncClient, cobro
    ) -> None:
        """§7.3 pide el `<meta>`; la cabecera lo repite porque el PDF no tiene head."""
        response = await client.get(f"/r/{cobro.receipt.public_token}")

        assert 'content="noindex, nofollow"' in response.text
        assert response.headers["x-robots-tag"] == "noindex, nofollow"

    async def test_no_third_party_origin_is_referenced(self, client: AsyncClient, cobro) -> None:
        """§7.3 y §10: sin analíticas ni recursos de terceros en /r/{token}.

        Las imágenes van como `data:` URI, así que la página no debe pedirle
        nada a ningún host.
        """
        response = await client.get(f"/r/{cobro.receipt.public_token}")

        assert "http://" not in response.text.replace("http://test", "")
        assert "https://" not in response.text

    async def test_the_material_never_reaches_the_public_page(
        self, client: AsyncClient, scope: AccountScope, account: Account, customer: Client
    ) -> None:
        """RN-14 y `AC-06`, ahora en el documento que ve el cliente."""
        result = await charge(
            scope,
            account,
            ChargeRequest(
                client_id=customer.id,
                concept="Reparación de tablero",
                payment_amount_cents=100000,
                material_cents=30000,
            ),
        )

        response = await client.get(f"/r/{result.receipt.public_token}")

        text = visible_text(response)
        assert "$1,000.00" in text
        assert "$300.00" not in text
        assert "$700.00" not in text
        assert "mano de obra y material" in text, "§8.3: el concepto va agregado"


class TestAc12AnInvalidTokenLeaksNothing:
    async def test_an_unknown_token_is_a_404(self, client: AsyncClient) -> None:
        response = await client.get("/r/token-que-no-existe")
        assert response.status_code == 404

    async def test_a_malformed_token_answers_exactly_like_an_unknown_one(
        self, client: AsyncClient, cobro
    ) -> None:
        """Cualquier diferencia observable convierte la ruta en un oráculo."""
        unknown = await client.get("/r/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
        malformed = await client.get("/r/../../etc/passwd")

        assert unknown.status_code == malformed.status_code == 404

    async def test_the_404_does_not_reveal_any_receipt_data(
        self, client: AsyncClient, cobro
    ) -> None:
        response = await client.get("/r/token-que-no-existe")

        assert "Ana Ramírez" not in response.text
        assert "RB-0001" not in response.text

    async def test_a_valid_token_of_another_account_still_resolves(
        self, client: AsyncClient, db: AsyncSession, cobro
    ) -> None:
        """La liga pública es deliberadamente global: el token es la credencial.

        `AC-13` acota lo que se lee **con sesión**; aquí no hay sesión, y un
        recibo que solo abriera su propio emisor no serviría para nada.
        """
        other = await make_account(db, display_name="Otro Emisor")
        other_client = await make_client(db, other, full_name="Luis Mendoza")
        theirs = await charge(
            AccountScope(db, other.id),
            other,
            ChargeRequest(
                client_id=other_client.id, concept="Cableado", payment_amount_cents=50000
            ),
        )

        response = await client.get(f"/r/{theirs.receipt.public_token}")

        assert response.status_code == 200
        assert "Luis Mendoza" in visible_text(response)

    async def test_enumeration_is_rate_limited(self, client: AsyncClient) -> None:
        """§10: la liga se limita para impedir el barrido de tokens."""
        from receipt.config import get_settings

        limit = get_settings().public_receipt_max_requests
        for _ in range(limit):
            await client.get("/r/token-inexistente")

        blocked = await client.get("/r/token-inexistente")
        assert blocked.status_code == 429


class TestAc10ACancelledReceiptStillResolves:
    async def test_the_link_answers_200_with_the_seal_and_the_reason(
        self, client: AsyncClient, scope: AccountScope, cobro
    ) -> None:
        await cancel_receipt(scope, cobro.receipt, reason="Se registró con el monto equivocado")

        response = await client.get(f"/r/{cobro.receipt.public_token}")

        assert response.status_code == 200
        text = visible_text(response)
        assert "CANCELADO" in text
        assert "Se registró con el monto equivocado" in text

    async def test_the_content_is_still_there_under_the_seal(
        self, client: AsyncClient, scope: AccountScope, cobro
    ) -> None:
        """RN-22: cancelar nunca borra. El cliente que guardó la liga tiene que
        poder leer qué se anuló, no encontrarse un 404 que parezca un engaño."""
        await cancel_receipt(scope, cobro.receipt, reason="Cobro duplicado")

        response = await client.get(f"/r/{cobro.receipt.public_token}")

        text = visible_text(response)
        assert "Ana Ramírez" in text
        assert "RB-0001" in text

    async def test_the_pdf_of_a_cancelled_receipt_carries_the_seal(
        self, client: AsyncClient, db: AsyncSession, blobs: BlobStore, scope: AccountScope, cobro
    ) -> None:
        """Un PDF generado antes de cancelar no puede seguir circulando limpio.

        Es el escenario que convierte un comprobante anulado en prueba falsa.
        """
        before = await client.get(f"/r/{cobro.receipt.public_token}/pdf")
        assert before.status_code == 200
        assert cobro.receipt.pdf_blob_path is not None, "quedó cacheado"

        await cancel_receipt(scope, cobro.receipt, reason="Cobro duplicado")
        await db.commit()

        assert cobro.receipt.pdf_blob_path is None, "el PDF cacheado se soltó"

        after = await client.get(f"/r/{cobro.receipt.public_token}/pdf")
        assert after.status_code == 200
        assert after.content != before.content, "se regeneró con el sello"


class TestAc14ThePdfIsPublicAndTheBlobIsNot:
    async def test_the_pdf_downloads_without_a_session(self, client: AsyncClient, cobro) -> None:
        response = await client.get(f"/r/{cobro.receipt.public_token}/pdf")

        assert response.status_code == 200
        assert response.headers["content-type"] == "application/pdf"
        assert response.content.startswith(b"%PDF-")
        assert response.content.rstrip().endswith(b"%%EOF"), "el PDF quedó truncado"

    async def test_it_is_offered_with_a_readable_filename(self, client: AsyncClient, cobro) -> None:
        response = await client.get(f"/r/{cobro.receipt.public_token}/pdf")
        assert 'filename="recibo-RB-0001.pdf"' in response.headers["content-disposition"]

    async def test_the_blob_is_not_reachable_without_going_through_the_app(
        self, client: AsyncClient, blobs: BlobStore, cobro
    ) -> None:
        """§3.3: el contenedor es privado y todo acceso pasa por la aplicación.

        Se comprueba contra el almacenamiento real, no contra un doble: lo que
        hay que probar es justamente que el contenedor no quedó público.
        """
        import httpx

        await client.get(f"/r/{cobro.receipt.public_token}/pdf")
        path = cobro.receipt.pdf_blob_path
        assert path is not None

        direct = f"http://azurite:10000/devstoreaccount1/{blobs._container}/{path}"
        async with httpx.AsyncClient() as raw:
            response = await raw.get(direct)

        assert response.status_code in {401, 403, 404}, (
            f"el blob respondió {response.status_code}: el contenedor está expuesto"
        )

    async def test_the_second_download_is_served_from_the_cached_blob(
        self, client: AsyncClient, cobro
    ) -> None:
        first = await client.get(f"/r/{cobro.receipt.public_token}/pdf")
        stored = cobro.receipt.pdf_blob_path
        second = await client.get(f"/r/{cobro.receipt.public_token}/pdf")

        assert cobro.receipt.pdf_blob_path == stored, "no se regeneró"
        assert first.content == second.content

    async def test_the_pdf_of_an_unknown_token_is_a_404(self, client: AsyncClient) -> None:
        response = await client.get("/r/token-que-no-existe/pdf")
        assert response.status_code == 404


class TestTheSignatureTravelsIntoTheDocument:
    async def test_a_signed_receipt_shows_who_signed_and_in_what_capacity(
        self, client: AsyncClient, db: AsyncSession, blobs: BlobStore, cobro
    ) -> None:
        """§14.1: sin este dato se reproduce el "yo no fui" cuando firma un tercero."""
        await store_client_signature(
            db,
            blobs,
            cobro.receipt,
            data_uri=SIGNATURE_DATA_URI,
            signer_name="Marta Ramírez",
            capacity=SignerCapacity.FAMILY,
        )
        await db.commit()

        response = await client.get(f"/r/{cobro.receipt.public_token}")

        text = visible_text(response)
        assert "Marta Ramírez" in text
        assert "Familiar del titular" in text

    async def test_the_issuer_signature_is_stamped_on_every_receipt(
        self,
        client: AsyncClient,
        db: AsyncSession,
        blobs: BlobStore,
        account: Account,
        scope: AccountScope,
        customer: Client,
    ) -> None:
        """RN-20: se captura una vez y se estampa en todos, sin pedirla al emitir."""
        await store_signature(db, blobs, account, data_uri=SIGNATURE_DATA_URI)
        result = await charge(
            scope,
            account,
            ChargeRequest(
                client_id=customer.id, concept="Cambio de apagador", payment_amount_cents=45000
            ),
        )
        await db.commit()

        response = await client.get(f"/r/{result.receipt.public_token}")

        assert "data:image/png;base64," in response.text, "la firma va incrustada, no por URL"


class TestTheThreeDestinationsShareOneTemplate:
    """§3.2: una sola implementación sirve a página pública, PDF e impresión."""

    async def test_the_print_view_renders_the_same_document(
        self, client: AsyncClient, cobro
    ) -> None:
        public = await client.get(f"/r/{cobro.receipt.public_token}")
        printable = await client.get(f"/r/{cobro.receipt.public_token}/imprimir")

        assert printable.status_code == 200
        for fragment in ("RB-0001", "Ana Ramírez", "$1,800.00", "$500.00"):
            assert fragment in visible_text(public)
            assert fragment in visible_text(printable)

    async def test_the_print_view_drops_the_page_chrome(self, client: AsyncClient, cobro) -> None:
        printable = await client.get(f"/r/{cobro.receipt.public_token}/imprimir")
        assert "Descargar PDF" not in visible_text(printable)
