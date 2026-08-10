"""Recibos e idempotencia de la emisión.

El `snapshot` es el corazón del sistema (§4.3): la página pública y el PDF se
renderizan **exclusivamente desde él**, nunca desde las tablas vivas. Si mañana
el emisor cambia su logo, su teléfono o el nombre del cliente, los recibos ya
emitidos no cambian. Es lo único que sostiene su valor como prueba.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from receipt.domain.enums import ReceiptStatus, ReceiptTemplate, SignerCapacity
from receipt.models.base import Base, CreatedAtMixin, PrimaryKeyMixin, pg_enum


class Receipt(Base, PrimaryKeyMixin):
    """Tabla de solo-inserción en sus campos de contenido: un recibo emitido
    nunca se recalcula (RN-10). Lo único mutable es el estado de cancelación."""

    __tablename__ = "receipt"
    __table_args__ = (
        UniqueConstraint("account_id", "folio", name="uq_receipt_account_id_folio"),
        UniqueConstraint("payment_id", name="uq_receipt_payment_id"),
        Index("ix_receipt_account_id_issued_at", "account_id", "issued_at"),
        # RN-22 / AC-08: cancelar sin motivo debe fallar, también por SQL directo.
        CheckConstraint(
            "status <> 'cancelled'"
            " OR (cancellation_reason IS NOT NULL"
            "     AND btrim(cancellation_reason) <> ''"
            "     AND cancelled_at IS NOT NULL)",
            name="cancellation_requires_reason",
        ),
    )

    account_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("account.id", ondelete="CASCADE"), index=True
    )
    """Desnormalizado desde el pago para que la consulta pública no tenga que
    recorrer payment → service → account."""

    payment_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("payment.id"))

    folio: Mapped[str] = mapped_column(Text)
    """`RB-0002`. Secuencial por cuenta, con hueco tras cancelación (RN-23)."""

    public_token: Mapped[str] = mapped_column(Text, unique=True, index=True)
    """`secrets.token_urlsafe(32)`. Prohibido derivarlo del ID (§10)."""

    template: Mapped[ReceiptTemplate] = mapped_column(pg_enum(ReceiptTemplate, "receipt_template"))
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    """Todo el contenido congelado del recibo (§4.3)."""

    pdf_blob_path: Mapped[str | None] = mapped_column(Text)
    client_signature_blob_path: Mapped[str | None] = mapped_column(Text)

    client_signature_signer_name: Mapped[str | None] = mapped_column(Text)
    client_signature_signer_capacity: Mapped[SignerCapacity | None] = mapped_column(
        pg_enum(SignerCapacity, "signer_capacity")
    )
    """§14.1, resuelto: se captura quién firmó y en qué calidad. Sin este dato se
    reproduce el problema de "yo no fui" cuando firma un tercero."""

    client_signature_signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    """§8.1 imprime la firma del cliente **con fecha y hora**, y no puede tomarla
    de `issued_at`: la firma se captura al entregar el comprobante, que no
    siempre es el instante de emitirlo. Sin esta columna no habría fecha que
    imprimir."""

    status: Mapped[ReceiptStatus] = mapped_column(
        pg_enum(ReceiptStatus, "receipt_status"),
        default=ReceiptStatus.ISSUED,
        server_default=ReceiptStatus.ISSUED.value,
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancellation_reason: Mapped[str | None] = mapped_column(Text)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class IdempotencyKey(Base, PrimaryKeyMixin, CreatedAtMixin):
    """Protege `POST /payments` frente a doble envío (§6, AC-18).

    Un electricista con mala señal va a tocar el botón dos veces, y un recibo
    duplicado con un cobro duplicado es un fallo grave. La clave se inserta
    dentro de la misma transacción que emite el recibo: el segundo envío choca
    con la restricción única y devuelve el recibo ya emitido.
    """

    __tablename__ = "idempotency_key"
    __table_args__ = (
        UniqueConstraint("account_id", "key", name="uq_idempotency_key_account_id_key"),
    )

    account_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("account.id", ondelete="CASCADE")
    )
    key: Mapped[str] = mapped_column(Text)
    request_hash: Mapped[str] = mapped_column(Text)
    """Permite distinguir un reintento legítimo de una clave reusada con otro
    contenido, que es un error del cliente y debe reportarse."""

    receipt_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("receipt.id"))
