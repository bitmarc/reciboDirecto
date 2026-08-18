"""nombre de la persona y nombre del negocio, separados

§8.1 imprimía dos cosas distintas con el mismo dato: la cabecera lleva la
identidad pública del emisor y el pie dice «Firma de quien recibe el pago». Con
una sola columna, quien registró «Plomería Sánchez» firmaba con el nombre de su
negocio, y un negocio no firma.

`display_name` se **renombra** a `person_name` en vez de crear una columna y
borrar la otra. No es cosmético: `--autogenerate` propone justamente
`add_column` × 2 + `drop_column`, que borraría el nombre de todas las cuentas.
Renombrar conserva el dato sin copiarlo y hace exacta la reversión.

Lo que cada cuenta tenía registrado hasta hoy es el único nombre que existe y es
el que ya salía en la línea de la firma, así que ahí es donde tiene que quedar.
Las cuentas que en realidad habían escrito una razón social la conservan tal cual
y la corrigen editando su perfil; adivinar cuál de los dos era cada texto sería
inventar datos sobre personas. Los recibos ya emitidos no cambian: su contenido
está congelado en el snapshot (RN-10).

Revision ID: f9bb81e5db36
Revises: b3c7d1e94a20
Create Date: 2026-08-18 21:49:14.169732
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f9bb81e5db36"
down_revision: str | None = "b3c7d1e94a20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # `existing_type` y `existing_nullable` van explícitos porque Alembic los
    # necesita para renombrar en varios dialectos, y de paso dejan escrito que la
    # columna sigue siendo TEXT NOT NULL.
    op.alter_column(
        "account",
        "display_name",
        new_column_name="person_name",
        existing_type=sa.Text(),
        existing_nullable=False,
    )
    op.add_column("account", sa.Column("business_name", sa.Text(), nullable=True))


def downgrade() -> None:
    # Dos columnas no caben en una: la reversión tiene que elegir, y elige lo que
    # `display_name` significaba —lo que encabeza el recibo (§8.1)—, que es el
    # negocio cuando existe. Así los recibos que se emitan con el código
    # revertido se ven igual que los de antes de este cambio.
    #
    # El nombre de la persona se pierde en esas cuentas, y es la pérdida
    # correcta: la columna única nunca supo distinguir los dos nombres.
    op.execute(
        "UPDATE account SET person_name = business_name "
        "WHERE business_name IS NOT NULL AND btrim(business_name) <> ''"
    )
    op.drop_column("account", "business_name")
    op.alter_column(
        "account",
        "person_name",
        new_column_name="display_name",
        existing_type=sa.Text(),
        existing_nullable=False,
    )
