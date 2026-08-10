"""Pantalla de cobro — `AC-18` y `AC-19`.

Es la única pantalla que importa (§7.1). El hito 2 ya probó el motor por debajo;
lo que se comprueba aquí es que el momento de verdad de §1.4 se resuelve tal
como lo describe §7.2: un formulario, un botón, sin recargas intermedias, y a
prueba de un pulgar que toca dos veces con mala señal.
"""

import io
import re

import pytest
from httpx import AsyncClient
from PIL import Image
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from receipt.domain.enums import PaymentStatus, ReceiptTemplate, ServiceStatus, SignerCapacity
from receipt.models import Account, Client, Payment, Receipt, Service
from receipt.repositories import AccountScope
from receipt.services.charging import ChargeRequest, charge
from receipt.services.work import load_totals
from tests.conftest import csrf_token_of, document_text, visible_text
from tests.factories import make_client
from tests.unit.test_images import as_data_uri

pytestmark = pytest.mark.db


def a_signature() -> str:
    buffer = io.BytesIO()
    Image.new("RGBA", (400, 150), (20, 24, 29, 255)).save(buffer, format="PNG")
    return as_data_uri(buffer.getvalue())


@pytest.fixture
async def account(signed_in: AsyncClient, db: AsyncSession) -> Account:
    account = await db.scalar(select(Account))
    assert account is not None
    return account


@pytest.fixture
async def scope(db: AsyncSession, account: Account) -> AccountScope:
    return AccountScope(db, account.id)


@pytest.fixture
async def customer(db: AsyncSession, account: Account) -> Client:
    return await make_client(db, account, full_name="Ana Ramírez", phone="5598765432")


def cobro_form(response) -> str:
    """Solo el formulario de cobro.

    La barra de navegación trae su propio formulario —el de salir—, así que
    contar sobre la página entera mediría el cromo en vez de la pantalla.
    """
    start = response.text.index('<form method="post" action="/cobrar"')
    return response.text[start : response.text.index("</form>", start)]


def key_of(response) -> str:
    """La clave de idempotencia que el servidor puso en el formulario."""
    match = re.search(r'name="idempotency_key" value="([^"]+)"', response.text)
    assert match, "el formulario no trae clave de idempotencia"
    return match.group(1)


async def cobrar(http: AsyncClient, **fields) -> object:
    payload = {"csrf_token": csrf_token_of(http)}
    payload.update({k: str(v) for k, v in fields.items()})
    return await http.post("/cobrar", data=payload, follow_redirects=False)


