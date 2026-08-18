# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Idioma

El repositorio trabaja en español: copy, rutas visibles, comentarios, docstrings,
documentación y mensajes de commit. **Los identificadores de código van en
inglés** (§16). Mantén esa separación al escribir código nuevo.

## Autoridad del PRD

`prd-plataforma-recibos.md` es la fuente de verdad del producto. Las reglas de
negocio (`RN-nn`) y los criterios de aceptación (`AC-nn`) son inviolables: **si
una decisión de implementación entra en conflicto con una, gana la regla y hay
que reportarlo al usuario**, no resolverlo por cuenta propia.

Las decisiones ya tomadas (y su porqué) están en `README.md`, sección
«Decisiones que conviene conocer antes de tocar el código». `docs/pendientes.md`
lleva el corte de lo que quedó a medias. Léelos antes de proponer un cambio de
arquitectura: casi todo lo que parece raro está justificado ahí.

Los docstrings y comentarios citan la sección del PRD (`§7.2`), la regla
(`RN-12`) o el criterio (`AC-07`) que sostienen. Sigue esa convención: es lo que
permite rastrear por qué existe cada pieza.

## Comandos

**Todo corre dentro de Docker**, incluidas las pruebas y el linting. No es
comodidad: WeasyPrint renderiza a través de Pango y una versión distinta de
Pango cambia tipografías y saltos de línea. Nunca ejecutes `pytest`, `ruff` o
`mypy` en el host — además, `DATABASE_URL` apunta a `db:5432`, que solo resuelve
dentro de la red del compose.

```bash
docker compose up -d --build                       # app + postgres + azurite
docker compose run --rm app alembic upgrade head   # esquema

docker compose run --rm app pytest                             # suite completa
docker compose run --rm app pytest tests/unit                  # dominio puro, sin base de datos
docker compose run --rm app pytest -m "not db"                 # todo lo que no toca PostgreSQL
docker compose run --rm app pytest tests/integration/test_charging.py::test_nombre -x   # una prueba

docker compose run --rm app ruff check .
docker compose run --rm app ruff format .
docker compose run --rm app mypy src/receipt

docker compose run --rm app alembic revision --autogenerate -m "descripción"
docker compose run --rm app alembic check          # ¿falta generar alguna migración?

docker compose logs -f app
docker compose exec db psql -U receipt -d receipt
```

La imagen corre como usuario `app` (uid 1000). Si un comando que **escribe** en
el código montado (`ruff format`, `ruff check --fix`, `alembic revision
--autogenerate`) falla por permisos, agrégale `--user root`.

Antes de dar por terminado un cambio, replica la puerta de CI:

```bash
docker compose run --rm app sh -c "ruff check . && ruff format --check . && mypy src/receipt && pytest && alembic check"
```

CI además comprueba que las migraciones **revierten y se vuelven a aplicar**
(`upgrade head` → `downgrade base` → `upgrade head`): un `downgrade` roto rompe
el build.

## Arquitectura

Las dependencias van en un solo sentido: `domain` ← `services` ← `web`.

| Capa | Responsabilidad |
|---|---|
| `domain/` | Lógica pura: dinero, fechas, cálculos, snapshot, planes. Sin I/O, sin SQLAlchemy, sin FastAPI. `mypy --strict`. |
| `models/` | Tablas SQLAlchemy 2.0 (async). |
| `repositories/` | `AccountScope`: el único camino a los datos. |
| `services/` | Casos de uso. **Dueños de la transacción**. Lanzan `DomainError`, no `HTTPException`. |
| `security/` | Argon2id, sesiones firmadas, CSRF, cabeceras, límite de intentos. |
| `web/` | Rutas delgadas (validan, llaman a un servicio, renderizan), plantillas Jinja2, estáticos. |
| `infra/` | Azure Blob, correo (ACS) y PDF (WeasyPrint). |

### Transacciones

`get_db_session` **no hace commit**. La unidad de trabajo la define la capa de
servicios: emitir un recibo es una sola transacción de principio a fin —cliente,
trabajo, ajuste, pago, recibo, folio, consumo del plan—. Si escribes un servicio
nuevo, el commit es tuyo; si lo llamas desde otro servicio, no.

### Aislamiento por cuenta (`AC-13`)

`repositories/base.py` mantiene un registro `_RULES` que declara cómo se ata cada
modelo a una cuenta. **Un modelo sin regla registrada no se puede consultar**:
`AccountScope.select()` lanza `LookupError`. Al agregar una tabla, agrega su
regla ahí.

`AccountScope` se construye con el `account_id` de la sesión firmada y ninguna
ruta acepta uno por parámetro. `tests/integration/test_account_scope.py` recorre
`app.routes` por introspección y comprueba que cada recurso con ID responde 404 a
una cuenta ajena: las rutas nuevas quedan cubiertas solas, pero también fallan
solas si se salen del patrón.

`/r/{token}` es la **única** consulta fuera de `AccountScope`: ahí no hay sesión y
el token es la credencial.

### Cinco invariantes que no se negocian

1. **Dinero en centavos enteros, nunca `float`.** El formateo vive solo en
   `domain/money.py` y tampoco convierte a punto flotante.
2. **Todo se almacena en UTC; el corte contable es hora de CDMX**
   (`domain/clock.py`). Ningún cálculo de mes usa UTC (`AC-07`).
