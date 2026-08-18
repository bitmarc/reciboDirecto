# PRD — Plataforma de comprobantes de pago no fiscales

**Nombre de trabajo:** Recibo (provisional)
**Versión:** 1.0 — 7 de agosto de 2026
**Autor:** producto definido por entrevista de proceso previa
**Destinatario de este documento:** Claude Code / agente de desarrollo
**Estado:** listo para implementar

---

## 0. Cómo usar este documento

Este PRD es la fuente de verdad del producto. Está escrito para que un agente de desarrollo pueda construir el sistema con el mínimo de preguntas de vuelta.

**Reglas de lectura:**

- Las **reglas de negocio (§5)** están numeradas como `RN-nn` y son inviolables. Si una decisión de implementación entra en conflicto con una `RN`, gana la `RN` y hay que reportarlo, no resolverlo por cuenta propia.
- Los **criterios de aceptación (§12)** están numerados como `AC-nn` y deben tener una prueba automatizada asociada. Un hito no está terminado si sus `AC` no pasan.
- Lo marcado como **`[DECISIÓN ABIERTA]`** no debe implementarse sin confirmar con el responsable del producto.
- Lo marcado como **`[NO-OBJETIVO]`** no debe construirse, ni siquiera de forma preparatoria "para más adelante". El presupuesto de tiempo es de 160 horas y el mayor riesgo del proyecto es el desvío de alcance.
- Todo el texto visible al usuario final va **en español de México**. Todos los identificadores de código van **en inglés**. El glosario de §16 mapea ambos vocabularios.

**Contexto del equipo:** un solo desarrollador, backend, con experiencia en Python y Azure, ~20 h/semana durante 8 semanas.

---

## 1. Contexto y propósito

### 1.1 Qué es

Una aplicación web que permite a un prestador de servicios **generar, entregar y conservar comprobantes de pago no fiscales**, y llevar el control de lo cobrado y lo pendiente de cobro.

**No es un producto fiscal.** No timbra ante el SAT, no genera XML, no requiere PAC, no calcula impuestos. Cualquier tarea que implique cumplimiento fiscal está fuera de alcance de forma permanente en la v1.

### 1.2 El problema real

El usuario de validación es un electricista a domicilio en Ciudad de México: 15–20 trabajos al mes, cobro casi siempre en efectivo, contacto con clientes por WhatsApp, y control de pagos anotado en las notas de su celular.

Tuvo dos conflictos documentados con clientes:

1. Un cliente **olvidó cuánto había abonado** al inicio y no había forma de comprobarlo.
2. Hubo **confusión sobre el monto acordado**, porque pasó tiempo entre el acuerdo y el cobro.

Ninguno de los dos fue por mala fe: fueron **fallos de memoria de ambas partes**.

**Esta es la observación más importante del documento y condiciona todo el diseño.** No se necesita firma con validez legal, ni biometría, ni certificados, ni cadena de bloques. Se necesita que las dos partes tengan **una copia fechada de lo acordado y de cada abono**, disponible cuando la memoria falle.

**Corolario:** el recibo no es solo comprobante de pago. Es también **la memoria del acuerdo**. Por eso el recibo detallado muestra el panorama completo (total, extras, acumulado, saldo) y no solamente "recibí $X".

### 1.3 Los tres dolores, en orden

1. **Prueba del pago** — hoy todo es de palabra.
2. **Control de ingresos** — no hay registro consolidado de lo ganado ni de lo pendiente.
3. **Fricción del papel** — llenar plantillas a mano es tedioso y los papeles se pierden.

### 1.4 El momento de verdad

El flujo crítico ocurre así: el emisor acaba de cobrar en efectivo, está **de pie**, en la puerta de una casa o en un cuarto de máquinas, con el celular en una mano y posiblemente las manos sucias.

**Si el flujo completo de emisión no se resuelve en menos de un minuto, el producto no se usa.** Toda decisión de UI se subordina a esto. Ante la duda entre una pantalla más y un campo opcional menos, gana quitar la pantalla.

### 1.5 Usuario final del recibo

El cliente del emisor **no instala nada, no crea cuenta, no inicia sesión y no hace nada desde su celular**. Solo recibe una liga o un papel. No existe superficie de producto para él más allá de la página pública del recibo.

---

## 2. Alcance

### 2.1 Dentro de la v1

| Épica | Descripción |
|---|---|
| E1 — Cuenta y emisor | Registro, login, perfil del emisor, captura única de firma |
| E2 — Clientes | Directorio reutilizable de clientes |
| E3 — Servicios y pagos | Servicio con total acordado, montos extra, pagos múltiples, saldo |
| E4 — Recibos | Dos plantillas, folio, firma del cliente opcional, cancelación |
| E5 — PDF y liga pública | Generación de PDF, página pública permanente, descarga |
| E6 — Compartir | Copiar liga, compartir a WhatsApp, envío por correo |
| E7 — Contabilidad | Panel de ingresos, comparación mensual, vista de "quién me debe", reporte descargable |
| E8 — Planes y límites | Plan gratuito con límite, plan de pago, cupones |

### 2.2 No-objetivos explícitos

**`[NO-OBJETIVO]`** — no construir, ni dejar andamiaje preparatorio:

- Apps nativas Android/iOS
- Modo sin conexión, service workers, sincronización diferida
- WhatsApp Business API o cualquier integración con Meta
- Recordatorios automáticos de cobro (al emisor o al cliente)
- Multiusuario, roles, permisos, organizaciones
- Analítica avanzada, tendencias, resúmenes con IA
- Personalización de plantillas más allá del logo
- El segmento "empresa que paga a proveedores" (flujo invertido)
- Cotizaciones o presupuestos previos
- Fotos adjuntas al servicio
- Cualquier funcionalidad fiscal (CFDI, XML, PAC, impuestos)
- Internacionalización, multi-moneda, multi-país

