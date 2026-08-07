"""Configuración de la suite.

Las pruebas corren contra PostgreSQL real, no sobre un motor embebido. No es una
preferencia: AC-01 exige que la inmutabilidad falle también por SQL directo, y
eso lo sostiene un trigger que solo existe en PostgreSQL.

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
from collections.abc import AsyncIterator, Iterator

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

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
