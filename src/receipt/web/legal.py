"""Contenido del aviso de privacidad que no es prosa (§10).

Vive aquí y no dentro de la plantilla porque el **inventario de datos** es lo
único del aviso que puede volverse falso en silencio. El resto del texto se
queda viejo de formas que se notan al leerlo; el inventario no: el día que
alguien añada una columna con un dato personal nuevo, la página seguirá
diciendo exactamente lo mismo y habrá dejado de ser cierta.

Declarado como datos, `tests/unit/test_legal_inventory.py` puede recorrer el
esquema real y fallar cuando aparezca una columna que nadie clasificó. Esa
prueba es el motivo de que este archivo exista.

Módulo hermano de `labels.py`, que es donde ya viven las constantes de
presentación.
"""

from dataclasses import dataclass

from receipt.config import get_settings

# Fecha de la versión del documento. El art. 15 de la LFPDPPP exige informar el
# procedimiento para comunicar cambios, y sin una fecha visible el emisor no
# tiene forma de saber si lo que leyó hace meses sigue vigente.
AVISO_VIGENTE_DESDE = "14 de agosto de 2026"


@dataclass(frozen=True)
class Identidad:
    """Quién responde. El art. 15 lo exige y sale de la configuración."""

    responsable: str
    domicilio: str
    contacto: str

    @property
    def completa(self) -> bool:
        """En desarrollo los tres pueden venir vacíos; en producción no arranca."""
        return all((self.responsable, self.domicilio, self.contacto))


def identidad() -> Identidad:
    settings = get_settings()
    return Identidad(
        responsable=settings.legal_responsable.strip(),
        domicilio=settings.legal_domicilio.strip(),
        contacto=settings.legal_contacto_arco.strip(),
    )


@dataclass(frozen=True)
class Categoria:
    """Un grupo de datos personales, tal como se le explica a una persona.

    `columnas` no se muestra: es el puente con el esquema que permite probar
    que el inventario sigue completo.
    """

    titulo: str
    para_que: str
    datos: tuple[str, ...]
    columnas: frozenset[str]


# El emisor es el titular de lo que hay aquí, y la plataforma su responsable.
DEL_EMISOR: tuple[Categoria, ...] = (
    Categoria(
        titulo="Los de tu cuenta",
        para_que=(
            "Identificarte al entrar, ponerte como emisor en cada comprobante y "
            "poder devolverte el acceso si olvidas la contraseña."
        ),
        datos=(
            "Nombre o razón social",
            "Correo electrónico",
            "Teléfono",
            "Ciudad",
            "Oficio, si lo capturas",
            "Tu contraseña, guardada como huella irreversible (Argon2id), nunca en claro",
        ),
        columnas=frozenset(
            {
                "account.display_name",
                "account.phone",
                "account.occupation",
                "account.default_city",
                "app_user.email",
                "app_user.password_hash",
                "app_user.email_verified_at",
            }
        ),
    ),
    Categoria(
        titulo="Tu firma y tu logo",
        para_que=(
            "Estamparlos en los comprobantes que emites, sin que tengas que "
            "dibujar la firma cada vez."
        ),
        datos=(
            "La imagen de tu firma, capturada una sola vez",
            "El archivo de tu logo, si subes uno",
        ),
        columnas=frozenset({"account.signature_blob_path", "account.logo_blob_path"}),
    ),
    Categoria(
        titulo="Tu plan",
        para_que="Saber qué funciones tienes disponibles y hasta cuándo.",
        datos=("El plan que tienes", "La fecha hasta la que está vigente"),
        columnas=frozenset({"account.plan", "account.plan_valid_until"}),
    ),
)

