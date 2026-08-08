"""Motor asíncrono, sesiones y dependencia de FastAPI."""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from receipt.config import get_settings

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(
            settings.database_url,
            # El volcado de SQL se pide aparte de `debug`: encendido por omisión
            # ahoga el log y deja ilegible, por ejemplo, la liga que imprime el
            # adaptador de correo de consola.
            echo=settings.sql_echo,
            pool_pre_ping=True,
            # El tier Burstable B1ms de Azure PostgreSQL tiene pocas conexiones
            # disponibles (§13); un pool modesto evita agotarlas.
            pool_size=5,
            max_overflow=5,
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(get_engine(), expire_on_commit=False, autoflush=False)
    return _session_factory


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """Dependencia de FastAPI: una sesión por petición.

    No hace commit por su cuenta. El dueño de la transacción es la capa de
    servicios, que es donde se sabe qué constituye una unidad de trabajo — la
    emisión de un recibo, por ejemplo, es una sola transacción de principio a
    fin (§6).
    """
    async with get_session_factory()() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def dispose_engine() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None
