"""Alta y autenticación del emisor (E1)."""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from receipt.domain.enums import CounterKind, Plan
from receipt.models import AccountCounter
from receipt.services.accounts import authenticate, register_account
from receipt.services.errors import ConflictError, ValidationError
from tests.factories import make_account, make_user

pytestmark = pytest.mark.db


async def _register(db: AsyncSession, **overrides: str):
    payload = {
        "email": "electricista@example.mx",
        "password": "contrasena-larga",
        "person_name": "Juan Pérez",
        "phone": "5512345678",
        "default_city": "Ciudad de México",
    }
    payload.update(overrides)
    return await register_account(db, **payload)  # type: ignore[arg-type]


class TestRegistration:
    async def test_a_new_account_starts_on_the_free_plan(self, db: AsyncSession) -> None:
        user = await _register(db)
        await db.refresh(user, ["account"])
        assert user.account.plan is Plan.FREE

    async def test_folio_counters_are_seeded_at_registration(self, db: AsyncSession) -> None:
        """RN-25: los contadores existen desde el alta para que la emisión nunca
        tenga que crearlos bajo concurrencia."""
        user = await _register(db)
        counters = (
            await db.scalars(
                select(AccountCounter).where(AccountCounter.account_id == user.account_id)
            )
        ).all()
        assert {counter.kind for counter in counters} == set(CounterKind)
        assert all(counter.next_value == 1 for counter in counters)

    async def test_the_password_is_never_stored_in_clear_text(self, db: AsyncSession) -> None:
        user = await _register(db, password="contrasena-larga")
        assert "contrasena-larga" not in user.password_hash
        assert user.password_hash.startswith("$argon2id$")

    async def test_the_email_is_normalized(self, db: AsyncSession) -> None:
        user = await _register(db, email="  Electricista@Example.MX  ")
        assert user.email == "electricista@example.mx"

    async def test_a_duplicate_email_is_rejected(self, db: AsyncSession) -> None:
        await _register(db)
        with pytest.raises(ConflictError, match="ya tiene una cuenta"):
            await _register(db)

    async def test_a_short_password_is_rejected(self, db: AsyncSession) -> None:
        with pytest.raises(ValidationError, match="al menos 8"):
            await _register(db, password="corta")

    async def test_an_invalid_email_is_rejected(self, db: AsyncSession) -> None:
        with pytest.raises(ValidationError, match="correo"):
            await _register(db, email="no-es-un-correo")

    async def test_the_phone_is_required_because_it_appears_on_the_receipt(
        self, db: AsyncSession
    ) -> None:
        with pytest.raises(ValidationError, match="teléfono"):
            await _register(db, phone="   ")


class TestTheIssuerNameAndTheBusinessName:
    """§8.1: el recibo lo encabeza una identidad y lo firma una persona."""

    async def test_a_person_name_without_a_surname_is_rejected(self, db: AsyncSession) -> None:
        """Firma una persona, y un nombre de pila suelto no identifica a nadie
        meses después, que es cuando se consulta el comprobante."""
        with pytest.raises(ValidationError, match="apellido"):
            await _register(db, person_name="Juan")

    async def test_the_person_name_is_required(self, db: AsyncSession) -> None:
        with pytest.raises(ValidationError, match="nombre"):
            await _register(db, person_name="   ")

    async def test_the_business_name_is_optional_and_becomes_the_public_identity(
        self, db: AsyncSession
    ) -> None:
        user = await _register(db, business_name="Plomería Sánchez")
        await db.refresh(user, ["account"])

        assert user.account.person_name == "Juan Pérez", "quien firma sigue siendo la persona"
        assert user.account.business_name == "Plomería Sánchez"
        assert user.account.issuer_name == "Plomería Sánchez", "y es lo que encabeza el recibo"

    async def test_without_a_business_the_person_is_the_public_identity(
        self, db: AsyncSession
    ) -> None:
        user = await _register(db)
        await db.refresh(user, ["account"])

        assert user.account.business_name is None
        assert user.account.issuer_name == "Juan Pérez"

    async def test_a_one_word_business_name_is_fine(self, db: AsyncSession) -> None:
        """Un negocio se puede llamar como quiera: no firma nada."""
        user = await _register(db, business_name="Plomería")
        await db.refresh(user, ["account"])

        assert user.account.business_name == "Plomería"


class TestAuthentication:
    async def test_correct_credentials_return_the_user(self, db: AsyncSession) -> None:
        account = await make_account(db)
        user = await make_user(db, account, email="a@example.mx", password="una-contrasena")

        found = await authenticate(db, email="a@example.mx", password="una-contrasena")

        assert found is not None
        assert found.id == user.id

    async def test_a_wrong_password_is_rejected(self, db: AsyncSession) -> None:
        account = await make_account(db)
        await make_user(db, account, email="a@example.mx", password="una-contrasena")

        assert await authenticate(db, email="a@example.mx", password="otra") is None

    async def test_an_unknown_email_is_rejected_without_revealing_it_is_unknown(
        self, db: AsyncSession
    ) -> None:
        assert await authenticate(db, email="nadie@example.mx", password="x") is None

    async def test_the_email_is_matched_case_insensitively(self, db: AsyncSession) -> None:
        account = await make_account(db)
        await make_user(db, account, email="a@example.mx", password="una-contrasena")

        found = await authenticate(db, email="A@Example.MX", password="una-contrasena")
        assert found is not None
