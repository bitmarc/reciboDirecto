"""Planes y límites — `AC-15` y `AC-17` de extremo a extremo (E8, §11).

Dos criterios tiran en direcciones opuestas y por eso conviene probarlos juntos:
`AC-15` exige que el muro **bloquee**, y `AC-17` que bajar de plan **no quite
nada de lo ya hecho**. Un límite que se lleva por delante el historial no es un
límite, es una pérdida de datos.

No hay pasarela (§11.4, §14.5 sigue abierta): el plan de pago se concede con un
cupón de meses gratis o a mano desde la base, así que aquí se prueban esas dos
vías y no un checkout.
"""

from datetime import datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from receipt.domain.clock import LOCAL_TZ, current_period, now_utc, shift_period
from receipt.domain.enums import DiscountType, Plan
from receipt.models import Account, Client, CouponRedemption, PlanBlockEvent, Receipt
from receipt.repositories import AccountScope
from receipt.services.charging import ChargeRequest, charge
from receipt.services.errors import BusinessRuleViolation, ConflictError, ValidationError
from receipt.services.plans import PlanLimitReached, allowance_of, redeem_coupon
from receipt.services.receipts import cancel_receipt
from receipt.services.usage import record_issuance
from tests.conftest import csrf_token_of, document_text, visible_text
from tests.factories import make_client, make_coupon

pytestmark = pytest.mark.db


@pytest.fixture
async def account(signed_in: AsyncClient, db: AsyncSession) -> Account:
    stored = await db.scalar(select(Account))
    assert stored is not None
    return stored


@pytest.fixture
async def scope(db: AsyncSession, account: Account) -> AccountScope:
    return AccountScope(db, account.id)


@pytest.fixture
async def customer(db: AsyncSession, account: Account) -> Client:
    return await make_client(db, account, full_name="Ana Ramírez", phone="5598765432")


async def make_paid(db: AsyncSession, account: Account, *, days: int = 30) -> Account:
    """Activación manual del plan, tal como propone §11.4 para la validación."""
    account.plan = Plan.PAID
    account.plan_valid_until = now_utc() + timedelta(days=days)
    await db.flush()
    return account


async def burn_quota(db: AsyncSession, account: Account, count: int) -> None:
    """Consume cupo sin emitir recibos de verdad.

    El contador **es** la fuente de RN-33 —lo dice §4.2 y por eso los cancelados
    también suman—, así que para las pruebas que no miran el recibo, avanzarlo
    directamente prueba lo mismo y no tarda diez cobros.
    """
    period = (await allowance_of(db, account)).period
    for _ in range(count):
        await record_issuance(db, account.id, period)


async def charge_form(http: AsyncClient, **overrides: str) -> dict[str, str]:
    return {
        "new_client_name": "Rosa Delgado",
        "new_client_phone": "5533334444",
        "concept": "Cambio de apagador",
        "amount": "450",
        "csrf_token": csrf_token_of(http),
    } | overrides


# ---------------------------------------------------------------------------
# AC-15 — el muro, y su instrumentación
# ---------------------------------------------------------------------------