**Nota sobre multiusuario:** el modelo de datos debe permitir que esto se agregue después sin una migración destructiva (ver §4.1), pero **no se implementa nada de ello ahora**: sin tabla de roles, sin invitaciones, sin selector de organización.

---

## 3. Stack y decisiones técnicas

Estas decisiones son el punto de partida recomendado. Están alineadas con el perfil del desarrollador (Python, Azure) y con la restricción de presupuesto de infraestructura.

### 3.1 Stack

| Capa | Decisión | Razón |
|---|---|---|
| Backend | **Python 3.12 + FastAPI** | Perfil del desarrollador; async nativo para el manejo de blobs |
| ORM / migraciones | **SQLAlchemy 2.x + Alembic** | Las migraciones versionadas son obligatorias desde el commit uno |
| Base de datos | **PostgreSQL** (Azure Database for PostgreSQL Flexible Server, tier Burstable B1ms) | Ver §13 sobre costo |
| Frontend | **Server-rendered: Jinja2 + HTMX + Alpine.js**, CSS propio o Pico/Tailwind | Un backender solo, 160 horas. Un SPA en React consume 30–40 h que no sobran, y este producto tiene muy poco estado en cliente |
| Firma en pantalla | `signature_pad` (canvas, ~4 KB), exportando PNG con fondo transparente | Único componente que exige JS real |
| PDF | **WeasyPrint** (HTML+CSS → PDF) | Ver §3.2 |
| Almacenamiento | **Azure Blob Storage**, contenedor **privado** | Ver §3.3 |
| Autenticación | Sesión con cookie `HttpOnly` + `Secure` + `SameSite=Lax`, contraseña con Argon2id | Sin OAuth externo en v1 |
| Correo | Azure Communication Services Email, o Resend/SendGrid free tier | Bajo volumen |
| Pagos | **Mercado Pago** (suscripciones) o Stripe | Ver §11.4 |
| Hosting | Azure App Service (Linux, plan B1) o Azure Container Apps | Ver §13 |
| Tests | pytest + pytest-asyncio + httpx |

**`[DECISIÓN ABIERTA]`** Si el responsable prefiere React/Next.js por familiaridad o por reutilización futura en las apps nativas, es una decisión legítima, pero implica recortar alcance en otro lado. No cambiar de stack a media construcción.

### 3.2 Generación de PDF — decisión importante

**Usar WeasyPrint, no un navegador headless.**

Un navegador headless (Playwright, Puppeteer) consume 300–500 MB de RAM por instancia y obliga a un tier de App Service mayor, lo que dispara el costo mensual varias veces. WeasyPrint renderiza HTML+CSS en el mismo proceso, con un consumo marginal, y el recibo es un documento simple: texto, tablas, una imagen de logo y dos imágenes de firma.

Consecuencia de diseño: **la plantilla del recibo se escribe una sola vez en HTML+CSS** y sirve para los tres destinos — la página pública, el PDF y la impresión. No debe haber dos plantillas divergentes.

Limitación conocida a respetar: WeasyPrint soporta un subconjunto de CSS. No usar flexbox complejo ni grid en la plantilla del recibo; tablas y bloques.

### 3.3 Liga pública y almacenamiento — decisión importante

**La liga pública apunta a una ruta de la aplicación (`/r/{token}`), nunca directamente al blob.**

Razones, todas bloqueantes:

- Un blob es estático y no conoce el estado del recibo: sería imposible mostrar el sello de cancelado (`RN-21`).
- Un contenedor público expone todo el contenedor; un SAS de larga vida es una llave permanente que no se puede revocar.
- Se pierde la capacidad de saber si el cliente abrió el recibo (dato deseable a futuro).

**Implementación:** el contenedor de blobs es **privado**. La ruta `/r/{token}` consulta el estado del recibo en base de datos, renderiza la página pública, y sirve el PDF haciendo streaming desde el blob o redirigiendo a un SAS generado al vuelo con vida corta (≤ 5 min).

### 3.4 Convenciones transversales

- **Dinero:** enteros en centavos (`BIGINT`), nunca `float`. Moneda fija `MXN` en v1, pero el campo existe.
- **Fechas:** todo se almacena en UTC (`TIMESTAMPTZ`). La zona horaria de presentación y de corte contable es **`America/Mexico_City`**. El corte mensual usa la fecha local, no UTC — un pago del 31 a las 20:00 hora de CDMX pertenece a ese mes.
- **IDs internos:** `BIGSERIAL` o UUIDv7. **Nunca exponer IDs secuenciales en URLs públicas.**
- **Tokens públicos:** `secrets.token_urlsafe(32)`, único, indexado.
- **Borrado:** no existe borrado físico de recibos, pagos ni servicios. Solo cancelación y estados.
- **Idioma:** identificadores en inglés, copy en español (§16).

---

## 4. Modelo de datos

### 4.1 Diagrama conceptual

```
Account (1) ──< Client (N)
    │
    └──< Service (N) ──< Payment (N) ──< Receipt (0..1)
              │
              └──< ServiceAdjustment (N)
```

**Invariante estructural:** `Service (1) → Payment (N) → Receipt (0..1)`. Los recibos **no son entidades sueltas**; cada recibo documenta un pago, y cada pago pertenece a un servicio. Un pago único es simplemente un servicio con un solo pago que lo salda.

