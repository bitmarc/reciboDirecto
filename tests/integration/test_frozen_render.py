"""`AC-02` y `AC-03` — el recibo emitido no sigue a las ediciones posteriores.

Estas dos son la razón de ser del snapshot y, con él, de todo el producto: si el
comprobante cambiara cuando el emisor edita su perfil, dejaría de ser prueba de
nada (§1.2, §4.3).

Se prueban **sobre el render**, no sobre el diccionario. El hito 2 ya comprobó
que el snapshot no cambia; lo que falta comprobar es que el documento que ve el
cliente se construye de verdad desde él y no desde las tablas vivas. La trampa
que esto atrapa es sutil: el snapshot guarda *rutas* de blob, así que un blob
sobrescrito cambiaría la imagen del recibo sin tocar una fila.
"""

import io
import re
from copy import deepcopy

import pytest
from httpx import AsyncClient
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession

from receipt.domain.enums import Plan
from receipt.infra.blob import BlobStore
from receipt.models import Account, Client
from receipt.repositories import AccountScope
from receipt.services.charging import ChargeRequest, charge
from receipt.services.clients import update_client
from receipt.services.media import store_logo, store_signature
from tests.conftest import document_text, visible_text
from tests.factories import make_account, make_client
from tests.unit.test_images import as_data_uri

pytestmark = pytest.mark.db


def an_image(color: tuple[int, int, int, int]) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGBA", (400, 150), color).save(buffer, format="PNG")
    return buffer.getvalue()


async def pixel_of(blobs: BlobStore, path: str) -> tuple[int, int, int, int]:
    with Image.open(io.BytesIO(await blobs.download(path))) as image:
        return image.convert("RGBA").getpixel((0, 0))  # type: ignore[return-value]


@pytest.fixture
async def account(db: AsyncSession) -> Account:
    return await make_account(
        db, person_name="Juan Pérez", phone="5512345678", occupation="Electricista"
    )


@pytest.fixture
async def scope(db: AsyncSession, account: Account) -> AccountScope:
    return AccountScope(db, account.id)


@pytest.fixture
async def customer(db: AsyncSession, account: Account) -> Client:
    return await make_client(db, account, full_name="Ana Ramírez", phone="5598765432")


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


class TestAc02TheIssuerCanChangeWithoutRewritingHistory:
    async def test_changing_the_phone_does_not_alter_an_earlier_receipt(
        self, client: AsyncClient, db: AsyncSession, account: Account, cobro
    ) -> None:
        account.phone = "5500000000"
        await db.commit()

        response = await client.get(f"/r/{cobro.receipt.public_token}")

        text = visible_text(response)
        assert "5512345678" in text, "el recibo conserva el teléfono con el que se emitió"
        assert "5500000000" not in text

    async def test_registering_a_business_name_does_not_alter_an_earlier_receipt(
        self, client: AsyncClient, db: AsyncSession, account: Account, cobro
    ) -> None:
        account.business_name = "Instalaciones Pérez S.A. de C.V."
        await db.commit()

        response = await client.get(f"/r/{cobro.receipt.public_token}")

        text = visible_text(response)
        assert "Juan Pérez" in text
        assert "Instalaciones Pérez" not in text

    async def test_changing_the_person_name_does_not_alter_an_earlier_receipt(
        self, client: AsyncClient, db: AsyncSession, account: Account, cobro
    ) -> None:
        account.person_name = "Juan Pérez Hernández"
        await db.commit()

        response = await client.get(f"/r/{cobro.receipt.public_token}")

        text = visible_text(response)
        assert "Juan Pérez Hernández" not in text

    async def test_a_version_one_snapshot_still_prints_its_signature_line(
        self, client: AsyncClient, db: AsyncSession, cobro
    ) -> None:
        """RN-10 en su forma más literal.

        Un recibo emitido con el formato anterior no trae `signer_name`, porque
        entonces la cuenta tenía un solo nombre. No se puede reescribir, así que
        el render tiene que saber leerlo: su firma sale de `display_name`, que es
        exactamente lo que ese recibo imprimía en esa línea.
        """
        antiguo = deepcopy(cobro.receipt.snapshot)
        antiguo["version"] = 1
        del antiguo["issuer"]["signer_name"]
        # Reasignar y no mutar en el sitio: SQLAlchemy no detecta el cambio
        # dentro de una columna JSONB.
        cobro.receipt.snapshot = antiguo
        await db.commit()

        text = document_text(await client.get(f"/r/{cobro.receipt.public_token}"))
        assert re.search(r"Juan Pérez\s+Firma de quien recibe el pago", text)

    async def test_a_later_receipt_does_show_the_new_data(
        self,
        client: AsyncClient,
        db: AsyncSession,
        scope: AccountScope,
        account: Account,
        customer: Client,
        cobro,
    ) -> None:
        """La otra mitad: congelar no puede significar quedarse obsoleto."""
        account.phone = "5500000000"
        await db.flush()

        later = await charge(
            scope,
            account,
            ChargeRequest(
                client_id=customer.id, concept="Otro trabajo", payment_amount_cents=10000
            ),
        )
        await db.commit()

        response = await client.get(f"/r/{later.receipt.public_token}")
        assert "5500000000" in visible_text(response)

    async def test_replacing_the_signature_does_not_restamp_earlier_receipts(
        self,
        client: AsyncClient,
        db: AsyncSession,
        blobs: BlobStore,
        scope: AccountScope,
        account: Account,
        customer: Client,
    ) -> None:
        """La vía que ninguna prueba de base de datos detecta.

        El snapshot guarda la **ruta** del blob. Si recapturar la firma pisara el
        blob anterior, el recibo ya emitido cambiaría de firma sin que se tocara
        una sola fila. Por eso cada captura escribe una ruta nueva.
        """
        original = an_image((10, 20, 30, 255))
        await store_signature(db, blobs, account, data_uri=as_data_uri(original))
        first = await charge(
            scope,
            account,
            ChargeRequest(
                client_id=customer.id,
                concept="Instalación de contactos",
                payment_amount_cents=50000,
            ),
        )
        frozen_path = first.receipt.snapshot["issuer"]["signature_blob_path"]

        await store_signature(db, blobs, account, data_uri=as_data_uri(an_image((200, 5, 5, 255))))
        await db.commit()

        assert account.signature_blob_path != frozen_path, "la firma nueva fue a otra ruta"
        assert await pixel_of(blobs, frozen_path) == (10, 20, 30, 255), (
            "el blob del recibo emitido sigue intacto"
        )

        response = await client.get(f"/r/{first.receipt.public_token}")
        assert response.status_code == 200

    async def test_rn34_a_logo_added_later_does_not_appear_on_earlier_receipts(
        self, client: AsyncClient, db: AsyncSession, blobs: BlobStore, account: Account, cobro
    ) -> None:
        account.plan = Plan.PAID
        await store_logo(db, blobs, account, raw=an_image((90, 90, 200, 255)))
        await db.commit()

        assert account.logo_blob_path is not None
        assert cobro.receipt.snapshot["issuer"]["logo_blob_path"] is None

        response = await client.get(f"/r/{cobro.receipt.public_token}")
        assert response.status_code == 200

    async def test_rn34_a_receipt_issued_with_a_logo_keeps_it_after_downgrading(
        self,
        client: AsyncClient,
        db: AsyncSession,
        blobs: BlobStore,
        scope: AccountScope,
        account: Account,
        customer: Client,
    ) -> None:
        """ "Los recibos ya emitidos conservan el logo", textual."""
        account.plan = Plan.PAID
        await store_logo(db, blobs, account, raw=an_image((90, 90, 200, 255)))

        with_logo = await charge(
            scope,
            account,
            ChargeRequest(client_id=customer.id, concept="Con logo", payment_amount_cents=10000),
        )
        assert with_logo.receipt.snapshot["issuer"]["logo_blob_path"] is not None

        account.plan = Plan.FREE
        await db.commit()

        response = await client.get(f"/r/{with_logo.receipt.public_token}")
        assert response.status_code == 200
        assert "data:image/png;base64," in response.text, "el logo sigue estampado"


