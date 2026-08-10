"""Configuración de la suite.

Las pruebas corren contra PostgreSQL real, no sobre un motor embebido. No es una
preferencia: AC-01 exige que la inmutabilidad falle también por SQL directo, y
eso lo sostiene un trigger que solo existe en PostgreSQL. Los blobs van contra
Azurite real por la misma razón: lo que hay que probar del hito 1 es que la
subida sin sobrescritura funciona, y un doble en memoria probaría el doble.

Dos decisiones que evitan el fallo clásico de "Future attached to a different
loop":

- El esquema se construye **una vez por sesión** desde un fixture síncrono. Las
  migraciones abren y cierran su propio bucle de eventos, así que no comparten
  nada con las pruebas.
- El motor asíncrono se crea **por prueba**, ligado al bucle de esa prueba, con
  `NullPool` para que no queden conexiones vivas entre bucles distintos.

Aislamiento: cada prueba corre dentro de una transacción que se revierte al
terminar. Es más rápido que recrear el esquema y garantiza que ninguna prueba
vea los datos de otra.
"""

import os
import re
from collections.abc import AsyncIterator, Iterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

# --------------------------------------------------------------------------
# El entorno se fija ANTES de importar la aplicación: `receipt.config` se lee al
# construir los ajustes, y `web.dependencies` arma el limitador de intentos en
# tiempo de importación. Invertir el orden dejaría valores de producción dentro
# de la suite.
# --------------------------------------------------------------------------

os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("SESSION_SECRET", "secreto-solo-para-pruebas")
os.environ.setdefault(
    "AZURE_STORAGE_CONNECTION_STRING",
    "DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;"
    "AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;"
    "BlobEndpoint=http://azurite:10000/devstoreaccount1;",
)

TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://receipt:receipt@db:5432/receipt_test",
)
os.environ["DATABASE_URL"] = TEST_DATABASE_URL

# Contenedor aparte, para que la suite no ensucie los blobs de desarrollo.
os.environ["BLOB_CONTAINER"] = "receipts-test"
os.environ["MAIL_BACKEND"] = "memory"

from receipt.db import get_db_session  # noqa: E402
from receipt.infra.blob import BlobStore, get_blob_store, reset_blob_store  # noqa: E402
from receipt.infra.mail import RecordingMailer, get_mailer, set_mailer  # noqa: E402
from receipt.main import create_app  # noqa: E402
from receipt.security.rate_limit import clear_all  # noqa: E402

# `web.dependencies` construye los limitadores en tiempo de importación y los
# deja registrados, que es lo que `clear_all()` necesita encontrar. `create_app`
# ya lo importa, pero dejarlo explícito evita que un refactor lo desconecte en
# silencio y la suite se vuelva intermitente.
from receipt.web import dependencies as _dependencies  # noqa: E402, F401

# --------------------------------------------------------------------------
# Base de datos
# --------------------------------------------------------------------------


@pytest.fixture(scope="session")
def migrated_schema() -> Iterator[None]:
    """Esquema construido aplicando las migraciones, no `metadata.create_all`.

    Así la suite prueba el esquema que realmente se despliega, con sus triggers
    y sus restricciones. Un `create_all` no crearía el trigger de RN-11 y AC-01
    pasaría en verde sin proteger nada.
    """
    from alembic import command
    from alembic.config import Config

    os.environ["ALEMBIC_DATABASE_URL"] = TEST_DATABASE_URL
    config = Config("alembic.ini")

    command.downgrade(config, "base")
    command.upgrade(config, "head")
    yield


@pytest.fixture
async def engine(migrated_schema: None) -> AsyncIterator[AsyncEngine]:
    test_engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    try:
        yield test_engine
    finally:
        await test_engine.dispose()


@pytest.fixture
async def connection(engine: AsyncEngine) -> AsyncIterator[AsyncConnection]:
    """Conexión envuelta en una transacción que siempre se revierte."""
    async with engine.connect() as conn:
        transaction = await conn.begin()
        try:
            yield conn
        finally:
            await transaction.rollback()


