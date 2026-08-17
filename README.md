# Recibo — plataforma de comprobantes de pago no fiscales

Aplicación web para que un prestador de servicios **genere, entregue y conserve
comprobantes de pago no fiscales**, y lleve el control de lo cobrado y lo
pendiente de cobro.

**No es un producto fiscal.** No timbra ante el SAT, no genera XML, no requiere
PAC y no calcula impuestos. Todo lo fiscal está fuera de alcance de forma
permanente en la v1.

La fuente de verdad del producto es [prd-plataforma-recibos.md](prd-plataforma-recibos.md).
Las reglas de negocio (`RN-nn`) son inviolables: **si una decisión de
implementación entra en conflicto con una, gana la regla y hay que reportarlo**,
no resolverlo por cuenta propia.

---

## Arrancar en local

Requisitos: Docker Desktop. Nada más — no hace falta Python ni PostgreSQL en la
máquina.

```bash
cp .env.example .env          # ajusta SESSION_SECRET si vas a exponer el puerto
docker compose up -d --build
docker compose run --rm app alembic upgrade head
```

La aplicación queda en <http://localhost:8000>. Crea tu cuenta en `/registro`.

### Comandos frecuentes

```bash
docker compose run --rm app pytest                 # suite completa
docker compose run --rm app pytest tests/unit      # solo dominio, sin base de datos
docker compose run --rm app ruff check . && docker compose run --rm app ruff format .
docker compose run --rm app mypy src/receipt

docker compose run --rm app alembic revision --autogenerate -m "descripción"
docker compose run --rm app alembic upgrade head
docker compose run --rm app alembic check          # ¿falta generar alguna migración?

docker compose logs -f app
docker compose exec db psql -U receipt -d receipt
```

**Todo corre dentro de Docker a propósito.** WeasyPrint renderiza a través de
Pango, y una versión distinta de Pango cambia tipografías y saltos de línea: un
recibo que se ve bien en tu máquina se vería distinto en producción. Usar la
misma imagen en desarrollo, en CI y en Azure elimina esa clase de sorpresa. En
Windows, además, evita instalar el runtime GTK a mano.

---

## Cómo está organizado

Las capas dependen en un solo sentido: `domain` ← `services` ← `web`.

```
src/receipt/
  domain/        Lógica pura: dinero, fechas, cálculos, reglas. Sin I/O ni SQLAlchemy.
  models/        Tablas de SQLAlchemy.
  repositories/  Acceso a datos acotado por cuenta.
  services/      Casos de uso. Dueños de la transacción.
  security/      Argon2id, sesiones, CSRF, cabeceras, límite de intentos.
  web/           Rutas delgadas, plantillas Jinja2, estáticos.
  infra/         Blob Storage, correo y PDF (WeasyPrint).
```

`signature_pad` y `htmx` están vendorizados en `web/static/js/` con la versión
fijada en un comentario. No es preferencia: la CSP es `script-src 'self'`, y un
CDN en la CSP es un tercero con permiso de ejecutar código sobre datos de cobro.

`domain/` es puro a propósito: las reglas de negocio se prueban en milisegundos
y sin base de datos, que es lo que hace viable tener una prueba por cada `RN` y
cada `AC`.

### Cuatro invariantes que no se negocian

1. **Dinero en centavos enteros, nunca `float`.** El formateo vive solo en
   `domain/money.py` y tampoco convierte a punto flotante.
2. **Todo se almacena en UTC; el corte contable es hora de CDMX.** Un pago del 31
   a las 23:30 pertenece a ese mes (`AC-07`). Ver `domain/clock.py`.
3. **El recibo emitido nunca se recalcula** (`RN-10`). Su contenido vive
   congelado en `receipt.snapshot`, y la página pública y el PDF se renderizan
   solo desde ahí. Si el emisor cambia su logo o el nombre de un cliente, los
   recibos ya emitidos no cambian. Es lo único que sostiene su valor como prueba.
4. **Toda consulta se acota por cuenta.** `AccountScope` recibe el `account_id`
   de la sesión y ninguna ruta acepta uno por parámetro. Un modelo sin regla de
   alcance registrada no se puede consultar: falla al construir la query, no en
   producción. Una prueba recorre `app.routes` y comprueba que cada recurso con
   ID responde 404 a una cuenta ajena, así que cada ruta nueva queda cubierta
   sola.
5. **Los blobs se versionan; nunca se sobrescriben.** Cada captura de firma o
   logo escribe una ruta nueva. El snapshot guarda *rutas*, no bytes: pisar un
   blob cambiaría la firma de todos los recibos ya emitidos sin tocar una fila
   de la base de datos, y `AC-02` fallaría por una vía que ninguna prueba de
   base de datos detecta. Los blobs huérfanos son correctos y esperados.