class TestAc19OneScreenNoIntermediateReloads:
    """El flujo completo se puede completar en una sola pantalla."""

    async def test_the_screen_carries_the_four_blocks_of_section_72(
        self, signed_in: AsyncClient
    ) -> None:
        response = await signed_in.get("/cobrar")

        assert response.status_code == 200
        text = document_text(response)
        assert "¿A quién le cobras?" in text, "bloque 1 — cliente"
        assert "¿Qué trabajo fue?" in text, "bloque 2 — trabajo"
        assert "¿Cuánto recibiste?" in text, "bloque 3 — pago"
        assert "Firma" in text, "bloque 4 — firma"

    async def test_there_is_exactly_one_submit_button(self, signed_in: AsyncClient) -> None:
        """§7.2: un solo botón de confirmación. No hay wizard de varios pasos."""
        response = await signed_in.get("/cobrar")
        assert len(re.findall(r'type="submit"', cobro_form(response))) == 1
        assert "Generar recibo" in visible_text(response)

    async def test_everything_posts_to_a_single_endpoint(self, signed_in: AsyncClient) -> None:
        """Un solo POST resuelve cliente, trabajo, extra, pago y recibo (§6)."""
        response = await signed_in.get("/cobrar")
        # Un solo formulario de cobro, y ninguno anidado dentro de él.
        assert response.text.count('action="/cobrar"') == 1
        assert "<form" not in cobro_form(response)[len("<form") :]

    async def test_a_whole_charge_completes_in_one_post(
        self, signed_in: AsyncClient, scope: AccountScope, customer: Client
    ) -> None:
        form = await signed_in.get("/cobrar")

        response = await cobrar(
            signed_in,
            idempotency_key=key_of(form),
            client_id=customer.id,
            concept="Instalación de contactos",
            location_alias="Casa Ramírez",
            agreed_total="2300",
            amount="1800",
            method="cash",
        )

        assert response.status_code == 303
        assert response.headers["location"].startswith("/recibos/")
        assert response.headers["location"].endswith("?listo=1")

    async def test_the_client_search_is_a_fragment_not_a_page(
        self, signed_in: AsyncClient, customer: Client
    ) -> None:
        """El buscador va por HTMX: es un dato, no un paso del flujo."""
        response = await signed_in.get("/cobrar/clientes?q=ramirez")

        assert response.status_code == 200
        assert "<html" not in response.text.lower()
        assert "Ana Ramírez" in visible_text(response)
        assert "data-elige-cliente" in response.text, "selecciona en vez de navegar"

    async def test_the_open_jobs_list_is_a_fragment_too(
        self, signed_in: AsyncClient, scope: AccountScope, account: Account, customer: Client
    ) -> None:
        await charge(
            scope,
            account,
            ChargeRequest(
                client_id=customer.id,
                concept="Instalación de contactos",
                agreed_total_cents=230000,
                payment_amount_cents=180000,
            ),
        )

        response = await signed_in.get(f"/cobrar/trabajos?client_id={customer.id}")

        assert "<html" not in response.text.lower()
        text = visible_text(response)
        assert "Instalación de contactos" in text
        assert "SRV-0001" in text
        assert "$500.00" in text, "§7.2 muestra total, pagado y saldo al continuar"

    async def test_a_client_without_open_jobs_gets_no_continue_option(
        self, signed_in: AsyncClient, customer: Client
    ) -> None:
        """§7.2: si no hay trabajos abiertos, esa opción no se muestra."""
        response = await signed_in.get(f"/cobrar/trabajos?client_id={customer.id}")
        assert "Trabajo nuevo" not in visible_text(response)


class TestAc18DoubleSubmitProducesOneReceipt:
    """Un electricista con mala señal va a tocar el botón dos veces."""

    async def test_two_submissions_with_the_same_key_produce_one_payment_and_one_receipt(
        self, signed_in: AsyncClient, db: AsyncSession, customer: Client
    ) -> None:
        form = await signed_in.get("/cobrar")
        fields = {
            "idempotency_key": key_of(form),
            "client_id": customer.id,
            "concept": "Instalación de contactos",
            "agreed_total": "2300",
            "amount": "1800",
        }

        first = await cobrar(signed_in, **fields)
        second = await cobrar(signed_in, **fields)

        assert first.status_code == second.status_code == 303
        assert first.headers["location"] == second.headers["location"], (
            "el segundo envío devuelve el mismo recibo"
        )

        assert await db.scalar(select(func.count(Payment.id))) == 1
        assert await db.scalar(select(func.count(Receipt.id))) == 1
        assert await db.scalar(select(func.count(Service.id))) == 1

    async def test_the_key_comes_from_the_server_so_it_survives_without_javascript(
        self, signed_in: AsyncClient
    ) -> None:
        """Con mala señal es justo el JavaScript lo que no llega a cargar."""
        first = await signed_in.get("/cobrar")
        second = await signed_in.get("/cobrar")

        assert key_of(first) != key_of(second), "cada pantalla trae una clave nueva"

    async def test_two_separate_charges_are_two_receipts(
        self, signed_in: AsyncClient, db: AsyncSession, customer: Client
    ) -> None:
        """Cobrar dos veces a propósito sigue siendo legítimo."""
        for amount in ("500", "300"):
            form = await signed_in.get("/cobrar")
            await cobrar(
                signed_in,
                idempotency_key=key_of(form),
                client_id=customer.id,
                concept=f"Trabajo de {amount}",
                amount=amount,
            )

        assert await db.scalar(select(func.count(Receipt.id))) == 2

    async def test_a_failed_charge_returns_the_same_key(
        self, signed_in: AsyncClient, customer: Client
    ) -> None:
        """Si el fallo fue de red y el cobro sí entró, el reintento lo detecta."""
        form = await signed_in.get("/cobrar")
        original = key_of(form)

        response = await cobrar(
            signed_in,
            idempotency_key=original,
            client_id=customer.id,
            concept="",  # sin concepto: falla la validación
            amount="500",
        )

        assert response.status_code == 400
        assert key_of(response) == original


