"""Qué hace cada ruta cuando el proveedor de correo falla.

Hasta ahora ningún envío podía fallar: `ConsoleMailer` escribe al log y
`RecordingMailer` guarda en una lista, y ninguno lanza. Con Azure enchufado sí
puede, y con dominio gestionado —10 correos por hora en toda la suscripción—
falla a menudo. Las cuatro rutas que mandan correo deciden distinto, y cada
decisión se sostiene aquí.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from receipt.models import Account, Client, User
from receipt.repositories import AccountScope
from receipt.services.charging import ChargeRequest, charge
from tests.conftest import REGISTRATION, csrf_token_of, document_text
from tests.factories import make_client

pytestmark = pytest.mark.db


class TestElAltaDeCuenta:
    """El alta se completa aunque el correo de verificación no salga.

    La cuenta ya existe cuando se manda el correo y la verificación no bloquea
    el uso: tumbar un registro por un proveedor caído dejaría al usuario sin
    cuenta y sin explicación.
    """

    async def test_the_account_is_created_even_though_the_email_never_leaves(
        self, client: AsyncClient, mailer_caido, db: AsyncSession
    ) -> None:
        response = await client.post("/auth/register", data=REGISTRATION, follow_redirects=False)

        assert response.status_code == 303
        assert response.headers["location"] == "/"

        user = await db.scalar(select(User).where(User.email == REGISTRATION["email"]))
        assert user is not None, "el alta se revirtió por un correo que no salió"

    async def test_the_session_is_issued_anyway(self, client: AsyncClient, mailer_caido) -> None:
        """Sin cookie, el usuario acabaría en el login con una cuenta que ya existe
        y una contraseña que cree recién puesta."""
        response = await client.post("/auth/register", data=REGISTRATION, follow_redirects=False)

        assert "receipt_session" in response.cookies

    async def test_the_send_was_attempted(self, client: AsyncClient, mailer_caido) -> None:
        """Que no se rompa no puede significar que ni se intente."""
        await client.post("/auth/register", data=REGISTRATION, follow_redirects=False)

        assert len(mailer_caido.attempts) == 1


class TestLaRecuperacionDeContrasena:
    """Responde **lo mismo** falle o no. Es la garantía anti-enumeración.

    Un error de transporte solo puede aparecer cuando hubo intento de envío, o
    sea cuando la cuenta existe. Mostrarlo convertiría el formulario en el
    detector de cuentas que la respuesta única evita.
    """

    async def test_a_failed_send_answers_exactly_like_a_successful_one(
        self, client: AsyncClient, db: AsyncSession, app
    ) -> None:
        """Se comparan los dos cuerpos.

        Buscar la frase por separado no serviría: el día que alguien añada un
        «hubo un problema» al final, la frase seguiría estando y la prueba
        seguiría verde mientras la garantía se pierde.
        """
        await client.post("/auth/register", data=REGISTRATION, follow_redirects=False)

        bueno = await client.post("/auth/forgot-password", data={"email": REGISTRATION["email"]})

        from receipt.infra.mail import get_mailer
        from tests.conftest import FailingMailer

        roto = FailingMailer()
        app.dependency_overrides[get_mailer] = lambda: roto

        malo = await client.post("/auth/forgot-password", data={"email": REGISTRATION["email"]})

        assert malo.status_code == bueno.status_code == 200
        assert document_text(malo) == document_text(bueno)
        assert roto.attempts, "no se intentó el envío"

    async def test_an_unknown_address_also_answers_the_same(
        self, client: AsyncClient, mailer_caido
    ) -> None:
        """El tercer caso: correo que no existe. No hay intento, no hay fallo,
        y la respuesta tiene que seguir siendo indistinguible."""
        response = await client.post("/auth/forgot-password", data={"email": "nadie@example.mx"})

        assert response.status_code == 200
        assert "Si ese correo tiene una cuenta" in document_text(response)
        assert not mailer_caido.attempts


class TestElReenvioDeLaConfirmacion:
    """Aquí el error sí se dice: el usuario está autenticado y es su dirección."""

    async def test_it_says_that_the_email_could_not_be_sent(
        self, signed_in: AsyncClient, mailer_caido
    ) -> None:
        response = await signed_in.post(
            "/ajustes/verificar-correo",
            data={"csrf_token": csrf_token_of(signed_in)},
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert "error=" in response.headers["location"]
        assert "No%20pudimos%20enviar" in response.headers["location"]

    async def test_it_does_not_claim_the_link_was_sent(
        self, signed_in: AsyncClient, mailer_caido
    ) -> None:
        """El fallo silencioso sería peor que el error: el usuario esperaría."""
        response = await signed_in.post(
            "/ajustes/verificar-correo",
            data={"csrf_token": csrf_token_of(signed_in)},
            follow_redirects=False,
        )

        assert "aviso=" not in response.headers["location"]


class TestElEnvioDelRecibo:
    """El emisor tiene al cliente delante: el error se ve y dice qué hacer."""

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

    async def test_it_answers_502_and_not_400(
        self, signed_in: AsyncClient, mailer_caido, cobro
    ) -> None:
        """400 diría «corrige el dato» y aquí no hay dato que corregir."""
        response = await signed_in.post(
            f"/recibos/{cobro.receipt.id}/enviar",
            data={"csrf_token": csrf_token_of(signed_in)},
            follow_redirects=False,
        )

        assert response.status_code == 502

    async def test_it_points_at_the_channels_that_do_work(
        self, signed_in: AsyncClient, mailer_caido, cobro
    ) -> None:
        """WhatsApp y la liga no dependen del proveedor ni tienen cuota.

        Con dominio gestionado esta pantalla se va a ver de verdad, así que no
        basta con decir que falló: tiene que decir por dónde salir.
        """
        response = await signed_in.post(
            f"/recibos/{cobro.receipt.id}/enviar",
            data={"csrf_token": csrf_token_of(signed_in)},
            follow_redirects=False,
        )
        texto = document_text(response)

        assert "No pudimos enviar el correo" in texto
        assert "WhatsApp" in texto

    async def test_the_receipt_is_not_marked_as_sent(
        self, signed_in: AsyncClient, mailer_caido, cobro
    ) -> None:
        """La redirección de éxito lleva `?enviado=1`. Un fallo no puede acabar ahí."""
        response = await signed_in.post(
            f"/recibos/{cobro.receipt.id}/enviar",
            data={"csrf_token": csrf_token_of(signed_in)},
            follow_redirects=False,
        )

        assert "enviado=1" not in str(response.headers.get("location", ""))

    async def test_the_session_still_works_after_the_failure(
        self, signed_in: AsyncClient, mailer_caido, cobro
    ) -> None:
        """Sin `db.rollback()` en la rama de error.

        Añadirlo es la trampa: con `join_transaction_mode="create_savepoint"`
        tumba la transacción de la prueba entera, y el síntoma es un 404 en la
        petición siguiente en vez de un error de transacción. Ya pasó en el
        hito 5, en la ruta de cierre por incobrable.
        """
        await signed_in.post(
            f"/recibos/{cobro.receipt.id}/enviar",
            data={"csrf_token": csrf_token_of(signed_in)},
            follow_redirects=False,
        )

        siguiente = await signed_in.get(f"/recibos/{cobro.receipt.id}")
        assert siguiente.status_code == 200

    async def test_a_client_without_email_still_gets_the_400(
        self, signed_in: AsyncClient, mailer_caido, db: AsyncSession
    ) -> None:
        """El 502 no puede haberse tragado el error de dominio.

        Un cliente sin correo es un dato que el emisor sí puede corregir, y esa
        rama tiene que seguir contestando 400 con su mensaje.
        """
        account = await db.scalar(select(Account))
        assert account is not None
        sin_correo: Client = await make_client(
            db, account, full_name="Luis Torres", phone="5511112222", email=None
        )
        cobro = await charge(
            AccountScope(db, account.id),
            account,
            ChargeRequest(
                client_id=sin_correo.id,
                concept="Cambio de balastra",
                agreed_total_cents=50000,
                payment_amount_cents=50000,
            ),
        )

        response = await signed_in.post(
            f"/recibos/{cobro.receipt.id}/enviar",
            data={"csrf_token": csrf_token_of(signed_in)},
            follow_redirects=False,
        )

        assert response.status_code == 400
        assert "no tiene correo registrado" in document_text(response)
