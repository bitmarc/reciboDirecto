"""Base declarativa y utilidades compartidas por los modelos."""

from datetime import datetime
from enum import StrEnum

from sqlalchemy import BigInteger, DateTime, MetaData, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Nombres deterministas para índices y restricciones. Sin esto, Alembic genera
# nombres distintos en cada máquina y las migraciones dejan de ser reproducibles.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def pg_enum(enum_class: type[StrEnum], name: str) -> SAEnum:
    """Enum nativo de PostgreSQL que guarda los *valores*, no los nombres.

    Sin `values_callable`, SQLAlchemy persiste `FREE` en lugar de `free` y el
    esquema deja de coincidir con el PRD.
    """
    return SAEnum(
        enum_class,
        name=name,
        values_callable=lambda members: [member.value for member in members],
    )


def timestamp_column(**kwargs: object) -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), **kwargs)  # type: ignore[arg-type]


class PrimaryKeyMixin:
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class CreatedAtMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