class TestAc15TheFreeWall:
    """La cuenta gratuita en su recibo número 11 del mes recibe bloqueo."""

    async def test_the_first_ten_receipts_go_through(
        self, scope: AccountScope, account: Account, customer: Client
    ) -> None:
        for index in range(10):
            result = await charge(
                scope,
                account,
                ChargeRequest(
                    client_id=customer.id,
                    concept=f"Trabajo {index + 1}",
                    payment_amount_cents=10000,
                ),
            )
            assert result.receipt.folio

    async def test_the_eleventh_is_refused(
        self, db: AsyncSession, scope: AccountScope, account: Account, customer: Client
    ) -> None:
        await burn_quota(db, account, 10)

        with pytest.raises(PlanLimitReached) as raised:
            await charge(
                scope,
                account,
                ChargeRequest(client_id=customer.id, concept="El once", payment_amount_cents=10000),
            )

        assert raised.value.rule == "RN-33"
        assert raised.value.allowance.attempted_number == 11

    async def test_the_refused_charge_writes_nothing(
        self, db: AsyncSession, signed_in: AsyncClient, account: Account
    ) -> None:
        """§1.4: el emisor está de pie con el efectivo en la mano.

        El rechazo no puede dejarle medio cobro en el sistema: ni un cliente
        suelto en el directorio, ni un trabajo, ni un pago. Cuando se suscriba,
        vuelve a llenar el formulario y ya está.
        """
        await burn_quota(db, account, 10)
        before = await db.scalar(select(func.count(Client.id)))

        response = await signed_in.post(
            "/cobrar", data=await charge_form(signed_in), follow_redirects=False
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/planes?bloqueo=1"
        assert await db.scalar(select(func.count(Client.id))) == before
        assert await db.scalar(select(func.count(Receipt.id))) == 0

    async def test_the_block_is_recorded_with_account_date_and_number(
        self, db: AsyncSession, signed_in: AsyncClient, account: Account
    ) -> None:
        """§11.2 declara esta instrumentación obligatoria.

        Y tiene que sobrevivir al rechazo del cobro: si el evento se insertara
        dentro de la transacción que se descarta, se iría con ella y el dato que
        permite revisar §14.3 no existiría nunca.
        """
        await burn_quota(db, account, 10)

        await signed_in.post("/cobrar", data=await charge_form(signed_in), follow_redirects=False)

        events = (await db.scalars(select(PlanBlockEvent))).all()
        assert len(events) == 1
        assert events[0].account_id == account.id
        assert events[0].attempted_receipt_number == 11
        assert events[0].occurred_at is not None

    async def test_every_attempt_leaves_its_own_event(
        self, db: AsyncSession, signed_in: AsyncClient, account: Account
    ) -> None:
        """Insistir es la señal: dice cuánto le estorbó el muro, no solo que topó."""
        await burn_quota(db, account, 10)

        for _ in range(3):
            await signed_in.post(
                "/cobrar", data=await charge_form(signed_in), follow_redirects=False
            )

        assert await db.scalar(select(func.count(PlanBlockEvent.id))) == 3

    async def test_the_charge_screen_sends_him_to_the_plans_page(
        self, db: AsyncSession, signed_in: AsyncClient, account: Account
    ) -> None:
        """No se le deja llenar el formulario para negárselo al final."""
        await burn_quota(db, account, 10)

        response = await signed_in.get("/cobrar", follow_redirects=False)

        assert response.status_code == 303
        assert response.headers["location"] == "/planes?bloqueo=1"

    async def test_ac16_cancelling_does_not_free_quota(
        self, db: AsyncSession, scope: AccountScope, account: Account, customer: Client
    ) -> None:
        """RN-33: si cancelar devolviera cupo, el límite sería evadible."""
        await burn_quota(db, account, 9)
        tenth = await charge(
            scope,
            account,
            ChargeRequest(client_id=customer.id, concept="El diez", payment_amount_cents=10000),
        )
        await cancel_receipt(scope, tenth.receipt, reason="Me equivoqué de monto")

        with pytest.raises(PlanLimitReached):
            await charge(
                scope,
                account,
                ChargeRequest(client_id=customer.id, concept="El once", payment_amount_cents=10000),
            )

    async def test_a_paid_account_has_no_wall(
        self, db: AsyncSession, scope: AccountScope, account: Account, customer: Client
    ) -> None:
        await burn_quota(db, account, 40)
        await make_paid(db, account)

        result = await charge(
            scope,
            account,
            ChargeRequest(client_id=customer.id, concept="Sin tope", payment_amount_cents=10000),
        )

        assert result.receipt.folio

    async def test_a_retry_of_an_already_issued_charge_is_never_blocked(
        self, db: AsyncSession, scope: AccountScope, account: Account, customer: Client
    ) -> None:
        """AC-18 manda sobre RN-33 en el reenvío.

        El décimo recibo agota el cupo. Si el emisor pierde la señal y su
        celular reintenta, tiene que recuperar **su** recibo, no toparse un muro
        que él mismo acaba de levantar.
        """
        await burn_quota(db, account, 9)
        first = await charge(
            scope,
            account,
            ChargeRequest(
                client_id=customer.id,
                concept="El diez",
                payment_amount_cents=10000,
                idempotency_key="clave-del-doble-toque",
            ),
        )

        again = await charge(
            scope,
            account,
            ChargeRequest(
                client_id=customer.id,
                concept="El diez",
                payment_amount_cents=10000,
                idempotency_key="clave-del-doble-toque",
            ),
        )

        assert again.replayed
        assert again.receipt.id == first.receipt.id


class TestTheWarningBeforeTheWall:
    """§14.3: enterarse con el efectivo en la mano produce abandono, no conversión."""

    async def test_the_charge_screen_warns_from_the_ninth(
        self, db: AsyncSession, signed_in: AsyncClient, account: Account
    ) -> None:
        await burn_quota(db, account, 8)

        response = await signed_in.get("/cobrar")

        assert "Te quedan 2 recibos este mes" in visible_text(response)

    async def test_it_stays_quiet_earlier(
        self, db: AsyncSession, signed_in: AsyncClient, account: Account
    ) -> None:
        await burn_quota(db, account, 5)

        response = await signed_in.get("/cobrar")

        assert "Te quedan" not in visible_text(response)

    async def test_the_singular_reads_naturally(
        self, db: AsyncSession, signed_in: AsyncClient, account: Account
    ) -> None:
        await burn_quota(db, account, 9)

        response = await signed_in.get("/cobrar")

        assert "Te quedan 1 recibo este mes" in visible_text(response)

    async def test_a_paid_account_is_never_warned(
        self, db: AsyncSession, signed_in: AsyncClient, account: Account
    ) -> None:
        await burn_quota(db, account, 40)
        await make_paid(db, account)

        response = await signed_in.get("/cobrar")

        assert "Te quedan" not in visible_text(response)


# ---------------------------------------------------------------------------
# AC-17 — bajar de plan no quita lo ya hecho
# ---------------------------------------------------------------------------


class TestAc17DowngradeKeepsTheHistory:
    """Una cuenta que baja a gratuito conserva lectura de todo su historial."""

    @pytest.fixture
    async def history(
        self, db: AsyncSession, scope: AccountScope, account: Account, customer: Client
    ):
        """Un recibo de hace cuatro meses, emitido en plan de pago."""
        await make_paid(db, account)
        old = await charge(
            scope,
            account,
            ChargeRequest(
                client_id=customer.id,
                concept="Cableado de bodega",
                agreed_total_cents=400000,
                payment_amount_cents=120000,
                paid_at=now_utc() - timedelta(days=120),
            ),
        )
        # Baja a gratuito: lo emitido no se toca.
        account.plan = Plan.FREE
        account.plan_valid_until = None
        await db.flush()
        return old

    async def test_the_receipt_history_is_still_complete(
        self, signed_in: AsyncClient, history
    ) -> None:
        response = await signed_in.get("/recibos")

        text = visible_text(response)
        assert history.receipt.folio in text
        assert "Ana Ramírez" in text

    async def test_the_old_receipt_still_opens(self, signed_in: AsyncClient, history) -> None:
        response = await signed_in.get(f"/recibos/{history.receipt.id}")

        assert response.status_code == 200
        assert "Cableado de bodega" in visible_text(response)

    async def test_the_job_and_the_client_are_still_there(
        self, signed_in: AsyncClient, history
    ) -> None:
        jobs = await signed_in.get("/trabajos")
        clients = await signed_in.get("/clientes")

        assert "Cableado de bodega" in visible_text(jobs)
        assert "Ana Ramírez" in visible_text(clients)

    async def test_rn31_the_public_link_keeps_working(self, client: AsyncClient, history) -> None:
        """Un comprobante nunca deja de existir porque el emisor dejó de pagar."""
        response = await client.get(f"/r/{history.receipt.public_token}")

        assert response.status_code == 200
        assert "Cableado de bodega" in visible_text(response)

    async def test_the_pdf_is_still_downloadable(self, signed_in: AsyncClient, history) -> None:
        response = await signed_in.get(f"/recibos/{history.receipt.id}/pdf")

        assert response.status_code == 200
        assert response.content.startswith(b"%PDF")

    async def test_what_is_limited_is_only_the_derived_view(
        self, signed_in: AsyncClient, history
    ) -> None:
        """RN-35: se acotan analítica y gráficos, nunca el historial."""
        report = await signed_in.get(f"/reportes?periodo={shift_period(current_period(), -4)}")

        assert report.status_code == 402
        assert "Esto es del plan de pago" in visible_text(report)
        assert "siguen completos" in visible_text(report)


# ---------------------------------------------------------------------------
# RN-35 — la ventana de analítica
# ---------------------------------------------------------------------------


class TestTheAnalyticsWindow:
    async def test_a_free_account_sees_only_the_current_month_in_the_chart(
        self, signed_in: AsyncClient
    ) -> None:
        response = await signed_in.get("/")

        assert response.text.count('class="chart__row') == 1
        assert "verías 6 meses de comparación" in visible_text(response)

    async def test_a_paid_account_sees_the_six_months_of_section_7_4(
        self, db: AsyncSession, signed_in: AsyncClient, account: Account
    ) -> None:
        await make_paid(db, account)

        response = await signed_in.get("/")

        assert response.text.count('class="chart__row') == 6
        assert "verías" not in visible_text(response)

    async def test_the_current_month_report_is_free(self, signed_in: AsyncClient) -> None:
        """RN-35 acota la ventana, no la existencia del reporte."""
        response = await signed_in.get("/reportes")

        assert response.status_code == 200

    async def test_a_past_month_asks_for_the_paid_plan(self, signed_in: AsyncClient) -> None:
        response = await signed_in.get("/reportes?periodo=2020-01")

        assert response.status_code == 402
        assert "Ver los planes" in visible_text(response)

    async def test_a_paid_account_reaches_any_month(
        self, db: AsyncSession, signed_in: AsyncClient, account: Account
    ) -> None:
        await make_paid(db, account)

        response = await signed_in.get("/reportes?periodo=2020-01")

        assert response.status_code == 200

    async def test_the_csv_is_a_paid_feature(self, signed_in: AsyncClient) -> None:
        response = await signed_in.get("/reportes/mensual.csv")

        assert response.status_code == 402
        assert "text/csv" not in response.headers["content-type"]

    async def test_a_paid_account_downloads_the_csv(
        self, db: AsyncSession, signed_in: AsyncClient, account: Account
    ) -> None:
        await make_paid(db, account)

        response = await signed_in.get("/reportes/mensual.csv")

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/csv")

    async def test_the_report_screen_hides_the_download_when_it_is_locked(
        self, signed_in: AsyncClient
    ) -> None:
        """Un botón que siempre rebota es peor que no tener botón."""
        response = await signed_in.get("/reportes")

        assert "mensual.csv" not in response.text


# ---------------------------------------------------------------------------
# RN-34 — el logo mira la vigencia, no la columna
# ---------------------------------------------------------------------------


class TestTheLogoGate:
    async def test_a_free_account_is_not_offered_the_logo(self, signed_in: AsyncClient) -> None:
        response = await signed_in.get("/ajustes")

        assert "Subir logo" not in visible_text(response)

    async def test_a_paid_account_is(
        self, db: AsyncSession, signed_in: AsyncClient, account: Account
    ) -> None:
        await make_paid(db, account)

        response = await signed_in.get("/ajustes")

        assert "Subir logo" in visible_text(response)

    async def test_an_expired_paid_account_is_not(
        self, db: AsyncSession, signed_in: AsyncClient, account: Account
    ) -> None:
        """La columna dice `paid` pero la fecha ya pasó.

        Ofrecerle subir un logo que el recibo no estamparía sería mentirle: la
        emisión evalúa la vigencia, y esta pantalla tiene que evaluar lo mismo.
        """
        account.plan = Plan.PAID
        account.plan_valid_until = now_utc() - timedelta(days=1)
        await db.flush()

        response = await signed_in.get("/ajustes")

        assert "Subir logo" not in visible_text(response)


# ---------------------------------------------------------------------------
# §11.3 — cupones
# ---------------------------------------------------------------------------


class TestCoupons:
    async def test_a_free_months_coupon_grants_the_paid_plan(
        self, db: AsyncSession, scope: AccountScope, account: Account
    ) -> None:
        await make_coupon(db, code="GRACIAS3", discount_value=3)
        moment = datetime(2026, 8, 12, 12, 0, tzinfo=LOCAL_TZ)

        result = await redeem_coupon(scope, account, "gracias3", at=moment)

        assert account.plan is Plan.PAID
        assert result.months_granted == 3
        assert result.valid_until.astimezone(LOCAL_TZ).date().isoformat() == "2026-11-12"

    async def test_the_code_is_read_the_way_it_is_dictated(
        self, db: AsyncSession, scope: AccountScope, account: Account
    ) -> None:
        """Se dicta por WhatsApp: ni el espacio ni la caja pueden estorbar."""
        await make_coupon(db, code="GRACIAS3")

        await redeem_coupon(scope, account, "  gra cias 3 ")

        assert account.plan is Plan.PAID

    async def test_the_months_add_to_the_running_subscription(
        self, db: AsyncSession, scope: AccountScope, account: Account
    ) -> None:
        """Canjear a mitad de una suscripción no puede borrar lo ya pagado."""
        moment = datetime(2026, 8, 12, 12, 0, tzinfo=LOCAL_TZ)
        account.plan = Plan.PAID
        account.plan_valid_until = datetime(2026, 10, 12, 12, 0, tzinfo=LOCAL_TZ)
        await db.flush()
        await make_coupon(db, code="GRACIAS3", discount_value=3)

        result = await redeem_coupon(scope, account, "GRACIAS3", at=moment)

        assert result.valid_until.astimezone(LOCAL_TZ).date().isoformat() == "2027-01-12"

    async def test_an_expired_subscription_starts_counting_from_today(
        self, db: AsyncSession, scope: AccountScope, account: Account
    ) -> None:
        moment = datetime(2026, 8, 12, 12, 0, tzinfo=LOCAL_TZ)
        account.plan = Plan.PAID
        account.plan_valid_until = datetime(2026, 1, 1, tzinfo=LOCAL_TZ)
        await db.flush()
        await make_coupon(db, code="VUELVE", discount_value=1)

        result = await redeem_coupon(scope, account, "VUELVE", at=moment)

        assert result.valid_until.astimezone(LOCAL_TZ).date().isoformat() == "2026-09-12"

    async def test_the_same_code_cannot_be_redeemed_twice(
        self, db: AsyncSession, scope: AccountScope, account: Account
    ) -> None:
        await make_coupon(db, code="GRACIAS3", discount_value=3)
        await redeem_coupon(scope, account, "GRACIAS3")

        with pytest.raises(ConflictError, match="Ya canjeaste"):
            await redeem_coupon(scope, account, "GRACIAS3")

    async def test_the_refused_second_redemption_leaves_the_session_usable(
        self, db: AsyncSession, scope: AccountScope, account: Account
    ) -> None:
        """El choque contra el índice único se atiende, no se arrastra.

        Si el canje rechazado dejara su fila pendiente en la sesión, el
        siguiente flush la reintentaría y reventaría con `PendingRollbackError`
        por un error que ya se había resuelto. Es lo que pasa cuando el `add`
        se hace fuera del savepoint.
        """
        await make_coupon(db, code="GRACIAS3", discount_value=3)
        await redeem_coupon(scope, account, "GRACIAS3")
        with pytest.raises(ConflictError):
            await redeem_coupon(scope, account, "GRACIAS3")

        assert await db.scalar(select(func.count(CouponRedemption.id))) == 1
        assert await db.scalar(select(func.count(Account.id))) >= 1

    async def test_an_unknown_code_is_refused(self, scope: AccountScope, account: Account) -> None:
        with pytest.raises(ValidationError, match="no es válido"):
            await redeem_coupon(scope, account, "INVENTADO")

    async def test_an_inactive_code_is_indistinguishable_from_a_missing_one(
        self, db: AsyncSession, scope: AccountScope, account: Account
    ) -> None:
        """Distinguirlos convierte el formulario en un oráculo de códigos."""
        await make_coupon(db, code="APAGADO", active=False)

        with pytest.raises(ValidationError, match="no es válido"):
            await redeem_coupon(scope, account, "APAGADO")

    async def test_an_expired_code_is_refused(
        self, db: AsyncSession, scope: AccountScope, account: Account
    ) -> None:
        await make_coupon(db, code="VENCIDO", valid_until=now_utc() - timedelta(days=1))

        with pytest.raises(ValidationError, match="venció"):
            await redeem_coupon(scope, account, "VENCIDO")

    async def test_an_exhausted_code_is_refused(
        self, db: AsyncSession, scope: AccountScope, account: Account
    ) -> None:
        from tests.factories import make_account

        coupon = await make_coupon(db, code="PRIMEROS", max_redemptions=1)
        neighbour = await make_account(db, person_name="Otro emisor")
        await redeem_coupon(AccountScope(db, neighbour.id), neighbour, "PRIMEROS")
        assert coupon.max_redemptions == 1

        with pytest.raises(ConflictError, match="se agotó"):
            await redeem_coupon(scope, account, "PRIMEROS")

    async def test_a_discount_coupon_explains_that_there_is_no_checkout_yet(
        self, db: AsyncSession, scope: AccountScope, account: Account
    ) -> None:
        """§11.3 los define, pero sin pasarela no hay precio que descontar.

        Se rechaza con una explicación en lugar de guardar un descuento que
        nadie puede aplicar: eso sería el andamiaje preparatorio de §2.2.
        """
        await make_coupon(db, code="MITAD", discount_type=DiscountType.PERCENT, discount_value=50)

        with pytest.raises(BusinessRuleViolation, match="todavía no hay"):
            await redeem_coupon(scope, account, "MITAD")

        assert account.plan is Plan.FREE

    async def test_a_coupon_of_another_account_does_not_count_as_mine(
        self, db: AsyncSession, scope: AccountScope, account: Account
    ) -> None:
        """AC-13: el canje del vecino no puede bloquear ni conceder el mío."""
        from tests.factories import make_account

        await make_coupon(db, code="COMPARTIDO", discount_value=1)
        neighbour = await make_account(db, person_name="Otro emisor")
        await redeem_coupon(AccountScope(db, neighbour.id), neighbour, "COMPARTIDO")

        result = await redeem_coupon(scope, account, "COMPARTIDO")

        assert result.months_granted == 1
        assert account.plan is Plan.PAID


class TestTheCouponFormOnTheWeb:
    async def test_it_redeems_and_says_so(
        self, db: AsyncSession, signed_in: AsyncClient, account: Account
    ) -> None:
        await make_coupon(db, code="GRACIAS3", discount_value=3)

        response = await signed_in.post(
            "/planes/cupon",
            data={"code": "GRACIAS3", "csrf_token": csrf_token_of(signed_in)},
            follow_redirects=True,
        )

        assert response.status_code == 200
        assert "3 meses de plan de pago" in visible_text(response)
        assert account.plan is Plan.PAID

    async def test_a_bad_code_comes_back_with_the_reason(self, signed_in: AsyncClient) -> None:
        response = await signed_in.post(
            "/planes/cupon",
            data={"code": "NOEXISTE", "csrf_token": csrf_token_of(signed_in)},
            follow_redirects=True,
        )

        assert "no es válido" in visible_text(response)

    async def test_it_requires_the_csrf_token(self, signed_in: AsyncClient) -> None:
        response = await signed_in.post("/planes/cupon", data={"code": "GRACIAS3"})
        assert response.status_code == 403


class TestThePlansScreen:
    async def test_it_shows_the_consumption_of_the_month(
        self, db: AsyncSession, signed_in: AsyncClient, account: Account
    ) -> None:
        await burn_quota(db, account, 4)

        response = await signed_in.get("/planes")

        # `document_text` y no `visible_text`: el denominador va en su propio
        # `<span>` y la frase solo se lee entera sin etiquetas de por medio.
        text = document_text(response)
        assert "4 de 10" in text
        assert "Te quedan 6 este mes" in text

    async def test_the_block_screen_explains_the_wall_and_what_survives(
        self, db: AsyncSession, signed_in: AsyncClient, account: Account
    ) -> None:
        await burn_quota(db, account, 10)

        response = await signed_in.get("/planes?bloqueo=1")

        text = visible_text(response)
        assert "Llegaste a los 10 recibos de este mes" in text
        assert "el contador se reinicia el mes que entra" in text
        # RN-31: lo emitido nunca deja de existir por dejar de pagar.
        assert "sus ligas y sus PDF siguen funcionando" in text

    async def test_there_is_no_checkout_button_yet(self, signed_in: AsyncClient) -> None:
        """§11.4: la pasarela no se construye antes de saber si alguien paga."""
        response = await signed_in.get("/planes")

        assert "Todavía no tenemos pantalla de cobro en línea" in visible_text(response)

    async def test_a_paid_account_sees_its_expiry(
        self, db: AsyncSession, signed_in: AsyncClient, account: Account
    ) -> None:
        await make_paid(db, account, days=45)

        response = await signed_in.get("/planes")

        text = visible_text(response)
        assert "Sin límite" in text
        assert "Vigente hasta" in text