### Decisiones que conviene conocer antes de tocar el código

- **La tabla del usuario se llama `app_user`**, no `user`: es palabra reservada
  en PostgreSQL y obligaría a entrecomillarla en cada trigger y consulta directa.
  El nombre lógico del PRD se conserva en la clase `User`.
- **`agreed_total_cents` lo protege un trigger de PostgreSQL**, no la capa de
  aplicación. `AC-01` exige que modificarlo falle también por SQL directo.
- **Los folios usan una tabla contador con `UPDATE … RETURNING`** (`RN-25`).
  PostgreSQL no tiene secuencias por fila y los folios son por cuenta. El
  bloqueo se libera al cerrar la transacción, así que un cobro que falla y
  revierte no quema folio: los huecos de `RN-23` vienen de cancelaciones.
- **Un pago mayor que el saldo se rechaza**, con un error que apunta al monto
  extra. Un sobrepago es un aumento de precio sin documentar, y `RN-12` exige
  motivo para todo aumento; §7.2 ya pone ese campo en la misma pantalla. Es
  decisión de producto, no una regla del PRD: está en `services/charging.py`.
- **El snapshot lleva `paid_at_local` y `version`**, que §4.3 no enumera. El
  primero porque un comprobante de pago tiene que poder decir cuándo se pagó, y
  con captura en diferido (§17) esa fecha no coincide con la de emisión; el
  segundo porque `RN-10` prohíbe reescribir recibos, así que el formato tiene
  que decir qué forma tiene para poder evolucionar.
- **El cupo del plan gratuito se cuenta por mes de emisión; el ingreso, por
  `paid_at`.** Son dos cortes distintos porque miden cosas distintas: `RN-33`
  acota cuántos recibos se generan, §9.1 cuánto se cobró.
- **El PDF se genera bajo demanda y se cachea en el blob.** §6 dice "encolar",
  pero no hay cola y montarla es alcance que el presupuesto no tiene. Generarlo
  al emitir añadiría latencia al minuto de §1.4 y dejaría que un fallo del
  renderizador tirase un cobro ya ocurrido. Cancelar o firmar sueltan
  `pdf_blob_path`, así que la siguiente descarga regenera: un recibo cancelado
  que circula en PDF sin sello es peor que no tener PDF.
- **`/r/{token}` es la única consulta que no pasa por `AccountScope`.** Ahí no
  hay sesión y el token *es* la credencial. Por eso lleva 32 bytes de entropía,
  va limitada por IP contra la enumeración y un token inválido responde 404
  indistinguible de uno inexistente.
- **La plantilla del comprobante se escribe una vez** (`templates/receipt/`).
  `_document.html` es el recibo; `public.html`, `pdf.html` y la ficha del emisor
  solo lo envuelven. Dentro: tablas y bloques, nunca flexbox ni grid — WeasyPrint
  soporta un subconjunto de CSS (§3.2). Una prueba comprueba que el documento no
  se desborda a una segunda página.
- **Las imágenes del recibo van como `data:` URI, no como URL.** Es lo que
  WeasyPrint necesita, y además el contenedor es privado: una URL al blob no
  resolvería, y una que resolviera expondría un dato personal.
- **`/cobrar` no usa Alpine, aunque §3.1 lo nombre.** Alpine 3 evalúa sus
  expresiones con `new Function()`, y eso exige `unsafe-eval` en la CSP justo en
  la pantalla que maneja los datos de cobro. `static/js/cobrar.js` hace lo mismo
  en JavaScript plano, sin dependencias y sin tocar la CSP.
- **La clave de idempotencia la genera el servidor** al pintar `/cobrar`, no el
  navegador. Con mala señal, que es cuando se toca dos veces el botón, el
  JavaScript es precisamente lo que puede no haber cargado.
- **El alta de un cliente nuevo ocurre dentro de la transacción del cobro**
  (§6), no en una llamada aparte: un formulario abandonado no deja clientes
  sueltos en el directorio.
- **Hay pruebas del contrato entre el JavaScript y las plantillas**
  (`test_javascript_contract.py`). No hay navegador en la suite, así que un
  atributo renombrado dejaría la pantalla muerta sin que fallara nada del
  servidor. Nació de dos fallos reales al cablear `/cobrar`.
- **Las rutas visibles van en español** (`/cobrar`, `/recibos`, `/clientes`),
  porque §0 exige que todo lo visible al usuario esté en español de México. La
  superficie en inglés de §6 del PRD queda como alias de redirección.
