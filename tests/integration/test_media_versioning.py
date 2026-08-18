"""Firma y logo: versionado de blobs (RN-20, RN-34, mitad de AC-02).

**Por qué esto importa.** `receipt.snapshot` guardará `signature_blob_path` y
`logo_blob_path`: rutas, no bytes. Si al recapturar la firma se sobrescribiera
el blob anterior, todos los recibos ya emitidos cambiarían de firma sin que se
tocara una fila de la base de datos, y `AC-02` fallaría por una vía que ninguna
prueba de base de datos detecta.

La otra mitad de `AC-02` —que el recibo emitido no cambie— llega en el hito 3.
Si esta falla, aquella es imposible.
"""

import io

import pytest
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession

from receipt.domain.enums import Plan
from receipt.infra.blob import BlobStore
from receipt.services.errors import BusinessRuleViolation, ValidationError
from receipt.services.media import remove_logo, store_logo, store_signature
from tests.factories import make_account
from tests.unit.test_images import as_data_uri, make_image

pytestmark = pytest.mark.db


def signature_of(color: tuple[int, int, int, int]) -> str:
    """Firma distinguible por su color, para poder comprobar qué bytes quedaron."""
    image = Image.new("RGBA", (400, 150), color)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return as_data_uri(buffer.getvalue())


async def first_pixel(blobs: BlobStore, path: str) -> tuple[int, int, int, int]:
    with Image.open(io.BytesIO(await blobs.download(path))) as image:
        return image.convert("RGBA").getpixel((0, 0))  # type: ignore[return-value]


class TestSignatureVersioning:
    async def test_recapturing_writes_a_new_path(self, db: AsyncSession, blobs: BlobStore) -> None:
        account = await make_account(db)

        first = await store_signature(db, blobs, account, data_uri=signature_of((10, 0, 0, 255)))
        second = await store_signature(db, blobs, account, data_uri=signature_of((20, 0, 0, 255)))

        assert first != second
        assert account.signature_blob_path == second

    async def test_the_previous_signature_survives_untouched(
        self, db: AsyncSession, blobs: BlobStore
    ) -> None:
        """El corazón del asunto: los recibos emitidos con la primera firma
        deben seguir viéndola exactamente igual."""
        account = await make_account(db)

        first = await store_signature(db, blobs, account, data_uri=signature_of((10, 0, 0, 255)))
        before = await blobs.download(first)

        await store_signature(db, blobs, account, data_uri=signature_of((20, 0, 0, 255)))

        assert await blobs.exists(first)
        assert await blobs.download(first) == before
        assert (await first_pixel(blobs, first))[0] == 10

    async def test_each_capture_is_readable_on_its_own(
        self, db: AsyncSession, blobs: BlobStore
    ) -> None:
        account = await make_account(db)

        paths = [
            await store_signature(db, blobs, account, data_uri=signature_of((n, 0, 0, 255)))
            for n in (10, 20, 30)
        ]

        assert len(set(paths)) == 3
        for expected, path in zip((10, 20, 30), paths, strict=True):
            assert (await first_pixel(blobs, path))[0] == expected

    async def test_two_accounts_never_share_a_path(
        self, db: AsyncSession, blobs: BlobStore
    ) -> None:
        account_a = await make_account(db, person_name="Cuenta A")
        account_b = await make_account(db, person_name="Cuenta B")

        path_a = await store_signature(db, blobs, account_a, data_uri=signature_of((10, 0, 0, 255)))
        path_b = await store_signature(db, blobs, account_b, data_uri=signature_of((10, 0, 0, 255)))

        assert path_a != path_b
        assert f"/{account_a.id}/" in path_a
        assert f"/{account_b.id}/" in path_b

    async def test_an_invalid_signature_leaves_the_account_untouched(
        self, db: AsyncSession, blobs: BlobStore
    ) -> None:
        account = await make_account(db)
        original = await store_signature(db, blobs, account, data_uri=signature_of((10, 0, 0, 255)))

        with pytest.raises(ValidationError):
            await store_signature(db, blobs, account, data_uri="data:text/html;base64,PGI+")

        assert account.signature_blob_path == original


class TestLogoPlanGate:
    async def test_rn34_a_free_account_cannot_upload_a_logo(
        self, db: AsyncSession, blobs: BlobStore
    ) -> None:
        account = await make_account(db)
        assert account.plan is Plan.FREE

        with pytest.raises(BusinessRuleViolation, match="RN-34"):
            await store_logo(db, blobs, account, raw=make_image())

        assert account.logo_blob_path is None

    async def test_a_paid_account_can(self, db: AsyncSession, blobs: BlobStore) -> None:
        account = await make_account(db)
        account.plan = Plan.PAID
        await db.flush()

        path = await store_logo(db, blobs, account, raw=make_image())

        assert account.logo_blob_path == path
        assert await blobs.exists(path)

    async def test_a_jpeg_logo_is_stored_as_png(self, db: AsyncSession, blobs: BlobStore) -> None:
        account = await make_account(db)
        account.plan = Plan.PAID
        await db.flush()

        path = await store_logo(db, blobs, account, raw=make_image("JPEG", mode="RGB"))

        with Image.open(io.BytesIO(await blobs.download(path))) as image:
            assert image.format == "PNG"

    async def test_removing_the_logo_keeps_the_blob(
        self, db: AsyncSession, blobs: BlobStore
    ) -> None:
        """RN-34: los recibos ya emitidos conservan el logo en su snapshot."""
        account = await make_account(db)
        account.plan = Plan.PAID
        await db.flush()
        path = await store_logo(db, blobs, account, raw=make_image())

        await remove_logo(db, account)

        assert account.logo_blob_path is None
        assert await blobs.exists(path)
