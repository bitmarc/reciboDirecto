"""Directorio de clientes (E2)."""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from receipt.models import Client
from receipt.repositories import AccountScope
from receipt.services.clients import (
    archive_client,
    create_client,
    find_possible_duplicates,
    restore_client,
    search_statement,
)
from receipt.services.errors import ValidationError
from tests.conftest import csrf_token_of
from tests.factories import make_account

pytestmark = pytest.mark.db


@pytest.fixture
async def scope(db: AsyncSession) -> AccountScope:
    account = await make_account(db)
    return AccountScope(db, account.id)


class TestCreation:
    async def test_a_client_is_created_with_its_data_normalized(self, scope: AccountScope) -> None:
        client = await create_client(
            scope, full_name="  Ana   Ramírez ", phone="55 1234 5678", email=" ANA@example.MX "
        )

        assert client.full_name == "Ana Ramírez"
        assert client.phone == "55 1234 5678"
        assert client.email == "ana@example.mx"
        assert client.archived_at is None

    async def test_a_single_word_name_is_rejected(self, scope: AccountScope) -> None:
        with pytest.raises(ValidationError):
            await create_client(scope, full_name="Ana", phone="5512345678")

    async def test_the_email_is_optional(self, scope: AccountScope) -> None:
        client = await create_client(scope, full_name="Ana Ramírez", phone="5512345678")
        assert client.email is None


class TestSearch:
    @pytest.fixture(autouse=True)
    async def _directory(self, scope: AccountScope) -> None:
        await create_client(scope, full_name="Ana Ramírez", phone="55 1234 5678")
        await create_client(scope, full_name="Óscar Muñoz", phone="5598765432")
        await create_client(scope, full_name="Beatriz Solís", phone="5555555555")

    async def _names(self, scope: AccountScope, **kwargs: object) -> list[str]:
        found = await scope.list(search_statement(scope, **kwargs))  # type: ignore[arg-type]
        return [client.full_name for client in found]

    async def test_an_empty_query_lists_everyone_sorted(self, scope: AccountScope) -> None:
        assert await self._names(scope) == ["Ana Ramírez", "Beatriz Solís", "Óscar Muñoz"]

    async def test_it_finds_by_partial_surname(self, scope: AccountScope) -> None:
        assert await self._names(scope, query="ramírez") == ["Ana Ramírez"]

    async def test_the_search_is_case_insensitive(self, scope: AccountScope) -> None:
        assert await self._names(scope, query="ANA") == ["Ana Ramírez"]

    async def test_it_finds_an_accented_name_typed_without_accents(
        self, scope: AccountScope
    ) -> None:
        """El caso real: nadie escribe tildes en el celular estando de pie.

        Un buscador que no encuentra a la clienta obliga a darla de alta otra
        vez, y el directorio se llena de duplicados.
        """
        assert await self._names(scope, query="ramirez") == ["Ana Ramírez"]
        assert await self._names(scope, query="oscar munoz") == ["Óscar Muñoz"]

    async def test_it_finds_a_plain_name_typed_with_accents(self, scope: AccountScope) -> None:
        await create_client(scope, full_name="Jose Solis", phone="5511112222")
        assert "Jose Solis" in await self._names(scope, query="josé solís")

    async def test_it_finds_by_phone_as_it_was_stored(self, scope: AccountScope) -> None:
        assert await self._names(scope, query="55 1234") == ["Ana Ramírez"]

    async def test_it_finds_by_phone_without_separators(self, scope: AccountScope) -> None:
        """El emisor teclea el número como lo recuerda, no como lo capturó."""
        assert await self._names(scope, query="5512345678") == ["Ana Ramírez"]

    async def test_it_finds_a_phone_stored_without_separators_typed_with_them(
        self, scope: AccountScope
    ) -> None:
        assert await self._names(scope, query="55 9876 5432") == ["Óscar Muñoz"]

    async def test_an_unmatched_query_returns_nothing(self, scope: AccountScope) -> None:
        assert await self._names(scope, query="zzz") == []


class TestArchiving:
    async def test_archiving_hides_from_the_selector_but_keeps_the_record(
        self, scope: AccountScope
    ) -> None:
        client = await create_client(scope, full_name="Ana Ramírez", phone="5512345678")

        await archive_client(scope, client)

        assert client.archived_at is not None
        assert await scope.list(search_statement(scope)) == []
        assert len(await scope.list(search_statement(scope, include_archived=True))) == 1

    async def test_archiving_twice_keeps_the_original_date(self, scope: AccountScope) -> None:
        client = await create_client(scope, full_name="Ana Ramírez", phone="5512345678")
        await archive_client(scope, client)
        first = client.archived_at

        await archive_client(scope, client)

        assert client.archived_at == first

    async def test_it_can_be_restored(self, scope: AccountScope) -> None:
        client = await create_client(scope, full_name="Ana Ramírez", phone="5512345678")
        await archive_client(scope, client)

        await restore_client(scope, client)

        assert client.archived_at is None
        assert len(await scope.list(search_statement(scope))) == 1


class TestDuplicates:
    async def test_the_same_phone_is_flagged_but_not_blocked(self, scope: AccountScope) -> None:
        """Dos personas pueden compartir el teléfono de casa: se avisa, no se
        impide."""
        first = await create_client(scope, full_name="Ana Ramírez", phone="55 1234 5678")
        second = await create_client(scope, full_name="Luis Ramírez", phone="5512345678")

        duplicates = await find_possible_duplicates(scope, phone=second.phone, exclude_id=second.id)

        assert [client.id for client in duplicates] == [first.id]

    async def test_an_archived_client_is_not_reported_as_a_duplicate(
        self, scope: AccountScope
    ) -> None:
        first = await create_client(scope, full_name="Ana Ramírez", phone="5512345678")
        await archive_client(scope, first)

        assert await find_possible_duplicates(scope, phone="5512345678") == []


class TestThroughTheWeb:
    async def test_the_full_round_trip(self, signed_in: AsyncClient) -> None:
        created = await signed_in.post(
            "/clientes",
            data={
                "csrf_token": csrf_token_of(signed_in),
                "full_name": "Ana Ramírez",
                "phone": "55 1234 5678",
                "email": "ana@example.mx",
            },
            follow_redirects=False,
        )
        assert created.status_code == 303

        directory = await signed_in.get("/clientes")
        assert "Ana Ramírez" in directory.text

        results = await signed_in.get("/clientes/buscar", params={"q": "5512345678"})
        assert "Ana Ramírez" in results.text

    async def test_a_single_word_name_comes_back_with_an_explanation(
        self, signed_in: AsyncClient
    ) -> None:
        response = await signed_in.post(
            "/clientes",
            data={
                "csrf_token": csrf_token_of(signed_in),
                "full_name": "Ana",
                "phone": "5512345678",
            },
        )
        assert response.status_code == 400
        assert "apellido" in response.text

    async def test_a_post_without_a_csrf_token_is_refused(self, signed_in: AsyncClient) -> None:
        response = await signed_in.post(
            "/clientes", data={"full_name": "Ana Ramírez", "phone": "5512345678"}
        )
        assert response.status_code == 403

    async def test_a_client_of_another_account_is_not_reachable(
        self, signed_in: AsyncClient, db: AsyncSession
    ) -> None:
        other = await make_account(db, display_name="Otra cuenta")
        foreign = Client(account_id=other.id, full_name="Cliente Ajeno", phone="5500000000")
        db.add(foreign)
        await db.flush()

        response = await signed_in.get(f"/clientes/{foreign.id}")

        assert response.status_code == 404
        assert "Cliente Ajeno" not in response.text