class TestTheMomentOfTruth:
    """El caso completo de §1.4: cobrar en efectivo, de pie, en un minuto."""

    async def test_a_new_client_is_created_in_the_same_submission(
        self, signed_in: AsyncClient, db: AsyncSession
    ) -> None:
        """§7.2: se da de alta ahí mismo, dos campos, sin salir de la pantalla."""
        form = await signed_in.get("/cobrar")

        response = await cobrar(
            signed_in,
            idempotency_key=key_of(form),
            new_client_name="Luis Mendoza",
            new_client_phone="5511112222",
            concept="Cambio de apagador",
            amount="450",
        )

        assert response.status_code == 303
        created = await db.scalar(select(Client).where(Client.full_name == "Luis Mendoza"))
        assert created is not None
        assert created.phone == "5511112222"

    async def test_an_abandoned_form_leaves_no_orphan_client(
        self, signed_in: AsyncClient, db: AsyncSession
    ) -> None:
        """El alta va dentro de la transacción del cobro (§6), así que un envío
        que falla no ensucia el directorio."""
        form = await signed_in.get("/cobrar")

        response = await cobrar(
            signed_in,
            idempotency_key=key_of(form),
            new_client_name="Luis Mendoza",
            new_client_phone="5511112222",
            concept="Cambio de apagador",
            amount="",  # sin monto: falla
        )

        assert response.status_code == 400
        assert await db.scalar(select(func.count(Client.id))) == 0

    async def test_a_single_name_is_refused_with_a_readable_message(
        self, signed_in: AsyncClient
    ) -> None:
        form = await signed_in.get("/cobrar")

        response = await cobrar(
            signed_in,
            idempotency_key=key_of(form),
            new_client_name="Luis",
            new_client_phone="5511112222",
            concept="Cambio de apagador",
            amount="450",
        )

        assert response.status_code == 400
        assert "nombre y un apellido" in document_text(response)

    async def test_continuing_an_open_job_with_extra_work(
        self, signed_in: AsyncClient, scope: AccountScope, account: Account, customer: Client
    ) -> None:
        """El caso de `AC-05`, ahora desde la pantalla (RN-12, RN-16)."""
        first = await charge(
            scope,
            account,
            ChargeRequest(
                client_id=customer.id,
                concept="Instalación de contactos",
                agreed_total_cents=230000,
                payment_amount_cents=180000,
            ),
        )
        form = await signed_in.get("/cobrar")

        response = await cobrar(
            signed_in,
            idempotency_key=key_of(form),
            client_id=customer.id,
            service_id=first.service.id,
            adjustment_amount="100",
            adjustment_reason="Cambio de 2 contactos en recámara",
            amount="600",
        )

        assert response.status_code == 303
        totals = await load_totals(scope, first.service)
        assert totals.current_total_cents == 240000
        assert totals.balance_cents == 0
        assert first.service.status is ServiceStatus.SETTLED

    async def test_the_material_is_recorded_but_stays_out_of_the_receipt(
        self, signed_in: AsyncClient, db: AsyncSession, customer: Client
    ) -> None:
        """RN-14 desde la pantalla: el campo existe, el recibo no lo muestra."""
        form = await signed_in.get("/cobrar")

        response = await cobrar(
            signed_in,
            idempotency_key=key_of(form),
            client_id=customer.id,
            concept="Reparación de tablero",
            amount="1000",
            material="300",
        )

        assert response.status_code == 303
        payment = await db.scalar(select(Payment))
        assert payment is not None and payment.material_cents == 30000

        receipt = await db.scalar(select(Receipt))
        assert receipt is not None
        assert "material_cents" not in receipt.snapshot["amounts"]

    async def test_the_signature_is_stamped_within_the_same_submission(
        self, signed_in: AsyncClient, db: AsyncSession, customer: Client
    ) -> None:
        """§7.2, bloque 4: el interruptor y el canvas viven en la misma pantalla."""
        form = await signed_in.get("/cobrar")

        response = await cobrar(
            signed_in,
            idempotency_key=key_of(form),
            client_id=customer.id,
            concept="Instalación de contactos",
            amount="450",
            signature_data=a_signature(),
            signer_capacity=SignerCapacity.HOLDER.value,
        )

        assert response.status_code == 303
        receipt = await db.scalar(select(Receipt))
        assert receipt is not None
        assert receipt.client_signature_blob_path is not None
        assert receipt.client_signature_signer_name == "Ana Ramírez"

    async def test_a_replayed_submission_does_not_restamp_the_signature(
        self, signed_in: AsyncClient, db: AsyncSession, customer: Client
    ) -> None:
        """Reestampar escribiría un blob nuevo sin que nadie lo pidiera."""
        form = await signed_in.get("/cobrar")
        fields = {
            "idempotency_key": key_of(form),
            "client_id": customer.id,
            "concept": "Instalación de contactos",
            "amount": "450",
            "signature_data": a_signature(),
            "signer_capacity": SignerCapacity.HOLDER.value,
        }

        await cobrar(signed_in, **fields)
        receipt = await db.scalar(select(Receipt))
        assert receipt is not None
        first_path = receipt.client_signature_blob_path

        await cobrar(signed_in, **fields)

        assert receipt.client_signature_blob_path == first_path

    async def test_an_overpayment_is_refused_pointing_at_the_extra(
        self, signed_in: AsyncClient, scope: AccountScope, account: Account, customer: Client
    ) -> None:
        first = await charge(
            scope,
            account,
            ChargeRequest(
                client_id=customer.id,
                concept="Instalación",
                agreed_total_cents=230000,
                payment_amount_cents=180000,
            ),
        )
        form = await signed_in.get("/cobrar")

        response = await cobrar(
            signed_in,
            idempotency_key=key_of(form),
            client_id=customer.id,
            service_id=first.service.id,
            amount="900",
        )

        assert response.status_code == 400
        assert "monto extra" in document_text(response)

    async def test_it_requires_the_csrf_token(
        self, signed_in: AsyncClient, customer: Client
    ) -> None:
        response = await signed_in.post(
            "/cobrar", data={"client_id": str(customer.id), "amount": "450"}
        )
        assert response.status_code == 403


