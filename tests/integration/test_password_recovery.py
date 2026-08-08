"""Recuperación de contraseña y verificación de correo (E1, §6)."""

import re

import pytest
from httpx import AsyncClient

from receipt.infra.mail import RecordingMailer
from tests.conftest import REGISTRATION, register_through_app

pytestmark = pytest.mark.db

_LINK = re.compile(r"https?://\S+/(?:restablecer|verificar)\?token=(?P<token>[\w-]+)")


def token_from(mailer: RecordingMailer, address: str) -> str:
    message = mailer.last_to(address)
    assert message is not None, f"no se envió ningún correo a {address}"
    match = _LINK.search(message.body)
    assert match is not None, f"el correo no trae liga:\n{message.body}"
    return match.group("token")


class TestRequestingAReset:
    async def test_the_response_is_identical_for_known_and_unknown_emails(
        self, client: AsyncClient, mailer: RecordingMailer
    ) -> None:
        """Un mensaje distinto convertiría el formulario en un detector de
        cuentas dadas de alta."""
        await register_through_app(client)

        known = await client.post("/auth/forgot-password", data={"email": REGISTRATION["email"]})
        unknown = await client.post("/auth/forgot-password", data={"email": "nadie@example.mx"})

        assert known.status_code == unknown.status_code == 200
        assert known.text == unknown.text

    async def test_only_the_real_address_receives_a_message(
        self, client: AsyncClient, mailer: RecordingMailer
    ) -> None:
        await register_through_app(client)
        mailer.sent.clear()

        await client.post("/auth/forgot-password", data={"email": "nadie@example.mx"})
        assert mailer.sent == []

        await client.post("/auth/forgot-password", data={"email": REGISTRATION["email"]})
        assert mailer.last_to(REGISTRATION["email"]) is not None

    async def test_a_malformed_email_does_not_crash_the_form(self, client: AsyncClient) -> None:
        response = await client.post("/auth/forgot-password", data={"email": "no-es-un-correo"})
        assert response.status_code == 200


class TestUsingTheLink:
    async def test_the_password_changes_and_the_session_starts(
        self, client: AsyncClient, mailer: RecordingMailer
    ) -> None:
        await register_through_app(client)
        await client.post("/auth/forgot-password", data={"email": REGISTRATION["email"]})
        token = token_from(mailer, REGISTRATION["email"])

        client.cookies.clear()
        response = await client.post(
            "/auth/reset-password",
            data={"token": token, "password": "una-contrasena-nueva"},
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert (await client.get("/")).status_code == 200

    async def test_the_old_password_stops_working(
        self, client: AsyncClient, mailer: RecordingMailer
    ) -> None:
        await register_through_app(client)
        await client.post("/auth/forgot-password", data={"email": REGISTRATION["email"]})
        token = token_from(mailer, REGISTRATION["email"])
        await client.post(
            "/auth/reset-password",
            data={"token": token, "password": "una-contrasena-nueva"},
            follow_redirects=False,
        )
        client.cookies.clear()

        response = await client.post(
            "/auth/login",
            data={"email": REGISTRATION["email"], "password": REGISTRATION["password"]},
            follow_redirects=False,
        )
        assert response.status_code == 401

    async def test_a_token_only_works_once(
        self, client: AsyncClient, mailer: RecordingMailer
    ) -> None:
        await register_through_app(client)
        await client.post("/auth/forgot-password", data={"email": REGISTRATION["email"]})
        token = token_from(mailer, REGISTRATION["email"])

        first = await client.post(
            "/auth/reset-password",
            data={"token": token, "password": "una-contrasena-nueva"},
            follow_redirects=False,
        )
        second = await client.post(
            "/auth/reset-password",
            data={"token": token, "password": "otra-contrasena-mas"},
        )

        assert first.status_code == 303
        assert second.status_code == 400
        assert "ya no sirve" in second.text

    async def test_asking_again_invalidates_the_previous_link(
        self, client: AsyncClient, mailer: RecordingMailer
    ) -> None:
        """Si un atacante pidió una liga antes, la del dueño la anula."""
        await register_through_app(client)
        await client.post("/auth/forgot-password", data={"email": REGISTRATION["email"]})
        first_token = token_from(mailer, REGISTRATION["email"])

        await client.post("/auth/forgot-password", data={"email": REGISTRATION["email"]})
        second_token = token_from(mailer, REGISTRATION["email"])
        assert first_token != second_token

        stale = await client.post(
            "/auth/reset-password",
            data={"token": first_token, "password": "una-contrasena-nueva"},
        )
        assert stale.status_code == 400

    async def test_an_invented_token_is_refused(self, client: AsyncClient) -> None:
        response = await client.post(
            "/auth/reset-password",
            data={"token": "token-inventado", "password": "una-contrasena-nueva"},
        )
        assert response.status_code == 400

    async def test_a_short_password_is_refused(
        self, client: AsyncClient, mailer: RecordingMailer
    ) -> None:
        await register_through_app(client)
        await client.post("/auth/forgot-password", data={"email": REGISTRATION["email"]})
        token = token_from(mailer, REGISTRATION["email"])

        response = await client.post(
            "/auth/reset-password", data={"token": token, "password": "corta"}
        )
        assert response.status_code == 400


class TestEmailVerification:
    async def test_registering_sends_the_confirmation(
        self, client: AsyncClient, mailer: RecordingMailer
    ) -> None:
        await register_through_app(client)
        assert mailer.last_to(REGISTRATION["email"]) is not None

    async def test_the_link_confirms_the_address(
        self, client: AsyncClient, mailer: RecordingMailer
    ) -> None:
        await register_through_app(client)
        token = token_from(mailer, REGISTRATION["email"])

        response = await client.get("/verificar", params={"token": token})

        assert response.status_code == 200
        assert "confirmado" in response.text
        assert "confirmado" in (await client.get("/ajustes")).text

    async def test_an_unconfirmed_account_can_still_work(self, signed_in: AsyncClient) -> None:
        """No bloquea el uso: dejar al primer usuario real fuera por un correo
        caído en spam sería un autogol."""
        assert (await signed_in.get("/")).status_code == 200
        assert (await signed_in.get("/clientes")).status_code == 200

    async def test_a_used_token_is_refused(
        self, client: AsyncClient, mailer: RecordingMailer
    ) -> None:
        await register_through_app(client)
        token = token_from(mailer, REGISTRATION["email"])

        await client.get("/verificar", params={"token": token})
        second = await client.get("/verificar", params={"token": token})

        assert second.status_code == 400


class TestThrottling:
    async def test_repeated_failures_are_throttled(self, client: AsyncClient) -> None:
        """§10 lo exige: sin límite, el formulario permite probar contraseñas
        sin fin."""
        await register_through_app(client)
        client.cookies.clear()

        statuses = []
        for _ in range(12):
            response = await client.post(
                "/auth/login",
                data={"email": REGISTRATION["email"], "password": "incorrecta"},
                follow_redirects=False,
            )
            statuses.append(response.status_code)

        assert 429 in statuses, "el login nunca llegó a limitar los intentos"

    async def test_a_correct_login_clears_the_penalty(self, client: AsyncClient) -> None:
        await register_through_app(client)
        client.cookies.clear()

        for _ in range(3):
            await client.post(
                "/auth/login",
                data={"email": REGISTRATION["email"], "password": "incorrecta"},
                follow_redirects=False,
            )

        good = await client.post(
            "/auth/login",
            data={"email": REGISTRATION["email"], "password": REGISTRATION["password"]},
            follow_redirects=False,
        )
        assert good.status_code == 303