- **Los identificadores de código van en inglés**; el copy, en español (§16).
- **La búsqueda de clientes ignora acentos** vía `translate`, no vía la extensión
  `unaccent`: cubre el alfabeto español —todo lo que la v1 necesita— sin obligar
  a habilitar una extensión en Azure. Nadie escribe "Ramírez" con tilde en el
  celular estando de pie en una puerta.
- **La versión de la API de Azure Storage está fijada** (`infra/blob.py`). El SDK
  usa por omisión la más reciente y Azurite va por detrás; sin fijarla, cada
  actualización del SDK rompe el entorno local con un error que no dice nada.
- **`domain/images.py` fuerza `LOAD_TRUNCATED_IMAGES = False` durante la
  validación.** WeasyPrint lo pone en `True` al importarse; sin aislarlo, una
  firma a medias se aceptaría en silencio rellenada con lo que Pillow alcance a
  decodificar.

### Correo en desarrollo

`MAIL_BACKEND=console` —el valor por omisión— escribe el correo al log en lugar
de enviarlo. La liga de recuperación aparece en `docker compose logs -f app`.

`MAIL_BACKEND=azure` envía de verdad, por Azure Communication Services. Necesita
`ACS_CONNECTION_STRING` y `ACS_SENDER_ADDRESS`; sin ellas la aplicación no
arranca, a propósito. El alta de los recursos está en
[`docs/correo-azure-alta.md`](docs/correo-azure-alta.md).

### Aviso de privacidad

`/privacidad` se sirve sin sesión: quien aún no se registra necesita leerlo
antes de entregar sus datos. La identidad del responsable sale de
`LEGAL_RESPONSABLE`, `LEGAL_DOMICILIO` y `LEGAL_CONTACTO_ARCO`, **obligatorias en
producción** porque el art. 15 de la LFPDPPP las exige.

El inventario de datos que declara el aviso vive en `web/legal.py`, no en la
plantilla, y `tests/unit/test_legal_inventory.py` recorre el esquema real para
que una columna nueva sin clasificar rompa la suite. Es lo que impide que el
aviso se vuelva mentira en silencio.

---

## Estado

| Hito | Alcance | Estado |
|---|---|---|
| 0 | Cimientos: Docker, esquema completo, migraciones, autenticación, CI | **Terminado** — `AC-01` pasa |
| 1 | Perfil del emisor, firma, logo, clientes, recuperación de contraseña | **Terminado** — media parte de `AC-02`; barrido de `AC-13` en marcha |
| 2 | Motor de cobro: servicios, pagos, recibos, snapshot, folios, cancelación | **Terminado** — `AC-04` a `AC-09`, `AC-11` y `AC-16` pasan |
| 3 | Plantilla del recibo, PDF, liga pública, firma del cliente | **Terminado** — `AC-02`, `AC-03`, `AC-10`, `AC-12` y `AC-14` pasan |
| 4 | Pantalla de cobro e idempotencia, compartir | **Terminado** — `AC-18` y `AC-19` pasan. **Entregable al usuario real** |
| 5 | Contabilidad: panel, comparación mensual, quién me debe, CSV | Pendiente |
| 6 | Planes, límites, cupones, pasarela | Pendiente |

Al terminar el hito 4 el producto se entrega al usuario real, aunque no haya
panel ni planes: el panel se mira una vez por semana y el recibo se emite 20
veces al mes.

### Decisiones abiertas del PRD

| Sección | Tema | Estado |
|---|---|---|
| §14.1 | Firma de un tercero | **Resuelta e implementada**: se captura nombre y calidad de quien firma. Si firma el titular, el nombre se toma del recibo y no se teclea nada (§1.4) |
| §14.4 | Frontend | **Resuelta**: Jinja2 + HTMX + Alpine, server-rendered |
| RN-20 | "Firma capturada una sola vez" | **Resuelta**: se puede recapturar; "una sola vez" se opone a "cada vez que emites", no a "para siempre" |
| §14.2 | Gracia tras borrado de cuenta | Abierta. Bloquea la publicación de términos, no el desarrollo |
| §14.5 | Mercado Pago vs. Stripe | Abierta. Se decide al empezar el hito 6 |

Ventana contable del plan gratuito (hueco de §11.1): **solo mes actual**. Se
limita lo histórico —gráfica de 6 meses y CSV de meses anteriores—; "por cobrar"
y "quién me debe" siguen disponibles, porque son estado actual y no contabilidad
histórica.