@pytest.fixture
async def db(connection: AsyncConnection) -> AsyncIterator[AsyncSession]:
    """Sesión atada a la transacción de la prueba.

    `join_transaction_mode="create_savepoint"` deja que el código bajo prueba
    haga `commit()` de verdad —lo que hacen los servicios— sin que nada escape
    al rollback final.
    """
    async with AsyncSession(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    ) as session:
        yield session


# --------------------------------------------------------------------------
# Infraestructura y aplicación
# --------------------------------------------------------------------------


@pytest.fixture
def blobs() -> Iterator[BlobStore]:
    reset_blob_store()
    yield get_blob_store()
    reset_blob_store()


@pytest.fixture
def mailer() -> Iterator[RecordingMailer]:
    """Buzón en memoria: deja leer la liga que se le habría enviado al usuario."""
    recorder = RecordingMailer()
    set_mailer(recorder)
    yield recorder
    set_mailer(None)


@pytest.fixture(autouse=True)
def reset_rate_limiter() -> Iterator[None]:
    """Los limitadores son estado global del proceso.

    Sin limpiarlos, unas pruebas de login harían fallar a las siguientes por
    haber agotado el cupo. Se limpian **todos** por registro y no uno por uno:
    el limitador de la liga pública se agregó en el hito 3, y descubrir semanas
    después que la suite se volvió intermitente por no haberlo añadido aquí es
    un fallo caro de diagnosticar.
    """
    clear_all()
    yield
    clear_all()


@pytest.fixture
async def client(
    db: AsyncSession, blobs: BlobStore, mailer: RecordingMailer
) -> AsyncIterator[AsyncClient]:
    """Cliente HTTP sobre la app, compartiendo la transacción de la prueba."""
    app = create_app()
    app.dependency_overrides[get_db_session] = lambda: db
    app.dependency_overrides[get_blob_store] = lambda: blobs
    app.dependency_overrides[get_mailer] = lambda: mailer
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http
    app.dependency_overrides.clear()


REGISTRATION = {
    "email": "electricista@example.mx",
    "password": "contrasena-larga",
    "display_name": "Juan Pérez",
    "phone": "5512345678",
    "default_city": "Ciudad de México",
    "occupation": "Electricista",
}


async def register_through_app(http: AsyncClient, **overrides: str) -> None:
    """Da de alta una cuenta por la vía real y deja la sesión iniciada."""
    response = await http.post(
        "/auth/register", data=REGISTRATION | overrides, follow_redirects=False
    )
    assert response.status_code == 303, response.text


@pytest.fixture
async def signed_in(client: AsyncClient) -> AsyncClient:
    await register_through_app(client)
    return client


def visible_text(response: object) -> str:
    """HTML con los espacios normalizados.

    El ajuste de línea de una plantilla parte frases a mitad, así que buscar una
    subcadena en el HTML crudo falla por motivos que no tienen que ver con lo que
    se está probando.
    """
    return " ".join(response.text.split())  # type: ignore[attr-defined]


_MARKUP = re.compile(r"<[^>]+>")
_NON_TEXT = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE)


def document_text(response: object) -> str:
    """Lo que una persona lee en la página: sin etiquetas, sin CSS, sin scripts.

    `visible_text` conserva el marcado, así que una frase partida por una
    etiqueta —«Recibí de <span>Ana Ramírez</span>»— no aparece como subcadena
    aunque en pantalla se lea entera. Para probar el copy del comprobante hace
    falta lo que se lee, no lo que se sirve.
    """
    raw: str = response.text  # type: ignore[attr-defined]
    return " ".join(_MARKUP.sub(" ", _NON_TEXT.sub(" ", raw)).split())


def csrf_token_of(http: AsyncClient) -> str:
    """Token CSRF de la sesión activa, para los POST de las pruebas."""
    from receipt.security.sessions import SessionData, _serializer

    raw = http.cookies.get("receipt_session")
    assert raw, "no hay sesión iniciada"
    return SessionData.from_payload(_serializer().loads(raw)).csrf_token
