"""El contrato entre los módulos de JavaScript y las plantillas que los usan.

No hay navegador en la suite, así que nada aquí ejecuta el JavaScript. Lo que sí
se puede comprobar —y es donde de verdad se rompe— es que **los enganches
existan en las dos partes**: un atributo renombrado en la plantilla deja el
script buscando algo que ya no está, la pantalla se queda muerta y ninguna
prueba de servidor se entera.

Esta prueba nació de dos fallos reales encontrados al cablear `/cobrar`:

1. `signature.js` buscaba el formulario por `id="signature-form"`, que en
   `/cobrar` no existe: la firma nunca habría llegado al servidor.
2. Al estar vacío el lienzo hacía `preventDefault()` sobre el formulario que lo
   contiene. En `/cobrar` eso es el formulario del cobro entero, así que **nadie
   habría podido cobrar sin dibujar una firma**, cuando RN-21 la declara
   opcional.
"""

import re
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from receipt.models import Account, Client
from receipt.repositories import AccountScope
from receipt.services.charging import ChargeRequest, charge
from receipt.web.templating import STATIC_DIR
from tests.factories import make_client

pytestmark = pytest.mark.db

JS_DIR = Path(STATIC_DIR) / "js"


def script(name: str) -> str:
    return (JS_DIR / f"{name}.js").read_text(encoding="utf-8")


def without_comments(source: str) -> str:
    """El código, sin los comentarios.

    Los comentarios de estos archivos explican qué NO se hace y por qué —"Alpine
    evalúa con `new Function()`"—, así que buscar sobre el texto crudo encuentra
    justo lo que se está prohibiendo.
    """
    return re.sub(r"//.*", "", source)


def hooks_of(source: str) -> set[str]:
    """Los `data-*` que el script busca en el DOM."""
    return set(re.findall(r'\[(data-[a-z-]+)[\]="]', without_comments(source)))


@pytest.fixture
async def account(signed_in: AsyncClient, db: AsyncSession) -> Account:
    account = await db.scalar(select(Account))
    assert account is not None
    return account


@pytest.fixture
async def customer(db: AsyncSession, account: Account) -> Client:
    return await make_client(db, account, full_name="Ana Ramírez", phone="5598765432")


class TestTheChargeScreenHooks:
    async def test_every_hook_the_script_looks_for_exists_in_the_markup(
        self, signed_in: AsyncClient, db: AsyncSession, account: Account, customer: Client
    ) -> None:
        # La pantalla se arma con tres piezas: el formulario y los dos
        # fragmentos que HTMX inyecta. Los enganches viven repartidos entre las
        # tres, así que hay que mirarlas juntas.
        await charge(
            AccountScope(db, account.id),
            account,
            ChargeRequest(
                client_id=customer.id,
                concept="Instalación de contactos",
                agreed_total_cents=230000,
                payment_amount_cents=180000,
            ),
        )
        markup = ""
        for url in (
            "/cobrar",
            "/cobrar/clientes?q=ramirez",
            f"/cobrar/trabajos?client_id={customer.id}",
        ):
            markup += (await signed_in.get(url)).text

        missing = [hook for hook in hooks_of(script("cobrar")) if hook not in markup]
        assert not missing, (
            f"`cobrar.js` busca {missing} y ninguna plantilla los pone: "
            f"la pantalla se queda muerta sin que falle nada en el servidor."
        )

    async def test_the_ids_the_script_reads_are_there(self, signed_in: AsyncClient) -> None:
        page = (await signed_in.get("/cobrar")).text

        for element_id in ("cobro", "trabajos-abiertos", "cliente-resultados"):
            assert f'id="{element_id}"' in page, element_id

    async def test_every_reveal_points_at_something_that_exists(
        self, signed_in: AsyncClient, customer: Client
    ) -> None:
        """`data-revela="#x"` sin `#x` en la página es un botón que no hace nada."""
        page = (await signed_in.get("/cobrar")).text
        jobs = (await signed_in.get("/cobrar/trabajos?client_id=" + str(customer.id))).text

        for target in re.findall(r'data-revela="#([a-z-]+)"', page + jobs):
            assert f'id="{target}"' in page + jobs, f"data-revela apunta a #{target}, que no existe"

    async def test_the_script_is_actually_loaded(self, signed_in: AsyncClient) -> None:
        page = (await signed_in.get("/cobrar")).text
        assert "/static/js/cobrar.js" in page
        assert "/static/js/htmx.min.js" in page, "el buscador y los trabajos van por HTMX"