**Preparación para multiusuario:** todas las entidades cuelgan de `Account`, no de `User`. En v1 hay exactamente un `User` por `Account`. Cuando llegue el multiusuario, se agregan usuarios a la cuenta sin migrar datos. **No implementar la tabla de roles ahora.**

### 4.2 Tablas

#### `account`

| Campo | Tipo | Notas |
|---|---|---|
| `id` | PK | |
| `person_name` | text | Nombre de la persona. Obligatorio, nombre + apellido. Es **quien firma**: aparece en la línea de firma del recibo (§8.1) |
| `business_name` | text NULL | Razón social / nombre del negocio, opcional. Cuando existe, **encabeza** el recibo en lugar de `person_name` |
| `phone` | text | Obligatorio. Aparece en el recibo |
| `occupation` | text NULL | Opcional. Ej. "Electricista — instalaciones residenciales" |
| `logo_blob_path` | text NULL | Solo utilizable en plan de pago (`RN-32`) |
| `signature_blob_path` | text NULL | Firma del emisor, capturada una sola vez |
| `default_city` | text | Ej. "Ciudad de México". Cabecera del recibo |
| `plan` | enum(`free`,`paid`) | |
| `plan_valid_until` | timestamptz NULL | |
| `created_at`, `updated_at` | timestamptz | |

#### `user`

| Campo | Tipo | Notas |
|---|---|---|
| `id` | PK | |
| `account_id` | FK → account | |
| `email` | text unique | Credencial de acceso del emisor |
| `password_hash` | text | Argon2id |
| `email_verified_at` | timestamptz NULL | |
| `created_at` | timestamptz | |

#### `client`

| Campo | Tipo | Notas |
|---|---|---|
| `id` | PK | |
| `account_id` | FK | |
| `full_name` | text | **Obligatorio, mínimo nombre + un apellido.** Validar que contenga al menos dos palabras |
| `phone` | text | Obligatorio |
| `email` | text NULL | Opcional. Habilita el envío por correo |
| `archived_at` | timestamptz NULL | Ocultar del selector sin borrar histórico |
| `created_at`, `updated_at` | timestamptz | |

#### `service`

| Campo | Tipo | Notas |
|---|---|---|
| `id` | PK | |
| `account_id` | FK | |
| `client_id` | FK | |
| `code` | text | Folio del servicio, ej. `SRV-0001`. Secuencial **por cuenta** |
| `concept` | text | Descripción del trabajo |
| `location_alias` | text NULL | Ej. "Casa Ramírez", "Depto Narvarte" |
| `location_address` | text NULL | Colonia y calle |
| `agreed_total_cents` | bigint NULL | **INMUTABLE tras su creación** (`RN-11`). NULL = pago único sin total declarado |
| `status` | enum(`open`,`settled`,`closed_uncollected`) | |
| `closed_at` | timestamptz NULL | |
| `closed_reason` | text NULL | Obligatorio si `closed_uncollected` |
| `uncollected_cents` | bigint NULL | Monto dado por perdido al cerrar |
| `created_at`, `updated_at` | timestamptz | |

#### `service_adjustment`

Monto extra agregado a un servicio ya iniciado.

| Campo | Tipo | Notas |
|---|---|---|
| `id` | PK | |
| `service_id` | FK | |
| `amount_cents` | bigint | Positivo |
| `reason` | text | **Obligatorio.** Aparece textualmente en los recibos posteriores |
| `created_at` | timestamptz | |

#### `payment`

| Campo | Tipo | Notas |
|---|---|---|
| `id` | PK | |
| `service_id` | FK | |
| `amount_cents` | bigint | Monto total recibido en este pago |
| `material_cents` | bigint default 0 | **Registro interno.** Se resta del ingreso (`RN-14`). Nunca se muestra en el recibo |
| `method` | enum(`cash`,`transfer`) | |
| `paid_at` | timestamptz | Fecha de pago = fecha de corte contable (`RN-13`) |
| `status` | enum(`active`,`cancelled`) | Un pago se cancela junto con su recibo |
| `created_at` | timestamptz | |

#### `receipt`

**Tabla de solo-inserción en sus campos de contenido.** Un recibo emitido nunca se recalcula (`RN-10`).

| Campo | Tipo | Notas |
|---|---|---|
| `id` | PK | |
| `account_id` | FK | Desnormalizado para la consulta pública |
| `payment_id` | FK unique | |
| `folio` | text | Ej. `RB-0002`. Secuencial **por cuenta**, con hueco tras cancelación (`RN-23`) |
| `public_token` | text unique indexed | 32 bytes url-safe |
| `template` | enum(`single`,`detailed`) | |
| `snapshot` | jsonb | **Todo el contenido congelado del recibo.** Ver §4.3 |
| `pdf_blob_path` | text NULL | Se rellena tras la generación |
| `client_signature_blob_path` | text NULL | Firma del cliente, si se solicitó |
| `client_signature_signer_name` | text NULL | **`[DECISIÓN ABIERTA]`** — ver §14.1 |
| `status` | enum(`issued`,`cancelled`) | |
| `cancelled_at` | timestamptz NULL | |
| `cancellation_reason` | text NULL | **Obligatorio** al cancelar |
| `issued_at` | timestamptz | |

#### `usage_counter`

Para el límite del plan gratuito.

| Campo | Tipo | Notas |
|---|---|---|
| `account_id` | FK | |
| `period` | text | `YYYY-MM` en zona `America/Mexico_City` |
| `receipts_issued` | int | Los cancelados **sí cuentan** (`RN-33`) |

PK compuesta `(account_id, period)`.

#### `coupon` / `coupon_redemption`