# De esto el responsable es el emisor, y la plataforma solo su encargada.
DE_LOS_CLIENTES: tuple[Categoria, ...] = (
    Categoria(
        titulo="Los de tus clientes",
        para_que=(
            "Poder ponerlos en el comprobante, encontrarlos la próxima vez y "
            "enviarles su recibo si te dieron correo."
        ),
        datos=("Nombre completo", "Teléfono", "Correo electrónico, si lo capturas"),
        columnas=frozenset({"client.full_name", "client.phone", "client.email"}),
    ),
    Categoria(
        titulo="Los trabajos",
        para_que="Llevar la cuenta de lo acordado, lo cobrado y lo que falta.",
        datos=(
            "El concepto del trabajo",
            "La dirección o el alias del lugar donde se hizo",
            "El motivo, cuando cierras un trabajo como no cobrado",
            "El motivo de cada ajuste al total acordado",
        ),
        columnas=frozenset(
            {
                "service.concept",
                "service.location_alias",
                "service.location_address",
                "service.closed_reason",
                "service_adjustment.reason",
            }
        ),
    ),
    Categoria(
        titulo="Los cobros",
        para_que="Emitir el comprobante y darte el panel de ingresos y de por cobrar.",
        datos=(
            "Los montos acordados, cobrados y de material",
            "La forma de pago",
            "La fecha en que se cobró",
            "Los ajustes al total y lo que quedó sin cobrar",
        ),
        columnas=frozenset(
            {
                "payment.amount_cents",
                "payment.material_cents",
                "payment.method",
                "payment.paid_at",
                "service.agreed_total_cents",
                "service.uncollected_cents",
                "service_adjustment.amount_cents",
            }
        ),
    ),
    Categoria(
        titulo="Los comprobantes ya emitidos",
        para_que=(
            "Que el comprobante siga diciendo lo mismo dentro de dos años, que es "
            "lo único que lo hace servir de prueba."
        ),
        datos=(
            "Una copia congelada de todo lo anterior, tal como estaba al emitirlo",
            "La firma de quien recibió, si la pediste",
            "El nombre de quien firmó y en qué calidad lo hizo",
            "El motivo de la cancelación, si lo cancelaste",
            "El archivo PDF del comprobante",
        ),
        columnas=frozenset(
            {
                "receipt.snapshot",
                "receipt.client_signature_blob_path",
                "receipt.client_signature_signer_name",
                "receipt.client_signature_signer_capacity",
                "receipt.client_signature_signed_at",
                "receipt.cancellation_reason",
                "receipt.pdf_blob_path",
            }
        ),
    ),
)

INVENTARIO: tuple[Categoria, ...] = DEL_EMISOR + DE_LOS_CLIENTES

# Datos que no salen de ninguna tabla y que el aviso tiene que declarar igual.
TECNICOS: tuple[str, ...] = (
    "Una cookie de sesión, firmada, para no pedirte la contraseña en cada pantalla.",
    "Tu dirección IP, solo mientras dura el límite de intentos que protege el "
    "inicio de sesión y la liga pública.",
)

# Columnas de las tablas revisadas que **no** son datos personales. Se declaran
# una por una en vez de dar por hecho que "lo que no está, no importa": obligar
# a clasificar cada columna nueva es justo lo que hace útil a la prueba.
NO_PERSONALES: frozenset[str] = frozenset(
    {
        "client.archived_at",  # marca de archivado, no dice nada de la persona
        "service.code",  # folio interno del trabajo
        "service.status",
        "service.closed_at",
        "payment.status",
        "receipt.folio",
        "receipt.public_token",  # credencial de acceso, no un dato del titular
        "receipt.template",
        "receipt.status",
        "receipt.cancelled_at",
        "receipt.issued_at",
    }
)

# Tablas cuyo contenido habla de una persona. Son las que la prueba revisa
# columna por columna.
TABLAS_CON_DATOS_PERSONALES: frozenset[str] = frozenset(
    {
        "account",
        "app_user",
        "client",
        "service",
        "service_adjustment",
        "payment",
        "receipt",
    }
)

# El resto: contadores, cupones, control de reintentos y tokens de un solo uso.
# Se enlazan a una cuenta pero no describen a nadie. Se listan para que una
# tabla nueva tenga que clasificarse antes de que la suite vuelva a pasar.
TABLAS_OPERATIVAS: frozenset[str] = frozenset(
    {
        "account_counter",
        "auth_token",
        "coupon",
        "coupon_redemption",
        "idempotency_key",
        "plan_block_event",
        "usage_counter",
    }
)