3. **El recibo emitido nunca se recalcula** (`RN-10`). Su contenido vive
   congelado en `receipt.snapshot`; la página pública y el PDF se renderizan
   **solo** desde ahí, nunca desde las tablas vivas. Cambiar la estructura del
   snapshot obliga a subir `SNAPSHOT_VERSION` y a seguir leyendo las versiones
   anteriores.
4. **Toda consulta se acota por cuenta** (arriba).
5. **Los blobs se versionan; nunca se sobrescriben.** El snapshot guarda *rutas*,
   no bytes: pisar un blob cambiaría la firma de recibos ya emitidos sin tocar
   una fila de la base. Los blobs huérfanos son correctos y esperados.

### Restricciones de la capa web

- **CSP `script-src 'self'`.** `htmx` y `signature_pad` están vendorizados en
  `web/static/js/` con la versión fijada en un comentario. No agregues CDNs ni
  nada que necesite `unsafe-eval` (por eso `/cobrar` es JavaScript plano en
  `static/js/cobrar.js` y no Alpine, aunque §3.1 lo nombre).
- **Las rutas visibles van en español** (`/cobrar`, `/recibos`, `/clientes`); la
  superficie en inglés de §6 son alias de redirección en `web/routes/pages.py`,
  que se registra **al final** porque sus rutas son comodines.
- **La plantilla del comprobante se escribe una vez.** `receipt/_document.html`
  es el recibo; `public.html` y `pdf.html` solo lo envuelven. Dentro: tablas y
  bloques, **nunca flexbox ni grid** — WeasyPrint soporta un subconjunto de CSS.
  Las imágenes van como `data:` URI, no como URL: el contenedor de blobs es
  privado.
- **Formateo y etiquetas viven en `web/templating.py` y `web/labels.py`**, no en
  las plantillas: filtros `pesos`, `fecha_larga`, `mes`, globales
  `estado_recibo`, `metodo_pago`, etc.
- `verify_csrf` va como dependencia del router en las rutas autenticadas; la
  pública no lleva sesión ni CSRF.
- El aviso de privacidad declara su inventario de datos en `web/legal.py` (no en
  la plantilla) y `tests/unit/test_legal_inventory.py` recorre el esquema real:
  **una columna nueva sin clasificar rompe la suite**. Clasifícala ahí.

## Pruebas

- `tests/unit/` no toca base de datos: es donde se prueban las `RN` en
  milisegundos. Lo que puedas probar ahí, pruébalo ahí.
- `tests/integration/` corre contra **PostgreSQL real** (base `receipt_test`) y
  **Azurite real**, marcado con `pytestmark = pytest.mark.db`. No hay dobles en
  memoria a propósito: `AC-01` lo sostiene un trigger de PostgreSQL y `AC-02` la
  no-sobrescritura de blobs.
- El esquema se construye **aplicando las migraciones**, no con
  `metadata.create_all`: un `create_all` no crearía los triggers.
- Cada prueba corre dentro de una transacción que se revierte; el fixture `db`
  usa `join_transaction_mode="create_savepoint"` para que los servicios puedan
  hacer `commit()` de verdad sin escapar del rollback.
- Fixtures clave (`tests/conftest.py`): `signed_in` (cuenta registrada por la vía
  real, con sesión iniciada), `mailer` (`RecordingMailer`), `mailer_caido` (envío
  que falla), `blobs`, `app`, `client`. Ayudantes: `visible_text`,
  `document_text`, `csrf_token_of`. Constructores en `tests/factories.py`.
- `tests/integration/test_javascript_contract.py` comprueba que los enganches del
  JavaScript existan en las plantillas. Si renombras un `id`, un `data-*` o un
  `name` que toque un script, actualiza esa prueba: no hay navegador en la suite.

## Detalles del esquema que sorprenden

- La tabla del usuario se llama **`app_user`** (`user` es reservada en
  PostgreSQL); la clase se llama `User`.
- La cuenta guarda **dos nombres**: `person_name` (quien firma el recibo,
  obligatorio) y `business_name` (opcional, encabeza el recibo). `Account.
  issuer_name` deriva la identidad pública y es lo que se lee en todas partes
  menos en la línea de la firma. En el snapshot la llave sigue llamándose
  `issuer.display_name` **a propósito**: renombrarla dejaría sin cabecera a los
  recibos ya emitidos (RN-10). La firma cae a esa llave cuando el recibo es de
  la v1 del formato, y esa caída vive en `services/rendering.py` y no en la
  plantilla, porque Jinja usa `Undefined` y una llave ausente se renderizaría
  como una línea en blanco sin que falle nada.
- **`agreed_total_cents` lo protege un trigger de PostgreSQL**, no la aplicación:
  `AC-01` exige que modificarlo falle también por SQL directo.
- Los **folios** usan una tabla contador con `UPDATE … RETURNING` por cuenta
  (`RN-25`), no una secuencia. Un cobro que revierte no quema folio.
- El **PDF se genera bajo demanda** y se cachea en `pdf_blob_path`. Cancelar o
  firmar sueltan esa ruta para que la siguiente descarga regenere.

## Entorno local

`cp .env.example .env`. `MAIL_BACKEND=console` —el valor por omisión— escribe el
correo al log: la liga de recuperación aparece en `docker compose logs -f app`.
`MAIL_BACKEND=azure` envía de verdad y **exige** `ACS_CONNECTION_STRING` y
`ACS_SENDER_ADDRESS`; sin ellas la aplicación no arranca, a propósito. En
producción también son obligatorias `LEGAL_RESPONSABLE`, `LEGAL_DOMICILIO` y
`LEGAL_CONTACTO_ARCO` (art. 15 de la LFPDPPP).