| Campo | Tipo | Notas |
|---|---|---|
| `code` | text unique | |
| `discount_type` | enum(`percent`,`fixed`,`free_months`) | |
| `discount_value` | int | |
| `max_redemptions` | int NULL | |
| `valid_until` | timestamptz NULL | |
| `active` | bool | |

### 4.3 El `snapshot` del recibo — el corazón del sistema

Cuando se emite un recibo, se congela en `snapshot` (jsonb) **todo** lo necesario para renderizarlo, sin depender de ninguna otra tabla:

```json
{
  "issued_at_local": "2026-07-07T13:42:00-06:00",
  "city": "Ciudad de México",
  "issuer": {
    "display_name": "…",
    "signer_name": "…",
    "phone": "…",
    "occupation": "…",
    "logo_blob_path": "…",
    "signature_blob_path": "…"
  },
  "client": { "full_name": "…", "phone": "…" },
  "service": {
    "code": "SRV-0001",
    "concept": "…",
    "location_alias": "…",
    "location_address": "…"
  },
  "amounts": {
    "agreed_total_cents": 230000,
    "adjustments": [
      { "amount_cents": 10000, "reason": "Cambio de 2 contactos en recámara" }
    ],
    "current_total_cents": 240000,
    "previous_paid_cents": 180000,
    "payment_cents": 60000,
    "accumulated_paid_cents": 240000,
    "balance_cents": 0
  },
  "method": "cash"
}
```

`issuer.display_name` es la **identidad pública** que encabeza el comprobante —el nombre del negocio si el emisor registró uno, si no el suyo— y `issuer.signer_name` es la **persona que firma**. La llave `display_name` conserva su nombre desde la v1 del formato aunque ya no calque el nombre de la columna: `RN-10` impide reescribir un recibo emitido, así que renombrarla dejaría sin cabecera a todos los anteriores. Los recibos emitidos antes de que existiera `signer_name` no la traen y su línea de firma se lee de `display_name`, que es lo que esos recibos imprimían.

**Regla:** la página pública y el PDF se renderizan **exclusivamente desde el `snapshot`**, nunca desde las tablas vivas. Si mañana el emisor cambia su logo, su teléfono o el nombre del cliente, los recibos ya emitidos **no cambian**. Esto es lo único que sostiene su valor como prueba.

La única excepción es el **estado**: la página pública consulta `receipt.status` en vivo para mostrar el sello de cancelado.

---

## 5. Reglas de negocio

Numeradas e inviolables. Cada una debe tener prueba automatizada.

### Servicios y montos

- **RN-10** — Un recibo emitido **nunca se recalcula**. Su contenido vive congelado en `snapshot`.
- **RN-11** — `agreed_total_cents` es **inmutable** tras la creación del servicio. No existe endpoint de edición. Debe estar protegido a nivel de base de datos (trigger o `CHECK`), no solo en la capa de aplicación.
- **RN-12** — Los aumentos de precio se registran como `service_adjustment` con `amount_cents` y `reason` **obligatorio**. El `reason` aparece textualmente en los recibos posteriores.
- **RN-13** — `current_total = agreed_total + Σ adjustments`. `balance = current_total − Σ pagos activos`.
- **RN-14** — El ingreso contable de un pago es `amount_cents − material_cents`. El material **nunca** aparece en el recibo; el recibo muestra el monto agregado con el concepto combinado (§7.3).
- **RN-15** — Un servicio se crea **en el momento del primer pago**, nunca en una pantalla previa. Declarar el total es opcional dentro de ese mismo formulario.
- **RN-16** — Trabajo adicional en la misma visita: **decide el emisor**. Puede registrarlo como `service_adjustment` del servicio abierto, o crear un servicio nuevo. La UI debe ofrecer ambas rutas de forma explícita; el sistema no decide por él.
- **RN-17** — Un servicio puede cerrarse manualmente como `closed_uncollected` con `closed_reason` obligatorio. El monto no cobrado se registra en `uncollected_cents` y **sale** del total "por cobrar", pero se conserva para el reporte de dinero perdido.

### Recibos

- **RN-20** — La firma del emisor se captura **una sola vez** en la configuración de la cuenta y se estampa automáticamente en **todos** los recibos. Nunca se pide dibujarla al emitir.
- **RN-21** — La firma del cliente es **opcional** y la decide el emisor recibo por recibo. Es evidencia de entrega, no validación legal.
- **RN-22** — Cancelar **nunca borra**. El recibo queda `cancelled`, con `cancellation_reason` obligatorio, visible en el historial y **fuera de la contabilidad**. Su pago asociado pasa a `cancelled` y deja de contar para el saldo del servicio.
- **RN-23** — El folio de un recibo cancelado **no se reutiliza**. Los huecos en la numeración son correctos y esperados.
- **RN-24** — La liga pública de un recibo cancelado sigue resolviendo, y muestra un **sello de cancelado** con el motivo.
- **RN-25** — El folio y el código de servicio son secuenciales **por cuenta**, no globales. Deben generarse de forma segura ante concurrencia (secuencia por cuenta o `SELECT … FOR UPDATE`).

### Ligas y permanencia

- **RN-30** — La liga pública **no caduca mientras la cuenta exista**. Solo el borrado de cuenta dispara eliminación, con periodo de gracia.
- **RN-31** — Las ligas ya emitidas **siguen funcionando** aunque el emisor deje de pagar la suscripción. Un comprobante nunca deja de existir porque el emisor dejó de pagar.
- **RN-32** — El envío es **siempre manual**: copiar liga, compartir a WhatsApp desde el propio teléfono, o enviar por correo si el cliente lo proporcionó. **Nunca** vía WhatsApp Business API.