class TestAc03RenamingAClientDoesNotAlterEarlierReceipts:
    async def test_the_public_page_keeps_the_name_of_the_moment(
        self, client: AsyncClient, db: AsyncSession, scope: AccountScope, customer: Client, cobro
    ) -> None:
        await update_client(
            scope, customer, full_name="Ana María Ramírez Torres", phone="5598765432"
        )
        await db.commit()

        response = await client.get(f"/r/{cobro.receipt.public_token}")

        text = visible_text(response)
        assert "Ana Ramírez" in text
        assert "Ana María Ramírez Torres" not in text

    async def test_the_pdf_keeps_it_too(
        self, client: AsyncClient, db: AsyncSession, scope: AccountScope, customer: Client, cobro
    ) -> None:
        """El PDF se genera bajo demanda, así que se renderiza **después** del
        cambio. Si leyera de las tablas vivas, aquí saldría el nombre nuevo."""
        await update_client(
            scope, customer, full_name="Ana María Ramírez Torres", phone="5598765432"
        )
        await db.commit()

        response = await client.get(f"/r/{cobro.receipt.public_token}/pdf")

        assert response.content.startswith(b"%PDF-")
        assert cobro.receipt.snapshot["client"]["full_name"] == "Ana Ramírez"

    async def test_archiving_a_client_does_not_break_their_receipts(
        self, client: AsyncClient, db: AsyncSession, scope: AccountScope, customer: Client, cobro
    ) -> None:
        """RN-30: la liga no caduca mientras la cuenta exista."""
        from receipt.services.clients import archive_client

        await archive_client(scope, customer)
        await db.commit()

        response = await client.get(f"/r/{cobro.receipt.public_token}")

        assert response.status_code == 200
        assert "Ana Ramírez" in visible_text(response)


class TestRn31TheLinkOutlivesTheSubscription:
    async def test_a_receipt_issued_on_the_paid_plan_still_opens_on_the_free_one(
        self, client: AsyncClient, db: AsyncSession, account: Account, cobro
    ) -> None:
        """Un comprobante no puede dejar de existir porque el emisor dejó de pagar."""
        account.plan = Plan.PAID
        await db.flush()
        account.plan = Plan.FREE
        await db.commit()

        page = await client.get(f"/r/{cobro.receipt.public_token}")
        pdf = await client.get(f"/r/{cobro.receipt.public_token}/pdf")

        assert page.status_code == 200
        assert pdf.status_code == 200
