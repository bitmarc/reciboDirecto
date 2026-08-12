"""Constructores de datos para las pruebas.

Devuelven entidades ya persistidas con valores mínimos válidos, para que cada
prueba solo tenga que declarar lo que de verdad le importa.
"""

import secrets
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from receipt.domain.clock import now_utc
from receipt.domain.enums import (
    CounterKind,
    PaymentMethod,
    ReceiptTemplate,
    ServiceStatus,
)
from receipt.models import (
    Account,
    AccountCounter,
    Client,
    Payment,
    Receipt,
    Service,
    ServiceAdjustment,
    User,
)
from receipt.security.passwords import hash_password


async def make_account(
    db: AsyncSession,
    *,
    display_name: str = "Juan Pérez",
    phone: str = "5512345678",
    default_city: str = "Ciudad de México",
    occupation: str | None = "Electricista",
) -> Account:
    account = Account(
        display_name=display_name,
        phone=phone,
        default_city=default_city,
        occupation=occupation,
    )
    db.add(account)
    await db.flush()
    db.add_all(
        AccountCounter(account_id=account.id, kind=kind, next_value=1) for kind in CounterKind
    )
    await db.flush()
    return account


async def make_user(
    db: AsyncSession,
    account: Account,
    *,
    email: str | None = None,
    password: str = "contrasena-de-prueba",
) -> User:
    user = User(
        account_id=account.id,
        email=email or f"emisor-{secrets.token_hex(4)}@example.mx",
        password_hash=hash_password(password),
    )
    db.add(user)
    await db.flush()
    return user


async def make_client(
    db: AsyncSession,
    account: Account,
    *,
    full_name: str = "Ana Ramírez",
    phone: str = "5598765432",
    email: str | None = None,
) -> Client:
    client = Client(account_id=account.id, full_name=full_name, phone=phone, email=email)
    db.add(client)
    await db.flush()
    return client


async def make_service(
    db: AsyncSession,
    account: Account,
    client: Client,
    *,
    code: str = "SRV-0001",
    concept: str = "Instalación de contactos",
    agreed_total_cents: int | None = 230000,
    created_at: datetime | None = None,
) -> Service:
    """`created_at` se puede fijar porque la antigüedad de un adeudo (§7.4) se
    cuenta desde ahí, y una prueba de orden necesita trabajos de fechas
    distintas sin esperar días."""
    service = Service(
        account_id=account.id,
        client_id=client.id,
        code=code,
        concept=concept,
        agreed_total_cents=agreed_total_cents,
        status=ServiceStatus.OPEN,
    )
    if created_at is not None:
        service.created_at = created_at
        service.updated_at = created_at
    db.add(service)
    await db.flush()
    return service


async def make_adjustment(
    db: AsyncSession,
    service: Service,
    *,
    amount_cents: int = 10000,
    reason: str = "Cambio de 2 contactos en recámara",
) -> ServiceAdjustment:
    adjustment = ServiceAdjustment(service_id=service.id, amount_cents=amount_cents, reason=reason)
    db.add(adjustment)
    await db.flush()
    return adjustment


async def make_payment(
    db: AsyncSession,
    service: Service,
    *,
    amount_cents: int = 180000,
    material_cents: int = 0,
    method: PaymentMethod = PaymentMethod.CASH,
    paid_at: datetime | None = None,
) -> Payment:
    """`paid_at` es la fecha de corte contable (RN-13, §9.1), así que las
    pruebas del mes —`AC-07` entre ellas— tienen que poder fijarla."""
    payment = Payment(
        service_id=service.id,
        amount_cents=amount_cents,
        material_cents=material_cents,
        method=method,
        paid_at=paid_at or now_utc(),
    )
    db.add(payment)
    await db.flush()
    return payment


async def make_receipt(
    db: AsyncSession,
    account: Account,
    payment: Payment,
    *,
    folio: str = "RB-0001",
    template: ReceiptTemplate = ReceiptTemplate.DETAILED,
) -> Receipt:
    receipt = Receipt(
        account_id=account.id,
        payment_id=payment.id,
        folio=folio,
        public_token=secrets.token_urlsafe(32),
        template=template,
        snapshot={},
        issued_at=now_utc(),
    )
    db.add(receipt)
    await db.flush()
    return receipt