### Planes

- **RN-33** — El plan gratuito bloquea la emisión al llegar al límite mensual. Los recibos cancelados **sí** consumen cupo (si no, el límite es evadible cancelando).
- **RN-34** — El logo solo se aplica en plan de pago. Si una cuenta baja a gratuito, los recibos **ya emitidos conservan el logo** en su snapshot; los nuevos no lo llevan.
- **RN-35** — Al perder el plan de pago, el emisor conserva **acceso de lectura a todo su historial**. Lo que se limita son las funciones derivadas (analítica, gráficos), restringidas a la ventana del plan gratuito.

---

## 6. Superficie de API

Rutas privadas bajo sesión autenticada, salvo las marcadas como públicas.

### Autenticación

```
POST   /auth/register
POST   /auth/login
POST   /auth/logout
POST   /auth/forgot-password
POST   /auth/reset-password
```

### Cuenta / emisor

```
GET    /settings
PATCH  /settings                  person_name, business_name, phone, occupation, default_city
PUT    /settings/signature        PNG de la firma del emisor
PUT    /settings/logo             solo plan de pago
```

### Clientes

```
GET    /clients                   listado + búsqueda por nombre/teléfono
POST   /clients
GET    /clients/{id}              detalle + historial de servicios
PATCH  /clients/{id}
POST   /clients/{id}/archive
```

### Flujo principal de cobro

```
POST   /payments                  ← endpoint central, ver abajo
```

Este endpoint recibe el formulario completo del momento de cobro y resuelve, en una sola transacción:

- crear o seleccionar el cliente,
- crear el servicio (si es nuevo) o adjuntarse a uno abierto,
- registrar el `service_adjustment` si lo hay,
- registrar el pago,
- emitir el recibo con su snapshot y folio,
- encolar la generación del PDF.

Payload conceptual:

```json
{
  "client": { "id": 12 },
  "service": {
    "id": null,
    "concept": "…",
    "location_alias": "…",
    "location_address": "…",
    "agreed_total_cents": 230000
  },
  "adjustment": { "amount_cents": 10000, "reason": "…" },
  "payment": {
    "amount_cents": 60000,
    "material_cents": 0,
    "method": "cash"
  },
  "client_signature_png": "data:image/png;base64,…",
  "template": "detailed"
}
```

Debe ser **idempotente** frente a doble envío: aceptar una `idempotency_key` del cliente. Un electricista con mala señal va a tocar el botón dos veces, y un recibo duplicado con un cobro duplicado es un fallo grave.

### Servicios

```
GET    /services                  filtro por estado
GET    /services/{id}
POST   /services/{id}/adjustments
POST   /services/{id}/close       cierre por incobrable, reason obligatorio
```

### Recibos

```
GET    /receipts                  historial
GET    /receipts/{id}
POST   /receipts/{id}/cancel      reason obligatorio
GET    /receipts/{id}/pdf         descarga autenticada
POST   /receipts/{id}/send-email  solo si el cliente tiene correo
```

### Público (sin autenticación)

```
GET    /r/{token}                 página pública del recibo
GET    /r/{token}/pdf             descarga pública del PDF
```

### Contabilidad

```
GET    /dashboard                 mes actual, comparación, pendientes
GET    /reports/monthly?period=YYYY-MM
GET    /reports/monthly.csv
```

---

## 7. Pantallas y flujos

### 7.1 Principio rector

La pantalla de cobro es la única que importa. Todo lo demás puede ser feo y funcional; esa tiene que ser rápida, con targets grandes, legible al sol y utilizable con el pulgar de una sola mano.

### 7.2 Pantalla de cobro — `/cobrar`

Una sola pantalla, un solo scroll, un solo botón de confirmación. No hay wizard de varios pasos.

**Bloque 1 — Cliente**
Buscador con autocompletado sobre el directorio. Si no existe, se da de alta ahí mismo con nombre y teléfono (dos campos, sin salir de la pantalla).

**Bloque 2 — Trabajo**
- Selector: "Nuevo trabajo" (por defecto) o "Continuar un trabajo" (lista de servicios abiertos de ese cliente; si no hay, esta opción no se muestra).
- Si es nuevo: concepto, alias del lugar y dirección (ambos opcionales, prellenados con la ciudad por defecto).
- Casilla **"Este trabajo tiene un total acordado"** → revela un único campo de monto. Si no se marca, es pago único.
- Si es continuación: se muestra en cabecera el total, lo pagado y el saldo. Botón secundario **"Agregar trabajo o costo extra"** → revela monto + motivo (obligatorio). Junto a él, un enlace **"o crear un trabajo aparte"** (`RN-16`).

**Bloque 3 — Pago**
- Monto recibido (teclado numérico, campo grande).
- Método: efectivo / transferencia (efectivo por defecto).
- Enlace discreto **"Parte de este monto fue material"** → revela un campo. Colapsado por defecto; en el 90% de los casos no se toca.

**Bloque 4 — Firma**
- Interruptor **"Pedir firma al cliente"**, apagado por defecto.
- Al activarlo, canvas de firma a pantalla completa en horizontal, con botón de borrar.

**Botón: "Generar recibo"** — fijo al pie, ancho completo.

**Pantalla de resultado:** vista previa del recibo y tres acciones — **Compartir por WhatsApp**, **Copiar liga**, **Enviar por correo** (deshabilitado con explicación si el cliente no tiene email). Más un enlace secundario a **Descargar PDF**.

### 7.3 Página pública del recibo — `/r/{token}`

Renderizada desde el `snapshot`. Sin login, sin cookies de sesión, sin tracking de terceros.

