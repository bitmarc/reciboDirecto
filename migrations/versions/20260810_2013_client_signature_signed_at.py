"""firma del cliente con fecha y hora

§8.1 imprime la firma del cliente **con fecha y hora**, y esa fecha no puede
salir de `issued_at`: la firma se captura al entregar el comprobante, que no
siempre coincide con el instante de emitirlo. Sin esta columna no hay fecha que
imprimir.

Revision ID: b3c7d1e94a20
Revises: f1f5848aace4
Create Date: 2026-08-10 20:13:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b3c7d1e94a20"
down_revision: str | None = "f1f5848aace4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "receipt",
        sa.Column("client_signature_signed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("receipt", "client_signature_signed_at")
