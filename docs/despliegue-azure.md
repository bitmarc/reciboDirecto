# Despliegue en Azure — entorno de demostración

Guía para poner en marcha en Azure lo construido hasta el hito 4: registro y
sesión, perfil del emisor con firma, directorio de clientes, motor de cobro,
recibos con PDF y liga pública, y la pantalla de cobro con compartir.

Está escrita para un grupo de recursos vacío y una cuenta activa. Los comandos
son de `az` (Azure CLI) y se pueden pegar tal cual desde Git Bash o PowerShell
con los ajustes que se indican.

> **Antes de enseñárselo a un cliente, lee [§8](#8-antes-de-abrir-la-demo-a-un-cliente).**
> Hay dos cosas que el PRD exige y que este despliegue **no** resuelve solo: el
> envío real de correo y el aviso de privacidad.

---

## 1. Qué se va a crear

| Recurso | Para qué | SKU sugerido |
|---|---|---|
| Azure Container Registry | Guardar la imagen de la aplicación | Basic |
| **Base de datos PostgreSQL** | Los datos | **Dos opciones, ver abajo** |
| Cuenta de almacenamiento (Blob) | Firmas, logos y PDF de recibos | Standard LRS |
| App Service Plan (Linux) | El cómputo | B1 |
| Web App para contenedores | La aplicación | — |

**Por qué contenedor y no código.** WeasyPrint renderiza a través de Pango, y
una versión distinta de Pango cambia tipografías y saltos de línea. La imagen ya
fija esas librerías nativas, así que desplegar el contenedor es lo único que
garantiza que el recibo salga en Azure igual que en tu máquina (§3.2 del PRD).

### 1.1 La base de datos: dos caminos

Todo lo demás es idéntico. Solo cambia de dónde sale `DATABASE_URL`.

| | **A — Azure Database for PostgreSQL** | **B — Neon** |
|---|---|---|
| Costo | ~15–16 USD/mes (`Standard_B1ms` + 32 GB) | Plan gratuito suficiente para una demo |
| Apagado | Manual, hasta 7 días seguidos | Automático a los 5 min de inactividad |
| Primer acceso tras inactividad | Instantáneo si está encendida | Unos cientos de ms de arranque en frío |
| Red | Reglas de firewall por IP | Accesible por internet, con TLS y credenciales |
| Dónde viven los datos | En tu suscripción | En un proveedor externo |
| Copias y restauración | Respaldos de Azure | Restauración a un punto en el tiempo, ramas |

**Costo total aproximado del despliegue:** unos **30–40 USD al mes** con la
opción A; unos **18–20 USD** con la B, porque la base de datos deja de pesar en
la factura. En ambos casos, los precios cambian por región y con el tiempo:
confírmalos antes de dejarlo encendido.

**Cuál elegir.** Para una demo y para desarrollo, la **opción B** es la
razonable: la mitad del costo, se apaga sola y no hay que pelearse con reglas de
firewall cada vez que cambie tu IP. La **opción A** tiene sentido cuando quieras
todo dentro de una sola suscripción y una sola factura, o cuando los datos no
puedan salir de tu tenant.

> **Lo que cambia el proveedor externo, y no es técnico.** §10 del PRD dice que
> la plataforma es el *encargado* del tratamiento de los datos de los clientes
> del emisor. Con Neon estás incorporando un **subencargado**, y el aviso de
> privacidad tiene que nombrarlo, junto con el país donde se alojan los datos.
> Para una demo con datos inventados da igual; en cuanto haya nombres y
> teléfonos reales, no. Ver [§8.2](#82-aviso-de-privacidad-y-términos).

En [§9](#9-costos-apagar-y-encender) está cómo bajar el costo cuando no lo estés
usando.

---

## 2. Preparación

### 2.1 Herramientas

```bash
az --version          # Azure CLI
az login
az account show       # confirma que es la suscripción correcta
```

Si tienes varias suscripciones:

```bash
az account set --subscription "<nombre o id>"
```

### 2.2 Variables de trabajo

Todo lo que sigue usa estas variables. **Ejecútalas en la misma terminal** donde
vas a correr el resto, o vuelve a definirlas si abres otra.

Tres nombres deben ser **únicos en todo Azure**: el registro, la cuenta de
almacenamiento y la aplicación. Por eso el sufijo.

```bash
RG=<tu-grupo-de-recursos>
LOC=southcentralus          # o mexicocentral, si tiene los SKU que necesitas
SUFIJO=<algo-tuyo-corto>    # p. ej. jp2608, solo minúsculas y dígitos

ACR=acrrecibo$SUFIJO        # 5-50 caracteres, solo letras y dígitos
STG=strecibo$SUFIJO         # 3-24 caracteres, solo minúsculas y dígitos
PG=pg-recibo-$SUFIJO        # solo si eliges la opción A
PLAN=plan-recibo
APP=recibo-$SUFIJO
```

En PowerShell la sintaxis es `$RG = "<tu-grupo>"`, y las referencias posteriores
también llevan `$`.

Al terminar [§3.2](#32-base-de-datos) tendrás definidas dos variables más, y son
las que usa todo lo que viene después:

| Variable | Para qué |
|---|---|
| `DB_URL` | La que va a la aplicación |
| `DB_URL_MIGRACIONES` | La que usas tú para aplicar migraciones |

Con la opción A las dos son iguales. Con Neon pueden no serlo, y por eso están
separadas desde el principio.

### 2.3 Secretos

```bash
# Firma las cookies de sesión. Sin esto no arranca la aplicación.
SESSION_SECRET=$(python -c "import secrets; print(secrets.token_urlsafe(48))")

echo "Guárdalo en tu gestor de contraseñas: SESSION_SECRET=$SESSION_SECRET"
```

Si eliges la **opción A**, necesitas además una contraseña para PostgreSQL:

```bash
PGPASS=$(python -c "import secrets,string; \
  a=string.ascii_letters+string.digits; \
  print(''.join(secrets.choice(a) for _ in range(28)))")

echo "PGPASS=$PGPASS"
```

> **Por qué la contraseña sale sin símbolos.** Viaja dentro de `DATABASE_URL`, y
> caracteres como `@ : / ? # [ ]` rompen la URL. Si prefieres usar símbolos,
> tendrás que codificarlos en porcentaje. Veintiocho caracteres alfanuméricos ya
> son más entropía de la que hace falta. Lo mismo vale para la contraseña que te
> genere Neon: si trae símbolos, hay que codificarlos.

---

## 3. Crear la infraestructura

### 3.1 Registro de contenedores

```bash
az acr create -g $RG -n $ACR --sku Basic
az acr update -n $ACR --admin-enabled true
```

El usuario administrador es la vía corta para que App Service pueda descargar la
imagen. En [§10.1](#101-cambiar-el-registro-a-identidad-administrada) está cómo
sustituirlo por identidad administrada, que es lo correcto para producción.

### 3.2 Base de datos

Haz **una** de las dos. Las dos terminan definiendo `DB_URL` y
`DB_URL_MIGRACIONES`, que es lo único que el resto de la guía necesita.

---

#### Opción A: Azure Database for PostgreSQL

```bash
az postgres flexible-server create \
  -g $RG -n $PG \
  --location $LOC \
  --tier Burstable --sku-name Standard_B1ms \
  --version 16 \
  --storage-size 32 \
  --admin-user recibo \
  --admin-password "$PGPASS" \
  --database-name recibo \
  --public-access 0.0.0.0 \
  --yes
```

`--public-access 0.0.0.0` no abre el servidor a internet: crea la regla
«permitir servicios de Azure», que es lo que necesita App Service. El servidor
exige TLS por omisión y así se queda.

Añade también tu propia IP, porque desde tu equipo vas a aplicar las
migraciones:

```bash
MI_IP=$(curl -s https://api.ipify.org)
az postgres flexible-server firewall-rule create \
  -g $RG -n $PG --rule-name mi-equipo \
  --start-ip-address $MI_IP --end-ip-address $MI_IP
```

> Si tu IP es dinámica, tendrás que actualizar esta regla cada vez que cambie.

Y define las dos variables:

```bash
DB_URL="postgresql+asyncpg://recibo:$PGPASS@$PG.postgres.database.azure.com:5432/recibo?ssl=require"
DB_URL_MIGRACIONES="$DB_URL"
```

---

#### Opción B: Neon

**1. Crea el proyecto.** En <https://console.neon.tech>, «New project»:

- **Nombre:** `recibo`
- **Versión de Postgres:** 16 o 17
- **Región:** la más cercana a la región de App Service. Neon tiene regiones en
  AWS y en Azure; si hay una de Azure que coincida con tu `$LOC`, elígela — cada
  consulta cruza la red, y la latencia se acumula en una pantalla que tiene un
  minuto (§1.4 del PRD).
- **Base de datos:** deja `neondb` o llámala `recibo`.

Con la CLI, si la prefieres:

```bash
npx neonctl auth
npx neonctl projects create --name recibo --region-id aws-us-east-2
```

**2. Copia la cadena de conexión.** En el panel del proyecto, *Connection
Details*. Verás algo así:

```
postgresql://recibo_owner:AbC123xyz@ep-cool-name-a1b2c3d4.us-east-2.aws.neon.tech/neondb?sslmode=require&channel_binding=require
```

**3. Transfórmala. Esta es la parte que hay que hacer bien.**

Esa cadena **no funciona tal cual**. Está escrita para `libpq`, y este proyecto
usa `asyncpg`, que no entiende esos parámetros. Comprobado contra la versión que
usa el proyecto: `?sslmode=require` levanta
`TypeError: connect() got an unexpected keyword argument 'sslmode'` y el
contenedor no arranca.

Tres cambios:

| Lo que da Neon | Lo que necesita el proyecto |
|---|---|
| `postgresql://` | `postgresql+asyncpg://` |
| `?sslmode=require&channel_binding=require` | `?ssl=require` |
| — | El resto igual |

Es decir:

```bash
DB_URL="postgresql+asyncpg://recibo_owner:AbC123xyz@ep-cool-name-a1b2c3d4.us-east-2.aws.neon.tech/neondb?ssl=require"
DB_URL_MIGRACIONES="$DB_URL"
```

Se sigue conectando por TLS: `ssl=require` es el parámetro que asyncpg sí
entiende, y Neon no acepta conexiones sin cifrar de todos modos.

**4. Usa el punto de conexión directo, no el del pooler.**

Neon ofrece dos anfitriones para la misma base: el directo y el del *pooler*,
que lleva `-pooler` en el nombre (`ep-cool-name-a1b2c3d4-pooler.…`). **Para este
proyecto, usa el directo en los dos casos.**

Por qué, en concreto:

- El pooler es PgBouncer en modo transacción, y `asyncpg` usa sentencias
  preparadas. Para que convivan hay que configurar el motor de SQLAlchemy con
  `prepared_statement_cache_size=0` y nombres únicos de sentencia — es decir,
  **tocar `src/receipt/db.py`**, que hoy no lleva nada de eso. Sin esos ajustes
  aparecen errores del tipo `prepared statement "…" already exists`, y aparecen
  de forma intermitente, que es la peor manera.
- No hace falta. El pooler resuelve el problema de las funciones sin servidor,
  que abren una conexión por petición. Esta aplicación es **un solo contenedor
  de larga vida con su propio pool**: `db.py` mantiene `pool_size=5` y
  `max_overflow=5`, o sea **diez conexiones como máximo**, muy por debajo del
  límite de cualquier cómputo de Neon. SQLAlchemy ya es el pool.
- Y las migraciones exigen conexión directa de todos modos.

Si algún día pasas a varias réplicas o a Container Apps con escalado a cero,
entonces sí toca el pooler — y con él, el ajuste en `db.py`.

**5. Ten en cuenta el arranque en frío.** Neon suspende el cómputo tras unos
minutos sin uso. La primera consulta después lo despierta y tarda unos cientos
de milisegundos. Dos consecuencias:

- En una demo, **abre la aplicación un minuto antes** de enseñarla.
- Las conexiones abiertas se cortan al suspenderse. Esto ya está cubierto:
  `db.py` usa `pool_pre_ping=True`, así que SQLAlchemy comprueba cada conexión
  antes de usarla y reconecta sola. No hay que cambiar nada.

**6. No hay reglas de firewall que configurar.** Neon es accesible por internet
y se protege con TLS y credenciales. Si quieres restringir por IP, la lista de
permitidos está en los planes de pago.

### 3.3 Almacenamiento

```bash
az storage account create \
  -g $RG -n $STG \
  --location $LOC \
  --sku Standard_LRS --kind StorageV2 \
  --min-tls-version TLS1_2 \
  --allow-blob-public-access false
```

**`--allow-blob-public-access false` no es opcional.** §3.3 del PRD exige que el
contenedor sea privado y que todo acceso pase por la aplicación: el PNG de una
firma es dato personal (§10). Este parámetro lo impone a nivel de cuenta, así
que ni un error de configuración posterior puede exponerlo.

Guarda la cadena de conexión y crea el contenedor:

```bash
STG_CONN=$(az storage account show-connection-string \
  -g $RG -n $STG --query connectionString -o tsv)

az storage container create --name receipts --connection-string "$STG_CONN"
```

La aplicación crea el contenedor sola si no existe, pero crearlo aquí deja claro
que nace privado.

---

## 4. Construir y publicar la imagen

Se construye **dentro de Azure**, así no dependes de tu Docker local ni de la
arquitectura de tu equipo:

```bash
az acr build -r $ACR -t recibo:v1 .
```

La última etapa del `Dockerfile` es `runtime`, que es justo la que se quiere, así
que no hace falta indicar objetivo. El `.dockerignore` deja fuera `.git`, las
cachés, el `.env` y el PRD.

Tarda unos minutos la primera vez. Al terminar:

```bash
az acr repository show-tags -n $ACR --repository recibo -o table
```

---

## 5. Crear la aplicación

### 5.1 Plan y aplicación

```bash
az appservice plan create -g $RG -n $PLAN --is-linux --sku B1

az webapp create -g $RG -p $PLAN -n $APP \
  --deployment-container-image-name $ACR.azurecr.io/recibo:v1
```

### 5.2 Credenciales del registro

```bash
ACR_USER=$(az acr credential show -n $ACR --query username -o tsv)
ACR_PASS=$(az acr credential show -n $ACR --query 'passwords[0].value' -o tsv)

az webapp config container set -g $RG -n $APP \
  --container-image-name $ACR.azurecr.io/recibo:v1 \
  --container-registry-url https://$ACR.azurecr.io \
  --container-registry-user "$ACR_USER" \
  --container-registry-password "$ACR_PASS"
```

### 5.3 Variables de entorno

Este es el paso donde se decide si la aplicación funciona o falla en silencio.

```bash
az webapp config appsettings set -g $RG -n $APP --settings \
  WEBSITES_PORT=8000 \
  ENVIRONMENT=production \
  DEBUG=false \
  SQL_ECHO=false \
  SESSION_SECRET="$SESSION_SECRET" \
  DATABASE_URL="$DB_URL" \
  AZURE_STORAGE_CONNECTION_STRING="$STG_CONN" \
  BLOB_CONTAINER=receipts \
  PUBLIC_BASE_URL="https://$APP.azurewebsites.net" \
  MAIL_BACKEND=console \
  MAIL_FROM="Recibo <no-responder@recibo.mx>"
```

Qué hace cada una y por qué importa:

| Variable | Valor | Qué pasa si está mal |
|---|---|---|
| `WEBSITES_PORT` | `8000` | La imagen escucha en 8000. Sin esto, App Service busca en 80 y devuelve 502. |
| `ENVIRONMENT` | `production` | Activa `Secure` en la cookie de sesión y **oculta `/docs`**. Con otro valor, la documentación interactiva queda pública. |
| `DEBUG` | `false` | Reduce el detalle del registro. |
| `SESSION_SECRET` | aleatorio | Firma la cookie. Si cambia, todas las sesiones abiertas se invalidan. |
| `DATABASE_URL` | `$DB_URL`, de [§3.2](#32-base-de-datos) | Sin `?ssl=require` la conexión intenta ir en claro y el servidor la rechaza. Con `sslmode` en vez de `ssl`, el contenedor ni arranca. |
| `AZURE_STORAGE_CONNECTION_STRING` | de §3.3 | Sin ella no hay firmas, ni logos, ni PDF. |
| `BLOB_CONTAINER` | `receipts` | — |
| `PUBLIC_BASE_URL` | la URL real, con `https` | Es la base de **la liga que se le manda al cliente** y de las ligas de recuperación de contraseña. Si queda en `localhost`, los recibos se comparten con una liga que no abre en ningún lado. |
| `MAIL_BACKEND` | `console` | Acepta `console`, `memory` o `azure`. Cualquier otro valor **impide arrancar**. Ver [§8.1](#81-el-correo-el-adaptador-ya-existe-hay-que-dar-de-alta-los-recursos). |
| `ACS_CONNECTION_STRING` | — | Solo con `MAIL_BACKEND=azure`, donde es **obligatoria**: sin ella el contenedor no levanta. Ver [`correo-azure-alta.md`](correo-azure-alta.md). |
| `ACS_SENDER_ADDRESS` | — | Igual que la anterior. Es la dirección pelada del remitente, sin nombre visible. |
| `LEGAL_RESPONSABLE` | — | Nombre o razón social de quien responde por los datos. **Obligatoria en producción**: el art. 15 de la LFPDPPP la exige en el aviso de privacidad. |
| `LEGAL_DOMICILIO` | — | Domicilio del responsable. Obligatoria en producción, por lo mismo. |
| `LEGAL_CONTACTO_ARCO` | — | Correo para ejercer los derechos ARCO. **Tiene que recibir de verdad**: el remitente de Communication Services es `DoNotReply` y solo envía. |

> **Sobre `?ssl=require`, y por qué no `sslmode`.** Ambas cosas están
> comprobadas contra la versión que usa el proyecto (SQLAlchemy 2.0.51 sobre
> asyncpg):
>
> - **Sin el parámetro**, la conexión va sin cifrar. Tanto Azure Database for
>   PostgreSQL como Neon la rechazan.
> - **Con `?sslmode=require`** —que es como lo escribe `libpq`, y lo que copias
>   del panel de Neon— falla con
>   `TypeError: connect() got an unexpected keyword argument 'sslmode'`. Lo mismo
>   con `channel_binding`. `asyncpg` no habla ese dialecto.
>
> El único parámetro correcto aquí es `ssl=require`.

### 5.4 HTTPS, arranque permanente y sonda de salud

```bash
az webapp update -g $RG -n $APP --https-only true

az webapp config set -g $RG -n $APP --always-on true

az webapp config set -g $RG -n $APP \
  --generic-configurations '{"healthCheckPath": "/healthz"}'
```

**`--https-only` no es cosmético.** En producción la cookie de sesión lleva el
atributo `Secure`, así que por HTTP el navegador la descarta y el inicio de
sesión falla sin decir por qué.

`--always-on` evita que el primer acceso tras un rato de inactividad tarde
varios segundos, que en una demo se nota.

---

## 6. Aplicar las migraciones

El esquema no se crea solo. Se aplica **desde tu equipo**, con la misma imagen
que se desplegó, para que sea un paso deliberado y quede registro de cuándo se
hizo.

```bash
docker run --rm \
  -e DATABASE_URL="$DB_URL_MIGRACIONES" \
  $ACR.azurecr.io/recibo:v1 alembic upgrade head
```

Si tu Docker local no tiene sesión en el registro:

```bash
az acr login -n $ACR
```

Comprueba en qué revisión quedó:

```bash
docker run --rm \
  -e DATABASE_URL="$DB_URL_MIGRACIONES" \
  $ACR.azurecr.io/recibo:v1 alembic current
```

Debe responder con la revisión `b3c7d1e94a20 (head)`.

> **Por qué no se ejecutan en el arranque del contenedor.** Con más de una
> instancia, dos arranques simultáneos aplicarían la misma migración a la vez.
> Y un despliegue que migra solo convierte cada reinicio en un cambio de
> esquema, que es lo último que quieres a las once de la noche.

Reinicia la aplicación para que tome todo:

```bash
az webapp restart -g $RG -n $APP
echo "https://$APP.azurewebsites.net"
```

---

## 7. Verificar que funciona

No basta con que cargue la portada. Recorre esto en orden; cada punto ejercita
una pieza distinta de la infraestructura.

```bash
curl -s -o /dev/null -w "healthz  %{http_code}\n" https://$APP.azurewebsites.net/healthz
curl -s -o /dev/null -w "entrar   %{http_code}\n" https://$APP.azurewebsites.net/entrar
curl -s -o /dev/null -w "docs     %{http_code}\n" https://$APP.azurewebsites.net/docs
```

`/healthz` y `/entrar` deben dar `200`. **`/docs` debe dar `404`**: si responde
`200`, `ENVIRONMENT` no quedó en `production`.

Después, en el navegador:

- [ ] **Registro** en `/registro` → entra al panel. *(Base de datos escribiendo.)*
- [ ] **Cerrar sesión y volver a entrar.** *(Cookie `Secure` sobre HTTPS.)*
- [ ] **Ajustes → dibujar la firma** y guardar; recargar y ver que sigue ahí.
      *(Blob Storage.)*
- [ ] **Cobrar** con un cliente nuevo, concepto, total acordado de 2 300 y monto
      de 1 800 → debe llevarte a la pantalla de resultado. *(Motor de cobro.)*
- [ ] En esa pantalla, **abrir la liga pública en una ventana privada**, sin
      sesión. Debe verse el recibo con saldo de 500. *(Liga pública, snapshot.)*
- [ ] **Descargar el PDF** desde esa misma ventana privada. *(WeasyPrint y blob.)*
- [ ] **Copiar liga** y **Compartir por WhatsApp**: la liga debe empezar con tu
      dominio real, no con `localhost`. *(`PUBLIC_BASE_URL`.)*
- [ ] **Cancelar el recibo** con un motivo y recargar la liga pública: debe
      seguir abriendo, con el sello de CANCELADO. *(RN-24.)*

Si algo falla, mira el registro:

```bash
az webapp log tail -g $RG -n $APP
```

---

## 8. Antes de abrir la demo a un cliente

### 8.1 El correo: el adaptador ya existe, hay que dar de alta los recursos

`MAIL_BACKEND` admite `console`, `memory` y **`azure`**. El adaptador contra
Azure Communication Services está construido y probado (`infra/mail_azure.py`).

**Con el valor por omisión, `console`, no sale ningún correo**: el mensaje y su
PDF adjunto se escriben al registro de la aplicación, y la liga de recuperación
se lee con `az webapp log tail`.

Para que salga de verdad hay que dar de alta dos recursos en Azure y poner tres
variables. El paso a paso está en
[`correo-azure-alta.md`](correo-azure-alta.md); es una tarde de trabajo con
dominio gestionado.

**La limitación que hay que conocer antes de decidir:** con dominio gestionado
por Azure el tope es de **10 correos por hora en toda la suscripción**, sin
posibilidad de ampliarlo, y lo comparten recibos, ligas de recuperación y
confirmaciones de alta. Basta para una demo; no basta para usuarios reales. El
dominio propio lo sube a 100/hora y sigue pendiente.

Para una demo: **`console` y enseñar el camino que de verdad importa** —§7.2 y
RN-32, compartir la liga por WhatsApp, que funciona por completo y no depende de
ningún proveedor—. Antes del primer usuario real, `azure` con dominio propio,
porque sin correo no hay recuperación de contraseña y una contraseña olvidada
deja al usuario fuera de su propia cuenta.

### 8.2 Aviso de privacidad y términos

§10 del PRD lo dice sin rodeos: el aviso de privacidad y los términos **deben
estar publicados antes del primer usuario real**, y deben dejar claro que el
emisor es el *responsable del tratamiento* de los datos de sus clientes y la
plataforma el *encargado*.

Esto importa desde el momento en que el cliente teclee el nombre y el teléfono
de una persona real. Si la demo va con datos inventados, puedes posponerlo; si
va a capturar clientes de verdad, no.

**Si elegiste Neon, hay un punto más.** El aviso tiene que nombrar a los
subencargados y decir dónde se alojan los datos. Con la opción A todo vive en tu
suscripción de Azure; con la B, la base de datos vive en Neon, en la región que
hayas elegido —probablemente fuera de México—. No es un impedimento: la LFPDPPP
admite la transferencia, pero exige declararla. Lo que no se puede es omitirla.

Sigue abierta además la decisión §14.2 —periodo de gracia tras borrado de
cuenta—, que hay que cerrar antes de publicar los términos.

### 8.3 Qué no existe todavía

Para que nadie lo busque en la demo:

- **Panel de ingresos, «quién me debe» y reporte CSV** — hito 5.
- **Cierre de un trabajo por incobrable** — hito 5.
- **Planes, límite del plan gratuito y cobro** — hito 6. Hoy **no hay bloqueo
  por límite**: se pueden emitir recibos sin tope.
- El logo está gateado por plan de pago (RN-34). Para enseñarlo en la demo hay
  que poner la cuenta en plan de pago a mano:

```bash
# Solo para la demo: activa el plan de pago de una cuenta.
docker run --rm -it \
  -e DATABASE_URL="$DB_URL_MIGRACIONES" \
  $ACR.azurecr.io/recibo:v1 \
  python -c "
import asyncio, os
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
async def main():
    e = create_async_engine(os.environ['DATABASE_URL'])
    async with e.begin() as c:
        await c.execute(text(\"UPDATE account SET plan='paid'\"))
    await e.dispose()
asyncio.run(main())
"
```

---

## 9. Costos, apagar y encender

Entre demos, apaga lo que se cobra por tiempo encendido.

**Con la opción A**, lo más caro son la base de datos y el plan de App Service:

```bash
# Detener la base de datos (hasta 7 días; después arranca sola).
az postgres flexible-server stop -g $RG -n $PG

# Detener la aplicación.
az webapp stop -g $RG -n $APP
```

Para volver:

```bash
az postgres flexible-server start -g $RG -n $PG
az webapp start -g $RG -n $APP
```

**Con la opción B** solo hay que apagar la aplicación: Neon suspende el cómputo
por su cuenta a los pocos minutos sin uso y el almacenamiento sigue ahí.

```bash
az webapp stop -g $RG -n $APP
az webapp start -g $RG -n $APP
```

Si vas a estar sin tocarlo mucho tiempo y quieres ahorrar también el `Always
On`, apágalo — a cambio, el primer acceso tardará:

```bash
az webapp config set -g $RG -n $APP --always-on false
```

Bajar el plan de App Service a gratuito **no sirve** aquí: el nivel F1 de Linux
no soporta contenedores propios ni `Always On`.

Regla de ciclo de vida para los PDF, si la demo se alarga (§13 del PRD sugiere
pasar a Cool a los 90 días):

```bash
az storage account management-policy create \
  --account-name $STG -g $RG --policy '{
    "rules": [{
      "name": "pdf-a-cool",
      "enabled": true,
      "type": "Lifecycle",
      "definition": {
        "filters": {"blobTypes": ["blockBlob"], "prefixMatch": ["accounts/"]},
        "actions": {"baseBlob": {"tierToCool": {"daysAfterModificationGreaterThan": 90}}}
      }
    }]
  }'
```

Para borrar todo cuando la demo termine:

```bash
az group delete -n $RG --yes --no-wait
```

---

## 10. Después de la demo

### 10.1 Cambiar el registro a identidad administrada

El usuario administrador del registro es una contraseña compartida guardada en
la configuración de la aplicación. Para algo que dure:

```bash
az webapp identity assign -g $RG -n $APP
PRINCIPAL=$(az webapp identity show -g $RG -n $APP --query principalId -o tsv)
ACR_ID=$(az acr show -n $ACR --query id -o tsv)

az role assignment create --assignee $PRINCIPAL --role AcrPull --scope $ACR_ID
az webapp config set -g $RG -n $APP \
  --generic-configurations '{"acrUseManagedIdentityCreds": true}'
az acr update -n $ACR --admin-enabled false
```

### 10.2 Secretos en Key Vault

`SESSION_SECRET`, la contraseña de PostgreSQL y la cadena de almacenamiento
están hoy en texto plano en la configuración de la aplicación. Con Key Vault y
referencias `@Microsoft.KeyVault(...)` dejan de estarlo.

### 10.3 Dominio propio

Cuando haya dominio, hay que actualizar `PUBLIC_BASE_URL` **y** tener presente
que las ligas ya emitidas apuntan al dominio anterior: `RN-30` promete que la
liga no caduca mientras exista la cuenta. Mantén el dominio viejo respondiendo,
o redirígelo.

### 10.4 Despliegue de una versión nueva

```bash
az acr build -r $ACR -t recibo:v2 .

# ¿La versión nueva trae migraciones? Aplícalas ANTES de cambiar la imagen.
docker run --rm \
  -e DATABASE_URL="$DB_URL_MIGRACIONES" \
  $ACR.azurecr.io/recibo:v2 alembic upgrade head

az webapp config container set -g $RG -n $APP \
  --container-image-name $ACR.azurecr.io/recibo:v2
az webapp restart -g $RG -n $APP
```

Etiquetas con versión, nunca `latest`: con `latest` no se sabe qué está
corriendo ni se puede volver atrás cambiando una línea.

---

## 11. Problemas frecuentes

| Síntoma | Causa probable |
|---|---|
| `502` o «Application Error» | Falta `WEBSITES_PORT=8000`, o el contenedor no arranca. Mira `az webapp log tail`. |
| Arranca pero falla al entrar a cualquier página | Falta alguna variable obligatoria: `SESSION_SECRET`, `DATABASE_URL` o `AZURE_STORAGE_CONNECTION_STRING`. La configuración no tiene valores por omisión para ellas. |
| El inicio de sesión «no hace nada» y vuelve al formulario | Estás entrando por HTTP. En producción la cookie es `Secure`. Comprueba `--https-only`. |
| `relation "account" does not exist` | Faltan las migraciones ([§6](#6-aplicar-las-migraciones)). |
| Error de SSL contra PostgreSQL | Falta `?ssl=require` al final de `DATABASE_URL`. |
| `TypeError: connect() got an unexpected keyword argument 'sslmode'` | Pegaste la cadena de Neon tal cual. Cambia `sslmode`/`channel_binding` por `ssl=require` ([§3.2 B](#opción-b-neon)). |
| `InvalidAuthorizationSpecificationError` o «no such host» con Neon | Falta el prefijo `postgresql+asyncpg://`, o el nombre del *endpoint* está incompleto. |
| `prepared statement "…" already exists`, y de forma intermitente | Estás usando el anfitrión con `-pooler`. Cambia al directo ([§3.2 B](#opción-b-neon)). |
| La primera petición del día tarda un segundo (Neon) | Arranque en frío tras la suspensión. Es lo esperado; abre la aplicación un minuto antes de la demo. |
| No conecta a PostgreSQL desde tu equipo (opción A) | Tu IP no está en el firewall, o cambió. |
| La firma se guarda pero no aparece | Revisa la cadena de almacenamiento y que el contenedor sea `receipts`. |
| El PDF da error 500 | Casi siempre es el almacenamiento, no WeasyPrint: las imágenes se descargan del blob antes de renderizar. |
| La liga compartida abre `localhost` | `PUBLIC_BASE_URL` quedó con el valor de desarrollo. |
| `/docs` responde 200 | `ENVIRONMENT` no es `production`. |
| El contenedor arranca y muere al instante | Un valor inválido en `MAIL_BACKEND` (solo `console`, `memory` o `azure`); `azure` sin `ACS_CONNECTION_STRING` / `ACS_SENDER_ADDRESS`; o producción sin los tres `LEGAL_*`. El registro nombra cuál falta. |
| «Enviar por correo» responde 502 | El proveedor rechazó el envío. Con dominio gestionado, casi siempre es la cuota: 10/hora. Ver [`correo-azure-alta.md`](correo-azure-alta.md) §8. |

---

## Apéndice — resumen de variables de entorno

| Variable | Obligatoria | Valor en la demo |
|---|---|---|
| `WEBSITES_PORT` | sí (App Service) | `8000` |
| `ENVIRONMENT` | sí | `production` |
| `DEBUG` | no | `false` |
| `SQL_ECHO` | no | `false` |
| `SESSION_SECRET` | **sí** | aleatorio, ≥ 48 caracteres |
| `SESSION_COOKIE_NAME` | no | `receipt_session` |
| `SESSION_MAX_AGE_DAYS` | no | `30` |
| `DATABASE_URL` | **sí** | `postgresql+asyncpg://…?ssl=require` — nunca `sslmode` |
| `AZURE_STORAGE_CONNECTION_STRING` | **sí** | cadena de la cuenta |
| `BLOB_CONTAINER` | no | `receipts` |
| `PUBLIC_BASE_URL` | sí en la práctica | `https://<app>.azurewebsites.net` |
| `MAIL_BACKEND` | no | `console` en la demo; `azure` envía de verdad |
| `MAIL_FROM` | no | `Recibo <no-responder@recibo.mx>` — solo lo usa `console` |
| `ACS_CONNECTION_STRING` | **sí con `azure`** | `endpoint=https://…;accesskey=…` |
| `ACS_SENDER_ADDRESS` | **sí con `azure`** | `DoNotReply@<guid>.azurecomm.net` |
| `LEGAL_RESPONSABLE` | **sí en producción** | Nombre o razón social |
| `LEGAL_DOMICILIO` | **sí en producción** | Domicilio del responsable |
| `LEGAL_CONTACTO_ARCO` | **sí en producción** | Un buzón que reciba |
| `LOGIN_MAX_ATTEMPTS` | no | `8` |
| `LOGIN_WINDOW_SECONDS` | no | `300` |
| `PUBLIC_RECEIPT_MAX_REQUESTS` | no | `60` |
| `PUBLIC_RECEIPT_WINDOW_SECONDS` | no | `60` |

`TEST_DATABASE_URL` es solo para la suite de pruebas; no se configura en Azure.