Contiene: el recibo completo, un botón grande de **Descargar PDF**, y una leyenda visible: _"Guarda tu copia. Este comprobante está disponible en línea, pero te recomendamos descargarlo."_

Si `status = cancelled`: sello visible de **CANCELADO** con la fecha y el motivo, sobre el contenido atenuado.

Debe llevar `<meta name="robots" content="noindex, nofollow">`.

### 7.4 Panel — `/`

Responde dos preguntas, en ese orden y con ese peso visual:

1. **Cuánto llevo ganado este mes** (ingreso neto de material, sin cancelados, por fecha de pago).
2. **Cuánto me falta por cobrar** (suma de saldos de servicios abiertos).

Debajo: comparación contra los meses anteriores (gráfica de barras simple, 6 meses) y la lista de **quién me debe** — cliente, monto y antigüedad, ordenada por antigüedad descendente.

### 7.5 Otras pantallas

- `/recibos` — historial con búsqueda, filtro por estado, acceso a cancelar.
- `/clientes` — directorio y detalle con historial por cliente.
- `/trabajos` — servicios, filtrables por estado.
- `/ajustes` — perfil del emisor, firma, logo, plan.

---

## 8. Plantillas de recibo

Una sola implementación HTML+CSS que sirve a página pública, PDF e impresión. Diferenciadas por `template`.

### 8.1 Estructura común

**Cabecera:** logo (si aplica), identidad del emisor —el nombre de su negocio si registró uno, si no el suyo—, oficio (si existe), teléfono. A la derecha: **ciudad y fecha de emisión** ("Ciudad de México, 7 de julio de 2026").

**Identificación:** folio del recibo y código del servicio.

**Cuerpo:** "Recibí de {nombre del cliente}", concepto del trabajo, y la ubicación del punto de trabajo (alias y/o dirección) cuando exista.

**Pie:** firma del emisor (siempre), rotulada con el **nombre de la persona** (`person_name`) y nunca con el del negocio —un negocio no firma—; firma del cliente (si se solicitó, con fecha y hora), y la leyenda legal:

> _Comprobante interno sin efectos fiscales. No es un CFDI ni sustituye a uno._

### 8.2 Plantilla `single` (pago único)

Una línea de monto: "Recibí la cantidad de **$X** por concepto de {concepto}", más el monto en letra.

### 8.3 Plantilla `detailed` (pagos múltiples)

Tabla de montos, en este orden exacto:

| Línea | Condición |
|---|---|
| Total acordado inicial | siempre |
| Trabajo adicional — {motivo} | una fila por cada `adjustment`, si los hay |
| Total vigente | solo si hay adjustments |
| Pagos anteriores | si los hay |
| **Pago recibido hoy** ({método}) | siempre, destacado |
| Pagado acumulado | siempre |
| **Saldo pendiente** | siempre, destacado. `$0.00` con etiqueta "Liquidado" cuando corresponde |

**Regla de contenido:** cuando el pago incluye material, el concepto se redacta de forma agregada ("mano de obra y material") y **no se desglosa el monto de material** (`RN-14`).

### 8.4 Selección de plantilla

Automática: `detailed` si el servicio tiene `agreed_total_cents` o más de un pago; `single` en caso contrario. El emisor puede forzar la otra desde la vista previa.

---

## 9. Contabilidad

### 9.1 Definiciones exactas

- **Ingreso del mes** = `Σ (payment.amount_cents − payment.material_cents)` de pagos `active`, cuyos recibos no estén `cancelled`, con `paid_at` dentro del mes en zona `America/Mexico_City`.
- **Por cobrar** = `Σ balance` de servicios en estado `open`. Los `closed_uncollected` **no** cuentan.
- **No cobrado (perdido)** = `Σ uncollected_cents` de servicios `closed_uncollected`. Se muestra por separado, nunca mezclado con el ingreso.

### 9.2 Reporte descargable

CSV mensual con una fila por pago: fecha, folio, cliente, servicio, concepto, monto, material, ingreso neto, método, estado.

En plan gratuito, la consulta se limita a la ventana permitida (`RN-35`); en plan de pago, sin límite.

---

## 10. Seguridad y privacidad

- **Aislamiento por cuenta:** toda consulta filtra por `account_id`. Nunca confiar en el ID que venga del cliente. Idealmente, un helper de repositorio que haga imposible construir una query sin el `account_id` de la sesión.
- **Tokens públicos:** ≥ 32 bytes de entropía, generados con `secrets`. Prohibido derivarlos del ID.
- **Rate limiting** en `/r/{token}` para impedir enumeración por fuerza bruta, y en login.
- **Contraseñas:** Argon2id.
- **Blobs privados.** Todo acceso pasa por la aplicación (§3.3).
- **Cabeceras:** `noindex` en páginas públicas de recibos; CSP básica; sin analíticas de terceros en `/r/{token}`.
- **Firma:** almacenar el PNG de la firma es dato personal. No exponerlo en URL directa; servirlo siempre a través de la aplicación.
- **Avisos legales:** aviso de privacidad y términos deben dejar claro que el emisor es el **responsable del tratamiento** de los datos de sus clientes, y la plataforma el **encargado**. Debe estar publicado antes del primer usuario real.
- **Borrado de cuenta:** gracia de reversión de **30–90 días** `[DECISIÓN ABIERTA]`, ver §14.2.

---

## 11. Planes, límites y cobro

### 11.1 Planes

| | Gratuito | De pago (~$80 MXN/mes) |
|---|---|---|
| Emisión de recibos | Hasta **10/mes**, luego bloqueo | Ilimitados |
| Plantillas | Las dos, sin personalizar | Personalizables |
| Logo | No | Sí |
| Contabilidad | Ventana limitada | Completa |
| Envío por correo | Sí | Sí |

