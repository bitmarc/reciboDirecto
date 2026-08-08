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
  infra/         Blob Storage, correo y, desde el hito 3, PDF.
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
  PostgreSQL no tiene secuencias por fila y los folios son por cuenta.
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

`MAIL_BACKEND=console` escribe el correo al log en lugar de enviarlo. La liga de
recuperación aparece en `docker compose logs -f app`. El proveedor real se
enchufa al desplegar, que es cuando el hito 4 también necesitará enviar recibos.

---

## Estado

| Hito | Alcance | Estado |
|---|---|---|
| 0 | Cimientos: Docker, esquema completo, migraciones, autenticación, CI | **Terminado** — `AC-01` pasa |
| 1 | Perfil del emisor, firma, logo, clientes, recuperación de contraseña | **Terminado** — media parte de `AC-02`; barrido de `AC-13` en marcha |
| 2 | Motor de cobro: servicios, pagos, recibos, snapshot, folios, cancelación | Pendiente |
| 3 | Plantilla del recibo, PDF, liga pública, firma del cliente | Pendiente |
| 4 | Pantalla de cobro e idempotencia, compartir | Pendiente |
| 5 | Contabilidad: panel, comparación mensual, quién me debe, CSV | Pendiente |
| 6 | Planes, límites, cupones, pasarela | Pendiente |

Al terminar el hito 4 el producto se entrega al usuario real, aunque no haya
panel ni planes: el panel se mira una vez por semana y el recibo se emite 20
veces al mes.

### Decisiones abiertas del PRD

| Sección | Tema | Estado |
|---|---|---|
| §14.1 | Firma de un tercero | **Resuelta**: se captura nombre y calidad de quien firma |
| §14.4 | Frontend | **Resuelta**: Jinja2 + HTMX + Alpine, server-rendered |
| RN-20 | "Firma capturada una sola vez" | **Resuelta**: se puede recapturar; "una sola vez" se opone a "cada vez que emites", no a "para siempre" |
| §14.2 | Gracia tras borrado de cuenta | Abierta. Bloquea la publicación de términos, no el desarrollo |
| §14.5 | Mercado Pago vs. Stripe | Abierta. Se decide al empezar el hito 6 |

Ventana contable del plan gratuito (hueco de §11.1): **solo mes actual**. Se
limita lo histórico —gráfica de 6 meses y CSV de meses anteriores—; "por cobrar"
y "quién me debe" siguen disponibles, porque son estado actual y no contabilidad
histórica.
