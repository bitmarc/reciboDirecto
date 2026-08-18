"""El esqueleto web responde y las rutas privadas están cerradas.

El fixture `client` vive en `conftest.py` y lo comparten todas las pruebas web.
"""

import pytest
from httpx import AsyncClient

from receipt.security.headers import PUBLIC_RECEIPT_PREFIX

pytestmark = pytest.mark.db


async def test_health_check_responds(client: AsyncClient) -> None:
    response = await client.get("/healthz")
    assert response.status_code == 200


async def test_the_root_welcomes_a_visitor_without_a_session(client: AsyncClient) -> None:
    """§1.4: quien llega de fuera necesita saber qué es esto antes de dar su correo.

    `/` tiene dos públicos. Sin sesión sirve la bienvenida; el panel de §7.4 vive
    detrás de la cookie y se comprueba en `test_accounting`.
    """
    response = await client.get("/", follow_redirects=False)

    assert response.status_code == 200
    assert "Crear cuenta gratis" in response.text
    # La bienvenida no puede filtrar el menú del emisor.
    assert 'href="/cobrar"' not in response.text


async def test_a_private_screen_redirects_to_login_without_a_session(
    client: AsyncClient,
) -> None:
    response = await client.get("/cobrar", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/entrar?next=/cobrar"


async def test_a_forged_session_cookie_is_rejected(client: AsyncClient) -> None:
    """La cookie va firmada: un valor inventado no da acceso.

    Se comprueba sobre una ruta privada y no sobre `/`, que desde la bienvenida
    responde 200 a cualquiera y ya no distinguiría una cookie falsa de la
    ausencia de cookie.
    """
    client.cookies.set("receipt_session", "eyJ1IjoxLCJhIjoxLCJjIjoieCJ9.falsa.firma")
    response = await client.get("/ajustes", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/entrar")


async def test_registration_and_login_complete_the_round_trip(client: AsyncClient) -> None:
    registration = await client.post(
        "/auth/register",
        data={
            "email": "electricista@example.mx",
            "password": "contrasena-larga",
            "display_name": "Juan Pérez",
            "phone": "5512345678",
            "default_city": "Ciudad de México",
            "occupation": "Electricista",
        },
        follow_redirects=False,
    )
    assert registration.status_code == 303
    assert registration.headers["location"] == "/"

    dashboard = await client.get("/")
    assert dashboard.status_code == 200
    assert "Juan Pérez" in dashboard.text


async def test_wrong_credentials_do_not_issue_a_session(client: AsyncClient) -> None:
    response = await client.post(
        "/auth/login",
        data={"email": "nadie@example.mx", "password": "x"},
        follow_redirects=False,
    )
    assert response.status_code == 401
    assert "receipt_session" not in response.cookies


async def test_login_does_not_redirect_to_an_external_site(client: AsyncClient) -> None:
    """Un `next` absoluto convertiría el login en un redirector abierto."""
    await client.post(
        "/auth/register",
        data={
            "email": "otro@example.mx",
            "password": "contrasena-larga",
            "display_name": "Otro Emisor",
            "phone": "5512345678",
            "default_city": "Ciudad de México",
        },
        follow_redirects=False,
    )
    response = await client.post(
        "/auth/login",
        data={
            "email": "otro@example.mx",
            "password": "contrasena-larga",
            "next": "https://sitio-malicioso.example/",
        },
        follow_redirects=False,
    )
    assert response.headers["location"] == "/"


class TestSecurityHeaders:
    async def test_a_content_security_policy_is_always_sent(self, client: AsyncClient) -> None:
        response = await client.get("/entrar")
        assert "default-src 'self'" in response.headers["content-security-policy"]
        assert response.headers["x-content-type-options"] == "nosniff"

    async def test_public_receipt_pages_are_marked_noindex(self, client: AsyncClient) -> None:
        """§7.3: la liga del recibo no debe indexarse. La ruta llega en el hito 3;
        la cabecera ya está puesta para cuando exista."""
        response = await client.get(f"{PUBLIC_RECEIPT_PREFIX}token-inexistente")
        assert response.headers["x-robots-tag"] == "noindex, nofollow"