### 11.2 Comportamiento al topar el límite

Bloqueo de emisión hasta el mes siguiente o hasta pagar, con pantalla clara que ofrezca la suscripción.

**Instrumentación obligatoria:** registrar cada evento de bloqueo con cuenta, fecha y número de recibo intentado. Es el dato que permitirá distinguir "el producto no le gustó" de "topó el muro". Ver riesgo §14.3.

### 11.3 Cupones

Códigos canjeables al suscribirse: porcentaje, monto fijo o meses gratis. Con límite de canjes y vigencia. Primer uso: regalo a los primeros usuarios que den feedback.

### 11.4 Cobro

**`[DECISIÓN ABIERTA]`** Mercado Pago tiene mejor aceptación en México para cobros recurrentes de bajo monto; Stripe tiene mejor DX. A $80 MXN, la comisión deja ~$70 netos.

**Recomendación de secuencia:** no construir el cobro hasta el hito 5. Durante la validación con el primer usuario, la suscripción puede activarse manualmente desde base de datos. Construir la pasarela antes de saber si alguien quiere pagar es gastar horas del presupuesto más escaso.

---

## 12. Criterios de aceptación

Cada uno debe tener prueba automatizada.

**Inmutabilidad**
- `AC-01` Modificar `agreed_total_cents` de un servicio existente falla, tanto por API como por SQL directo.
- `AC-02` Cambiar el logo, teléfono o nombre del emisor **no** altera el render de un recibo emitido antes del cambio.
- `AC-03` Renombrar a un cliente no altera los recibos previos.

**Cálculo**
- `AC-04` Servicio de $2,300, pago de $1,800 → saldo $500, servicio `open`.
- `AC-05` Adjustment de +$100 con motivo, pago de $600 → total vigente $2,400, acumulado $2,400, saldo $0, servicio `settled`.
- `AC-06` Pago de $1,000 con $300 de material → ingreso del mes $700; el recibo muestra $1,000 sin desglose.
- `AC-07` Un pago del 31 de enero 23:30 hora CDMX cuenta en enero, no en febrero.

**Cancelación**
- `AC-08` Cancelar sin motivo falla.
- `AC-09` Un recibo cancelado desaparece del ingreso y su pago deja de contar en el saldo del servicio.
- `AC-10` La liga de un recibo cancelado responde 200 y muestra el sello con el motivo.
- `AC-11` El folio de un recibo cancelado no se reutiliza en la siguiente emisión.

**Ligas y aislamiento**
- `AC-12` `/r/{token}` con token inválido responde 404 sin filtrar información.
- `AC-13` Un usuario de la cuenta A no puede leer ningún recurso de la cuenta B por ninguna ruta.
- `AC-14` El PDF es descargable desde la liga pública sin sesión, y el blob no es accesible directamente.

**Planes**
- `AC-15` La cuenta gratuita en su recibo número 11 del mes recibe bloqueo, y el evento queda registrado.
- `AC-16` Un recibo cancelado consume cupo.
- `AC-17` Una cuenta que baja a gratuito conserva lectura de todo su historial.

**Flujo**
- `AC-18` Dos envíos del mismo formulario con la misma `idempotency_key` producen un solo pago y un solo recibo.
- `AC-19` El flujo completo de cobro se puede completar en una sola pantalla, sin recargas intermedias.

---

## 13. Infraestructura y costos

Restricción declarada: **presupuesto limitado**, sobre todo en desarrollo.

La aplicación no es intrínsecamente cara: sin procesamiento pesado, sin video, tráfico bajo. El único costo variable real es el almacenamiento de PDFs, y es marginal (un recibo pesa ~100 KB; 10.000 usuarios emitiendo 20/mes durante un año son ~240 GB, unos pocos dólares mensuales en tier Cool).

**Los tres puntos donde el costo se escapa sin avisar:**

1. **Base de datos.** El modelo elegido cambia el costo por un orden de magnitud. Para desarrollo, usar el tier más pequeño o Postgres en contenedor local. Evaluar autopausa donde exista.
2. **Generación de PDF.** Un navegador headless obliga a un tier de memoria mayor. Por eso §3.2 fija WeasyPrint.
3. **Servicios encendidos "por si acaso".** Revisar el grupo de recursos cada semana; apagar lo que no se usa en desarrollo.

**Recomendación de entorno de desarrollo:** todo en local con Docker Compose (Postgres + Azurite para blobs). Azure solo se toca para el entorno de validación con el usuario real. Esto ahorra dinero y acelera el ciclo.

**Blobs:** tier Hot para PDFs recientes, regla de ciclo de vida a Cool a los 90 días.

---

## 14. Decisiones abiertas

Deben resolverse con el responsable del producto. **No inventar la respuesta.**

### 14.1 Firma de un tercero
Cuando firma alguien que no es el titular (cónyuge, encargado, conserje), ¿debe capturarse **quién firmó y en qué calidad**? Es precisamente uno de los escenarios que motivan pedir firma, y sin ese dato se reproduce el problema de "yo no fui". El campo está previsto en `receipt`, pero la UI no debe construirse hasta confirmarlo.
**Bloquea:** hito 3.

### 14.2 Periodo de gracia tras borrado de cuenta
Se mencionó una gracia de 10 años, lo cual entra en conflicto con el derecho de cancelación bajo la LFPDPPP: retener una década no es gracia, es retención. Conviene separar **gracia de reversión** (30–90 días) de **retención posterior**, que solo procede con causa jurídica concreta declarada en el aviso de privacidad y limitada al comprobante, no al perfil completo.
_Orientación general, no asesoría legal; conviene revisarlo con un abogado antes de publicar los términos._
**Bloquea:** publicación de términos, no el desarrollo.