class TestTheSignatureContract:
    """RN-21: la firma del cliente es opcional. El script no puede olvidarlo."""

    async def test_the_charge_form_does_not_require_a_stroke(self, signed_in: AsyncClient) -> None:
        """El bug que casi entra: sin este atributo, `signature.js` bloquearía el
        envío del cobro cada vez que el lienzo estuviera vacío, que es el caso
        normal."""
        page = (await signed_in.get("/cobrar")).text

        form_start = page.index('<form method="post" action="/cobrar"')
        form_tag = page[form_start : page.index(">", form_start)]
        assert "data-firma-obligatoria" not in form_tag

    async def test_the_dedicated_signature_forms_do_require_one(
        self, signed_in: AsyncClient, db: AsyncSession, account: Account, customer: Client
    ) -> None:
        """En /ajustes y en /recibos el formulario existe solo para firmar:
        enviarlo vacío no significa nada."""
        result = await charge(
            AccountScope(db, account.id),
            account,
            ChargeRequest(client_id=customer.id, concept="Instalación", payment_amount_cents=45000),
        )

        for url in ("/ajustes", f"/recibos/{result.receipt.id}"):
            page = (await signed_in.get(url)).text
            assert "data-firma-obligatoria" in page, url

    @pytest.mark.parametrize("url", ["/ajustes", "/cobrar"])
    async def test_the_canvas_and_its_field_travel_together(
        self, signed_in: AsyncClient, url: str
    ) -> None:
        """El script escribe el PNG en el campo oculto; sin él no se manda nada."""
        page = (await signed_in.get(url)).text

        assert 'id="signature-canvas"' in page
        assert 'id="signature-data"' in page
        assert 'name="signature_data"' in page

    async def test_the_canvas_lives_inside_the_form_that_submits_it(
        self, signed_in: AsyncClient
    ) -> None:
        """`signature.js` sube por `closest("form")`. Si el canvas quedara fuera
        del formulario, el PNG no se adjuntaría a nada."""
        page = (await signed_in.get("/cobrar")).text

        form_start = page.index('<form method="post" action="/cobrar"')
        form_end = page.index("</form>", form_start)
        assert 'id="signature-canvas"' in page[form_start:form_end]


class TestTheCopyLinkContract:
    async def test_the_button_carries_the_url_the_script_reads(
        self, signed_in: AsyncClient, db: AsyncSession, account: Account, customer: Client
    ) -> None:
        result = await charge(
            AccountScope(db, account.id),
            account,
            ChargeRequest(client_id=customer.id, concept="Instalación", payment_amount_cents=45000),
        )

        page = (await signed_in.get(f"/recibos/{result.receipt.id}")).text

        assert "data-copiar=" in page, "`copiar.js` lee la liga de este atributo"
        assert "/static/js/copiar.js" in page
        assert result.receipt.public_token in page


class TestNoThirdPartyScripts:
    def test_every_script_is_vendored_locally(self) -> None:
        """La CSP es `script-src 'self'` (§10).

        Un CDN en la CSP es un tercero con permiso de ejecutar código sobre
        datos de cobro. Todo lo que se carga vive en `static/js/`.
        """
        for source in JS_DIR.glob("*.js"):
            text = source.read_text(encoding="utf-8", errors="ignore")
            assert "https://cdn" not in text, source.name

    def test_no_script_needs_unsafe_eval(self) -> None:
        """La razón por la que la pantalla de cobro no usa Alpine.

        `new Function(...)` y `eval(...)` obligarían a añadir `unsafe-eval` a la
        CSP justo en la pantalla que maneja los datos de cobro.
        """
        for name in ("cobrar", "signature", "copiar"):
            source = without_comments(script(name))
            assert "new Function" not in source, name
            assert re.search(r"\beval\s*\(", source) is None, name
