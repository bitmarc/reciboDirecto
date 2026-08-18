"""Aviso de privacidad — `/privacidad` (§10, art. 15 de la LFPDPPP).

Lo que se prueba no es la redacción, que es cosa de quien la revise legalmente,
sino que **el documento siga conteniendo lo que la ley exige** y que llegue a
donde tiene que llegar. Un aviso impecable al que no se puede entrar desde el
registro no sirve de nada.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from receipt.models import Account
from receipt.repositories import AccountScope
from receipt.services.charging import ChargeRequest, charge
from tests.conftest import document_text, visible_text
from tests.factories import make_client

pytestmark = pytest.mark.db


class TestSeLlegaAlAviso:
    async def test_it_opens_without_a_session(self, client: AsyncClient) -> None:
        """Es el requisito que hace útil al aviso.

        Quien todavía no se registra necesita leerlo **antes** de entregar sus
        datos, y quien pide que se los borren suele haber perdido el acceso. Un
        aviso detrás del inicio de sesión no cumple su función.
        """
        response = await client.get("/privacidad")

        assert response.status_code == 200
        assert "Aviso de privacidad" in document_text(response)

    async def test_it_opens_with_a_session_too(self, signed_in: AsyncClient) -> None:
        response = await signed_in.get("/privacidad")

        assert response.status_code == 200
        assert "Aviso de privacidad" in document_text(response)

    @pytest.mark.parametrize("ruta", ["/entrar", "/registro"])
    async def test_the_footer_links_to_it_where_data_is_handed_over(
        self, client: AsyncClient, ruta: str
    ) -> None:
        """Registro e inicio de sesión son las dos pantallas donde alguien
        decide entregar sus datos. Si la liga falta ahí, falta donde importa."""
        response = await client.get(ruta)

        assert 'href="/privacidad"' in visible_text(response)


class TestElContenidoMinimoDelArticulo15:
    """Los seis elementos que la ley exige. Si uno desaparece en un refactor de
    la plantilla, el aviso queda defectuoso sin que nada más lo note."""

    @pytest.fixture
    async def aviso(self, client: AsyncClient) -> str:
        return document_text(await client.get("/privacidad"))

    async def test_it_names_the_responsable_and_the_address(self, aviso: str) -> None:
        assert "Recibo Pruebas, S.A. de C.V." in aviso
        assert "Calle de Prueba 1, Ciudad de México" in aviso

    async def test_it_enumerates_the_data_it_processes(self, aviso: str) -> None:
        for dato in ("Nombre completo", "Teléfono", "Correo electrónico"):
            assert dato in aviso, f"el aviso no enumera «{dato}»"

    async def test_it_identifies_sensitive_data(self, aviso: str) -> None:
        """El art. 15 obliga a identificarlos. Decir que no hay es contenido
        requerido, no relleno: el titular no puede deducirlo del silencio."""
        assert "No recabamos datos personales sensibles" in aviso

    async def test_it_states_the_purposes(self, aviso: str) -> None:
        assert "Para qué los usamos" in aviso
        assert "No tenemos finalidades secundarias" in aviso

    async def test_it_explains_how_to_limit_use_and_disclosure(self, aviso: str) -> None:
        assert "limitar el uso o la divulgación" in aviso

    async def test_it_gives_a_working_channel_for_arco_rights(self, aviso: str) -> None:
        """De nada sirve enumerar los derechos sin una dirección que reciba."""
        assert "privacidad@example.mx" in aviso
        assert "veinte días" in aviso
        assert "quince días" in aviso

    async def test_it_says_how_changes_are_communicated(self, aviso: str) -> None:
        assert "Cambios a este aviso" in aviso
        assert "Vigente desde" in aviso


class TestLoPropioDeEsteProducto:
    """Lo que ninguna plantilla genérica traería, y que es justo lo que §10 pide."""

    @pytest.fixture
    async def aviso(self, client: AsyncClient) -> str:
        return document_text(await client.get("/privacidad"))

    async def test_it_separates_the_two_roles(self, aviso: str) -> None:
        """§10: el emisor es responsable de los datos de sus clientes y la
        plataforma solo encargada. Es lo que decide a quién le reclama el
        cliente del plomero."""
        assert "el responsable eres tú" in aviso
        assert "encargados" in aviso

    async def test_it_names_the_processors_and_the_countries(self, aviso: str) -> None:
        assert "Microsoft Azure" in aviso
        assert "West US 2" in aviso
        assert "Neon" in aviso
        assert "US West 2 (Oregón)" in aviso
        assert "fuera de México" in aviso

    async def test_it_admits_the_limit_of_rectification(self, aviso: str) -> None:
        """RN-10 choca con el derecho de rectificación y el aviso lo dice.

        Callarlo sería prometer una corrección que el producto no puede hacer.
        """
        assert "Un comprobante ya emitido no se modifica" in aviso

    async def test_it_states_the_retention_period(self, aviso: str) -> None:
        assert "90 días" in aviso

    async def test_it_explains_why_the_receipt_outlives_the_account(self, aviso: str) -> None:
        """RN-30 y RN-31: la liga es de la persona a la que se le entregó."""
        assert "Esa liga es de la persona a la que se la diste" in aviso

    async def test_it_names_the_current_authority_and_not_the_extinct_one(self, aviso: str) -> None:
        """El INAI se extinguió y sus funciones pasaron a la Secretaría
        Anticorrupción y Buen Gobierno.

        Esta prueba existe porque el error va a llegar pegando texto de una
        plantilla vieja: casi todas las que circulan siguen citando al INAI.
        """
        assert "Secretaría Anticorrupción y Buen Gobierno" in aviso
        assert "INAI" not in aviso


class TestLaPaginaDelClienteDelEmisor:
    """Quien abre `/r/{token}` ve su nombre y a veces su firma."""

    @pytest.fixture
    async def cobro(self, signed_in: AsyncClient, db: AsyncSession):
        account = await db.scalar(select(Account))
        assert account is not None
        customer = await make_client(
            db, account, full_name="Ana Ramírez", phone="5598765432", email="ana@example.mx"
        )
        return await charge(
            AccountScope(db, account.id),
            account,
            ChargeRequest(
                client_id=customer.id,
                concept="Instalación de contactos",
                agreed_total_cents=230000,
                payment_amount_cents=180000,
            ),
        )

    async def test_it_points_the_client_at_the_issuer_not_at_us(
        self, client: AsyncClient, cobro, db: AsyncSession
    ) -> None:
        """El responsable es el emisor, y se nombra desde el snapshot: el del
        comprobante que se está viendo, no el de la sesión de nadie."""
        account = await db.scalar(select(Account))
        assert account is not None

        response = await client.get(f"/r/{cobro.receipt.public_token}")
        texto = document_text(response)

        assert response.status_code == 200
        assert account.issuer_name in texto
        assert "puedes dirigirte para consultarlos o corregirlos" in texto

    async def test_it_links_to_the_notice(self, client: AsyncClient, cobro) -> None:
        response = await client.get(f"/r/{cobro.receipt.public_token}")

        assert 'href="/privacidad"' in visible_text(response)

    async def test_it_still_sets_no_cookies(self, client: AsyncClient, cobro) -> None:
        """§7.3: esa página no pone cookies ni carga nada de terceros.

        La línea de privacidad es texto y una liga interna, así que no debe
        haber cambiado nada de eso. Sin esta prueba, el día que alguien quiera
        «medir cuántos abren el aviso» la garantía se pierde sin ruido.
        """
        response = await client.get(f"/r/{cobro.receipt.public_token}")

        assert "set-cookie" not in {nombre.lower() for nombre in response.headers}