### 14.3 Calibración del límite gratuito
Está fijado en 10 recibos/mes con bloqueo. **El usuario de validación hace 15–20 al mes**: topará el muro a mitad de mes, y lo hará justo cuando tenga el efectivo en la mano. Ese momento no produce una conversión, produce un abandono. Alternativas menos hostiles, si se decide revisarlo: limitar por **ventana de historial** o por **número de clientes**, dejando la emisión siempre libre.
La decisión tomada es el bloqueo, y así se implementa. La instrumentación de §11.2 existe para poder revisarla con datos.
**No bloquea el desarrollo.**

### 14.4 Frontend
Si se prefiere React/Next.js sobre server-rendered, decidirlo **antes** del hito 1 y recortar alcance en otro lado.
**Bloquea:** hito 0.

### 14.5 Pasarela de pago
Mercado Pago vs. Stripe. Se puede diferir hasta el hito 5.

---

## 15. Plan de construcción

160 horas totales (8 semanas × 20 h). El orden está diseñado para que **el producto sea probable con el usuario real lo antes posible**, no para que esté completo.

### Hito 0 — Cimientos (10 h)
Repositorio, Docker Compose (Postgres + Azurite), FastAPI, SQLAlchemy, Alembic, pytest, CI mínima. Autenticación por sesión. Modelo de datos completo migrado, con la inmutabilidad de `agreed_total_cents` protegida en base de datos.
**Salida:** `AC-01` pasa.

### Hito 1 — Emisor y clientes (15 h)
Registro, login, perfil del emisor, captura de firma, directorio de clientes.

### Hito 2 — Motor de cobro (30 h)
Servicios, adjustments, pagos, recibos, snapshot, folios por cuenta, cálculo de saldos, cancelación. **Toda la lógica de dominio, sin UI bonita.**
**Salida:** `AC-04` a `AC-09`, `AC-11`, `AC-16`.

### Hito 3 — Recibo, PDF y liga (25 h)
Plantilla HTML única, WeasyPrint, subida a blob, página pública `/r/{token}`, sello de cancelado, descarga de PDF, firma del cliente en canvas.
**Salida:** `AC-02`, `AC-03`, `AC-10`, `AC-12`, `AC-14`.
**Requiere resolver §14.1.**

### Hito 4 — Pantalla de cobro y compartir (25 h)
La pantalla de `/cobrar` completa, con idempotencia. Compartir por WhatsApp, copiar liga, enviar por correo.
**Salida:** `AC-18`, `AC-19`.

> **Fin del hito 4 ≈ 105 h ≈ semana 5. Aquí el producto es entregable al usuario real.**
> Ponerlo en sus manos en este punto, aunque no haya panel ni planes ni cobros. El panel se mira una vez por semana; el recibo se emite 20 veces al mes. Si el flujo de emisión no se sostiene solo, nada de lo demás importa.

### Hito 5 — Contabilidad (20 h)
Panel de ingresos, comparación mensual, "quién me debe", reporte CSV, cierre por incobrable.
**Salida:** `AC-06`, `AC-07`.

### Hito 6 — Planes y cobro (20 h)
Contador de uso, bloqueo con instrumentación, pasarela, cupones.
**Salida:** `AC-15`, `AC-17`.

### Reserva (10 h)
Despliegue en Azure, aviso de privacidad y términos, correcciones de la validación.

---

## 16. Glosario y convenciones de nomenclatura

Identificadores en inglés; copy en español.

| Dominio (español) | Código (inglés) |
|---|---|
| Emisor / cuenta | `Account` |
| Cliente | `Client` |
| Trabajo / servicio | `Service` |
| Monto extra / trabajo adicional | `ServiceAdjustment` |
| Pago / abono | `Payment` |
| Recibo / comprobante | `Receipt` |
| Folio | `folio` |
| Total acordado | `agreed_total_cents` |
| Saldo pendiente | `balance_cents` |
| Material | `material_cents` |
| Liga pública | `public_token` |
| Dinero no cobrado | `uncollected_cents` |

**Copy de referencia:**
- "Recibí de {cliente}"
- "Total acordado" / "Trabajo adicional" / "Total vigente"
- "Pagos anteriores" / "Pago recibido hoy" / "Pagado acumulado"
- "Saldo pendiente" / "Liquidado"
- "Comprobante interno sin efectos fiscales. No es un CFDI ni sustituye a uno."

---

## 17. Definición de éxito

La v1 no se juzga por funcionalidades completadas, sino por lo que ocurra a las **6 semanas** de estar en manos del primer usuario real:

1. Sigue emitiendo recibos **sin que nadie se lo recuerde**.
2. Los emite **en sitio**, no capturándolos en su casa por la noche. Si captura en diferido, el producto perdió su momento de valor y se convirtió en tarea administrativa. **Instrumentar la diferencia entre `paid_at` y `created_at` para medir esto.**
3. Consulta la vista de "quién me debe" por iniciativa propia.
4. Señal más fuerte: **paga**, o declara que pagaría.

Si 1 y 2 no se cumplen, ninguna funcionalidad adicional lo arregla. El diagnóstico correcto está en la sección de riesgos: puede ser el muro del plan gratuito (§14.3) o puede ser la falta de señal en sótanos y obra negra, ya que la v1 exige internet. **Son conclusiones opuestas y hay que distinguirlas antes de tocar el producto.**
