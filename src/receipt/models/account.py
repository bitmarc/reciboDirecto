"""Cuenta del emisor, usuario y contadores por cuenta.

§4.1: todas las entidades cuelgan de `Account`, no de `User`. En v1 hay
exactamente un usuario por cuenta. Cuando llegue el multiusuario se agregan
usuarios sin migrar datos. **No se implementa tabla de roles ahora** (§2.2).
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from receipt.domain.enums import AuthTokenPurpose, CounterKind, Plan
from receipt.models.base import (
    Base,
    CreatedAtMixin,
    PrimaryKeyMixin,
    TimestampMixin,
    pg_enum,
)


class Account(Base, PrimaryKeyMixin, TimestampMixin):
    __tablename__ = "account"

    display_name: Mapped[str] = mapped_column(Text)
    """Nombre de la persona o razón social. Es lo que aparece como emisor."""

    phone: Mapped[str] = mapped_column(Text)
    occupation: Mapped[str | None] = mapped_column(Text)
    logo_blob_path: Mapped[str | None] = mapped_column(Text)
    """Solo utilizable en plan de pago (RN-34)."""

    signature_blob_path: Mapped[str | None] = mapped_column(Text)
    """Firma del emisor, capturada una sola vez (RN-20)."""

    default_city: Mapped[str] = mapped_column(Text)

    plan: Mapped[Plan] = mapped_column(
        pg_enum(Plan, "plan"), default=Plan.FREE, server_default=Plan.FREE.value
    )
    plan_valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    users: Mapped[list["User"]] = relationship(back_populates="account")


class User(Base, PrimaryKeyMixin, CreatedAtMixin):
    # `user` es palabra reservada en PostgreSQL y obligaría a entrecomillarla en
    # cada trigger y consulta directa. El nombre lógico del PRD se conserva en la
    # clase; solo cambia el nombre físico de la tabla.
    __tablename__ = "app_user"

    account_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("account.id", ondelete="CASCADE"), index=True
    )
    email: Mapped[str] = mapped_column(Text, unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    account: Mapped[Account] = relationship(back_populates="users")


class AccountCounter(Base):
    """Contadores secuenciales **por cuenta** para folios (RN-25).

    PostgreSQL no tiene secuencias por fila, así que la asignación se hace con
    `UPDATE … RETURNING`: una sola sentencia que toma bloqueo de fila y es segura
    ante concurrencia sin transacciones serializables.
    """

    __tablename__ = "account_counter"

    account_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("account.id", ondelete="CASCADE"), primary_key=True
    )
    kind: Mapped[CounterKind] = mapped_column(
        pg_enum(CounterKind, "counter_kind"), primary_key=True
    )
    next_value: Mapped[int] = mapped_column(Integer, default=1, server_default="1")


class AuthToken(Base, PrimaryKeyMixin, CreatedAtMixin):
    """Tokens de un solo uso para recuperación de contraseña y verificación.

    §6 expone `forgot-password` / `reset-password` y §4.2 define
    `email_verified_at`, pero no había dónde guardar los tokens. Se almacena solo
    el hash: una fuga de la tabla no permite usarlos.
    """

    __tablename__ = "auth_token"
    __table_args__ = (UniqueConstraint("token_hash"),)

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("app_user.id", ondelete="CASCADE"), index=True
    )
    purpose: Mapped[AuthTokenPurpose] = mapped_column(
        pg_enum(AuthTokenPurpose, "auth_token_purpose")
    )
    token_hash: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
