"""Pantalla de ajustes: perfil, firma y logo por la vía real (§7.5)."""

import io

import pytest
from httpx import AsyncClient
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from receipt.domain.enums import Plan
from receipt.models import Account
from tests.conftest import csrf_token_of, visible_text
from tests.integration.test_media_versioning import signature_of
from tests.unit.test_images import make_image

pytestmark = pytest.mark.db


async def only_account(db: AsyncSession) -> Account:
    return (await db.scalars(select(Account))).one()


async def save_signature(http: AsyncClient, color: tuple[int, int, int, int]):
    return await http.post(
        "/ajustes/firma",
        data={"csrf_token": csrf_token_of(http), "signature_data": signature_of(color)},
        follow_redirects=False,
    )


class TestProfile:
    async def test_the_page_shows_the_issuer_data(self, signed_in: AsyncClient) -> None:
        page = await signed_in.get("/ajustes")
        assert page.status_code == 200
        assert "Juan Pérez" in page.text
        assert "Ciudad de México" in page.text

    async def test_the_profile_can_be_edited(
        self, signed_in: AsyncClient, db: AsyncSession
    ) -> None:
        response = await signed_in.post(
            "/ajustes",
            data={
                "csrf_token": csrf_token_of(signed_in),
                "display_name": "Electricidad Pérez",
                "phone": "5599998888",
                "default_city": "Naucalpan",
                "occupation": "Instalaciones residenciales",
            },
            follow_redirects=False,
        )

        assert response.status_code == 303
        account = await only_account(db)
        assert account.display_name == "Electricidad Pérez"
        assert account.phone == "5599998888"
        assert account.default_city == "Naucalpan"

    async def test_emptying_a_required_field_is_refused(
        self, signed_in: AsyncClient, db: AsyncSession
    ) -> None:
        await signed_in.post(
            "/ajustes",
            data={
                "csrf_token": csrf_token_of(signed_in),
                "display_name": "  ",
                "phone": "5599998888",
                "default_city": "Naucalpan",
            },
            follow_redirects=False,
        )
        assert (await only_account(db)).display_name == "Juan Pérez"

    async def test_the_settings_alias_of_section_6_resolves(self, signed_in: AsyncClient) -> None:
        response = await signed_in.get("/settings", follow_redirects=False)
        assert response.status_code == 307
        assert response.headers["location"] == "/ajustes"


class TestSignatureThroughTheWeb:
    async def test_drawing_and_saving_a_signature(
        self, signed_in: AsyncClient, db: AsyncSession
    ) -> None:
        response = await save_signature(signed_in, (10, 0, 0, 255))

        assert response.status_code == 303
        assert (await only_account(db)).signature_blob_path is not None

    async def test_the_saved_signature_is_served_back(self, signed_in: AsyncClient) -> None:
        await save_signature(signed_in, (10, 0, 0, 255))

        image = await signed_in.get("/ajustes/firma.png")

        assert image.status_code == 200
        assert image.headers["content-type"] == "image/png"
        with Image.open(io.BytesIO(image.content)) as png:
            assert png.mode == "RGBA"

    async def test_the_image_route_is_not_cacheable_by_proxies(
        self, signed_in: AsyncClient
    ) -> None:
        """La ruta es estable mientras el blob cambia con cada recaptura, y una
        firma es dato personal (§10)."""
        await save_signature(signed_in, (10, 0, 0, 255))

        image = await signed_in.get("/ajustes/firma.png")

        assert "private" in image.headers["cache-control"]
        assert "no-cache" in image.headers["cache-control"]

    async def test_recapturing_through_the_web_versions_the_blob(
        self, signed_in: AsyncClient, db: AsyncSession
    ) -> None:
        await save_signature(signed_in, (10, 0, 0, 255))
        first = (await only_account(db)).signature_blob_path

        await save_signature(signed_in, (20, 0, 0, 255))
        second = (await only_account(db)).signature_blob_path

        assert first is not None and second is not None
        assert first != second

    async def test_garbage_is_refused_without_touching_the_account(
        self, signed_in: AsyncClient, db: AsyncSession
    ) -> None:
        response = await signed_in.post(
            "/ajustes/firma",
            data={
                "csrf_token": csrf_token_of(signed_in),
                "signature_data": "data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==",
            },
            follow_redirects=False,
        )

        assert response.status_code == 303  # vuelve a /ajustes con el aviso
        assert (await only_account(db)).signature_blob_path is None

    async def test_without_a_signature_the_route_is_a_404(self, signed_in: AsyncClient) -> None:
        assert (await signed_in.get("/ajustes/firma.png")).status_code == 404

    async def test_the_images_require_a_session(self, client: AsyncClient) -> None:
        """§10: todo acceso al blob pasa por la aplicación y con sesión."""
        response = await client.get("/ajustes/firma.png", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"].startswith("/entrar")


class TestLogoGate:
    async def test_a_free_account_sees_the_upsell_instead_of_the_field(
        self, signed_in: AsyncClient
    ) -> None:
        page = await signed_in.get("/ajustes")
        assert "plan de pago" in visible_text(page)
        assert 'name="logo"' not in page.text

    async def test_rn34_a_free_account_cannot_upload_one(
        self, signed_in: AsyncClient, db: AsyncSession
    ) -> None:
        response = await signed_in.post(
            "/ajustes/logo",
            data={"csrf_token": csrf_token_of(signed_in)},
            files={"logo": ("logo.png", make_image(), "image/png")},
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert (await only_account(db)).logo_blob_path is None

    async def test_a_paid_account_can_upload_and_see_it(
        self, signed_in: AsyncClient, db: AsyncSession
    ) -> None:
        account = await only_account(db)
        account.plan = Plan.PAID
        await db.flush()

        upload = await signed_in.post(
            "/ajustes/logo",
            data={"csrf_token": csrf_token_of(signed_in)},
            files={"logo": ("logo.jpg", make_image("JPEG", mode="RGB"), "image/jpeg")},
            follow_redirects=False,
        )

        assert upload.status_code == 303
        assert account.logo_blob_path is not None

        image = await signed_in.get("/ajustes/logo.png")
        assert image.status_code == 200
        with Image.open(io.BytesIO(image.content)) as png:
            assert png.format == "PNG", "el JPEG debe almacenarse convertido"
