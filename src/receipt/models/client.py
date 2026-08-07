"""Directorio de clientes del emisor (E2)."""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Text
from sqlalchemy.orm import Mapped, mapped_column

from receipt.models.base import Base, PrimaryKeyMixin, TimestampMixin


class Client(Base, PrimaryKeyMixin, TimestampMixin):
    __tablename__ = "client"
    __table_args__ = (
        # El buscador de la pantalla de cobro filtra siempre por cuenta y luego
        # por nombre o teléfono.
        Index("ix_client_account_id_full_name", "account_id", "full_name"),
        Index("ix_client_account_id_phone", "account_id", "phone"),
    )

    account_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("account.id", ondelete="CASCADE"), index=True
    )
    full_name: Mapped[str] = mapped_column(Text)
    """Obligatorio, mínimo nombre + un apellido. La validación vive en la capa
    de servicios; aquí solo se garantiza que no sea nulo."""

    phone: Mapped[str] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text)
    """Opcional. Su ausencia deshabilita el envío por correo (§7.2)."""

    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    """Oculta del selector sin borrar histórico. No hay borrado físico (§3.4)."""