class TestMoneyIsTypedTheWayPeopleType:
    """El emisor teclea con el pulgar; no va a pelearse con el formato."""

    @pytest.mark.parametrize(
        ("typed", "cents"),
        [("2300", 230000), ("2,300", 230000), ("2300.50", 230050), ("$2,300.5", 230050)],
    )
    async def test_amounts_are_parsed_without_floats(
        self, signed_in: AsyncClient, db: AsyncSession, customer: Client, typed: str, cents: int
    ) -> None:
        form = await signed_in.get("/cobrar")

        await cobrar(
            signed_in,
            idempotency_key=key_of(form),
            client_id=customer.id,
            concept="Trabajo",
            amount=typed,
        )

        payment = await db.scalar(select(Payment))
        assert payment is not None and payment.amount_cents == cents

    async def test_gibberish_in_the_amount_is_a_readable_error(
        self, signed_in: AsyncClient, customer: Client
    ) -> None:
        form = await signed_in.get("/cobrar")

        response = await cobrar(
            signed_in,
            idempotency_key=key_of(form),
            client_id=customer.id,
            concept="Trabajo",
            amount="mucho",
        )

        assert response.status_code == 400
        assert "mayor que cero" in document_text(response)


class TestTheResultScreen:
    """§7.2: vista previa del recibo y tres acciones."""

    @pytest.fixture
    async def cobro(self, scope: AccountScope, account: Account, customer: Client):
        return await charge(
            scope,
            account,
            ChargeRequest(
                client_id=customer.id,
                concept="Instalación de contactos",
                agreed_total_cents=230000,
                payment_amount_cents=180000,
            ),
        )

    async def test_it_confirms_the_receipt_was_issued(self, signed_in: AsyncClient, cobro) -> None:
        response = await signed_in.get(f"/recibos/{cobro.receipt.id}?listo=1")

        assert response.status_code == 200
        assert "ya está emitido" in document_text(response)

    async def test_it_shows_the_receipt_preview(self, signed_in: AsyncClient, cobro) -> None:
        response = await signed_in.get(f"/recibos/{cobro.receipt.id}?listo=1")

        text = document_text(response)
        assert "Recibí de Ana Ramírez" in text
        assert "$1,800.00" in text

    async def test_it_offers_the_three_actions(self, signed_in: AsyncClient, cobro) -> None:
        response = await signed_in.get(f"/recibos/{cobro.receipt.id}?listo=1")

        text = document_text(response)
        assert "Compartir por WhatsApp" in text
        assert "Copiar liga" in text
        assert "Enviar por correo" in text
        assert "Descargar PDF" in text

    async def test_the_whatsapp_link_carries_the_public_url_and_the_amount(
        self, signed_in: AsyncClient, cobro
    ) -> None:
        """RN-32: se comparte desde el WhatsApp del emisor, con el mensaje escrito."""
        response = await signed_in.get(f"/recibos/{cobro.receipt.id}")

        assert "https://wa.me/525598765432?text=" in response.text
        assert cobro.receipt.public_token in response.text

    async def test_email_is_disabled_with_an_explanation_when_there_is_no_address(
        self, signed_in: AsyncClient, cobro
    ) -> None:
        """§7.2, textual: deshabilitado con explicación, no escondido."""
        response = await signed_in.get(f"/recibos/{cobro.receipt.id}")

        text = document_text(response)
        assert "no tiene correo registrado" in text
        assert "Enviar por correo" in text

    async def test_a_cancelled_receipt_offers_no_sharing(
        self, signed_in: AsyncClient, scope: AccountScope, cobro
    ) -> None:
        """Compartir un comprobante anulado solo puede causar un malentendido."""
        from receipt.services.receipts import cancel_receipt

        await cancel_receipt(scope, cobro.receipt, reason="Monto equivocado")

        response = await signed_in.get(f"/recibos/{cobro.receipt.id}")

        assert "Compartir por WhatsApp" not in document_text(response)

    async def test_the_payment_of_a_cancelled_receipt_is_cancelled_too(
        self, signed_in: AsyncClient, scope: AccountScope, cobro
    ) -> None:
        from receipt.services.receipts import cancel_receipt

        await cancel_receipt(scope, cobro.receipt, reason="Monto equivocado")
        assert cobro.payment.status is PaymentStatus.CANCELLED


class TestSwitchingTheTemplate:
    """§8.4: el emisor puede forzar la otra plantilla desde la vista previa."""

    @pytest.fixture
    async def cobro(self, scope: AccountScope, account: Account, customer: Client):
        return await charge(
            scope,
            account,
            ChargeRequest(
                client_id=customer.id,
                concept="Instalación de contactos",
                agreed_total_cents=230000,
                payment_amount_cents=180000,
            ),
        )

    async def test_it_switches_and_drops_the_cached_pdf(
        self, signed_in: AsyncClient, cobro
    ) -> None:
        assert cobro.receipt.template is ReceiptTemplate.DETAILED
        await signed_in.get(f"/recibos/{cobro.receipt.id}/pdf")
        assert cobro.receipt.pdf_blob_path is not None

        response = await signed_in.post(
            f"/recibos/{cobro.receipt.id}/plantilla",
            data={"template": "single", "csrf_token": csrf_token_of(signed_in)},
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert cobro.receipt.template is ReceiptTemplate.SINGLE
        assert cobro.receipt.pdf_blob_path is None

    async def test_switching_does_not_change_a_single_amount(
        self, signed_in: AsyncClient, cobro
    ) -> None:
        """RN-10: la plantilla es presentación, no contenido."""
        frozen = dict(cobro.receipt.snapshot)

        await signed_in.post(
            f"/recibos/{cobro.receipt.id}/plantilla",
            data={"template": "single", "csrf_token": csrf_token_of(signed_in)},
            follow_redirects=False,
        )

        assert cobro.receipt.snapshot == frozen

    async def test_an_invented_template_is_a_404(self, signed_in: AsyncClient, cobro) -> None:
        response = await signed_in.post(
            f"/recibos/{cobro.receipt.id}/plantilla",
            data={"template": "inventada", "csrf_token": csrf_token_of(signed_in)},
        )
        assert response.status_code == 404
