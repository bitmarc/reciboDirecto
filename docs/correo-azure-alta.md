# Alta del correo en Azure — paso a paso

Guía de ejecución para dejar `MAIL_BACKEND=azure` funcionando. El diseño y el
porqué de cada decisión están en [`plan-correo-azure.md`](plan-correo-azure.md);
esto es la parte de teclear.

**Antes de empezar:** el código ya está. Fase A terminada — adaptador, config,
manejo de fallos en las cuatro rutas y 29 pruebas nuevas. Lo que falta es la
infraestructura.

---

## 0. Lo que hay que entender antes de crear nada

El tropiezo habitual con este servicio es que **hacen falta dos recursos
distintos y ninguno de los dos sirve solo**:

```
┌─────────────────────────────────────┐
│ Email Communication Service         │   ← aloja el dominio remitente
│   └── Dominio (gestionado o propio) │      NO da cadena de conexión
└─────────────────────────────────────┘
                  │
                  │  (4) enlazar
                  ▼
┌─────────────────────────────────────┐
│ Communication Services              │   ← da la CADENA DE CONEXIÓN
│   └── Dominios conectados           │      NO puede alojar dominios
└─────────────────────────────────────┘
```

Si se crea solo el primero, no hay credencial. Si se crea solo el segundo, no
hay remitente. Y si se crean los dos pero **no se enlazan**, el envío falla con
un error de autorización que no menciona en ningún sitio que falte el enlace.
Ese es el paso que se olvida.

Nombres usados en esta guía —cámbialos por los tuyos:

| Variable | Valor de ejemplo |
|---|---|
| Grupo de recursos | `rg-recibo` |
| Email Communication Service | `recibo-email` |
| Communication Services | `recibo-acs` |
| App Service | `recibo-app` |

---

## 1. Crear el Email Communication Service

Los comandos viven en una **extensión** del CLI, y el grupo `communication
email` está **en vista previa**. Necesita `az` 2.67.0 o superior.

```bash
az extension add --name communication

az communication email create \
  --name recibo-email \
  --resource-group rg-recibo \
  --location global \
  --data-location unitedstates
```

`--location` es `global` para este recurso (es también el valor por omisión). El
que decide dónde viven los datos es `--data-location`, va en **minúsculas y sin
espacios** —`unitedstates`, `europe`, `australia`...— y **no se puede cambiar
después**. **México no está en la lista**, así que hay que elegir la
jurisdicción aceptable más cercana.

> **Esto tiene consecuencia legal, no solo técnica.** El aviso de privacidad
> pendiente ([`pendientes.md`](pendientes.md) §1.1) tiene que decir en qué país
> se procesan los datos personales. La dirección de correo del cliente de un
> emisor es dato personal, y con esto sale del país. Anota el valor elegido: va
> literal en el aviso.

---

## 2. Añadir el dominio remitente

**Decidido: dominio gestionado por Azure**, con el tope de 10 correos/hora
asumido a conciencia (ver el plan, §1). Es instantáneo y no toca DNS.

```bash
az communication email domain create \
  --domain-name AzureManagedDomain \
  --email-service-name recibo-email \
  --resource-group rg-recibo \
  --domain-management AzureManaged
```

El nombre `AzureManagedDomain` es literal, no un marcador de posición: es el
identificador que espera el proveedor para este modo. `--location` se omite
porque su valor por omisión ya es `Global`.

Azure genera un dominio del tipo `abc12345-....azurecomm.net` y un remitente
`DoNotReply@<ese-dominio>`. Para verlo:

```bash
az communication email domain show \
  --domain-name AzureManagedDomain \
  --email-service-name recibo-email \
  --resource-group rg-recibo \
  --query "{dominio:mailFromSenderDomain, verificado:verificationStates}"
```

### Nombre visible del remitente

`senderAddress` viaja como dirección pelada; el nombre que ve el destinatario es
una propiedad del recurso, no del código:

```bash
az communication email domain sender-username create \
  --domain-name AzureManagedDomain \
  --email-service-name recibo-email \
  --resource-group rg-recibo \
  --sender-username DoNotReply \
  --username DoNotReply \
  --display-name Recibo
```

`--sender-username` y `--username` **tienen que llevar el mismo valor**; lo dice
el propio ejemplo de la documentación y es un tropiezo fácil.

Sin esto el cliente ve `donotreply@abc12345...azurecomm.net` a secas, que con un
PDF adjunto tiene toda la pinta de un fraude. Se puede hacer igual desde el
portal: **`recibo-email` → Provision domains → el dominio → MailFrom addresses**.

---

## 3. Crear el Communication Services, ya enlazado

Este es el que emite la credencial. Como el dominio ya existe, se enlaza **en el
mismo comando** y el paso 4 se ahorra:

```bash
DOMINIO_ID=$(az communication email domain show \
  --domain-name AzureManagedDomain \
  --email-service-name recibo-email \
  --resource-group rg-recibo \
  --query id -o tsv)

az communication create \
  --name recibo-acs \
  --resource-group rg-recibo \
  --location global \
  --data-location unitedstates \
  --linked-domains $DOMINIO_ID
```

`--data-location` **tiene que coincidir** con la del paso 1.

---

## 4. Comprobar el enlace (el paso que se olvida)

Si el recurso ya existía, o si se creó sin `--linked-domains`, el enlace se
añade después:

```bash
az communication update \
  --name recibo-acs \
  --resource-group rg-recibo \
  --linked-domains $DOMINIO_ID
```

En cualquiera de los dos casos, **comprueba que quedó**:

```bash
az communication show \
  --name recibo-acs \
  --resource-group rg-recibo \
  --query linkedDomains
```

Si devuelve una lista vacía, los envíos van a fallar con
`Unauthorized / sender domain not linked` por mucho que la cadena de conexión
sea correcta. Es el fallo que más tiempo hace perder, porque el mensaje de error
no menciona en ningún sitio que falte el enlace.

---

## 5. Obtener la cadena de conexión

```bash
az communication list-key \
  --name recibo-acs \
  --resource-group rg-recibo \
  --query primaryConnectionString -o tsv
```

Sale algo con esta forma:

```
endpoint=https://recibo-acs.communication.azure.com/;accesskey=<clave-larga>
```

> **Es un secreto del mismo rango que la cadena de Storage.** No va al
> repositorio, ni a un `docs/`, ni a `infra/envs.txt` — que **ya está en
> `.gitignore`** desde este cambio, y conviene que siga así. Si alguna vez se
> filtra, se rota con
> `az communication regenerate-key --name recibo-acs -g rg-recibo --key-type Primary`.

---

## 6. Configurar la aplicación

### En Azure

```bash
az webapp config appsettings set \
  --name recibo-app \
  --resource-group rg-recibo \
  --settings \
    MAIL_BACKEND=azure \
    ACS_CONNECTION_STRING="endpoint=https://recibo-acs.communication.azure.com/;accesskey=..." \
    ACS_SENDER_ADDRESS="DoNotReply@abc12345-....azurecomm.net"
```

Las tres van juntas. `config.py` valida al arrancar: con `MAIL_BACKEND=azure` y
cualquiera de las otras dos vacía, **el contenedor no levanta** y el log dice
cuál falta. Es deliberado — una aplicación que arranca creyendo que manda correo
y no lo manda falla en silencio hasta que alguien se queja.

### En local, para probar contra el servicio real

En `.env` (que está en `.gitignore`):

```ini
MAIL_BACKEND=azure
ACS_CONNECTION_STRING=endpoint=https://recibo-acs.communication.azure.com/;accesskey=...
ACS_SENDER_ADDRESS=DoNotReply@abc12345-....azurecomm.net
```

Y `docker compose restart app`. Para volver al modo sin envíos, `MAIL_BACKEND=console`.

### Tabla de variables

| Variable | ¿Obligatoria? | Valor | Dónde sale |
|---|---|---|---|
| `MAIL_BACKEND` | no (`console`) | `console`, `memory` o `azure` | — |
| `ACS_CONNECTION_STRING` | **sí con `azure`** | `endpoint=...;accesskey=...` | paso 5 |
| `ACS_SENDER_ADDRESS` | **sí con `azure`** | `DoNotReply@<guid>.azurecomm.net` | paso 2 |
| `MAIL_FROM` | no | Solo lo usa `ConsoleMailer` para el log | — |

---

## 7. Prueba de humo

La suite cubre el mapeo y los fallos, pero **no puede cubrir lo único que
importa aquí**: que el correo llegue, que el PDF se abra y que no caiga en spam.
Eso se comprueba a mano, una vez, contra el servicio real.

### Primero, sin la aplicación

Antes de tocar la app, comprueba que la infraestructura sola funciona. Si esto
falla, el problema está en los pasos 1-5 y no en el código:

```bash
az communication email send \
  --connection-string "endpoint=https://recibo-acs.communication.azure.com/;accesskey=..." \
  --sender "DoNotReply@abc12345-....azurecomm.net" \
  --to "tu-correo@example.com" \
  --subject "Prueba de infraestructura" \
  --text "Si esto llega, los cinco primeros pasos están bien."
```

Este comando **espera a la entrega** por omisión (`--wait-until completed`), así
que te dirá si el reparto terminó bien. Con `--wait-until started` solo espera a
que Azure lo acepte, que es justo lo que hace el adaptador de la aplicación:
comparar los dos es la forma más rápida de entender la diferencia.

### Después, por la aplicación

Con `MAIL_BACKEND=azure` apuntando a una dirección tuya:

1. **Recuperación** — `/recuperar` con tu correo. Debe llegar la liga, abrirse y
   dejar cambiar la contraseña. Verifica que la liga apunta al `PUBLIC_BASE_URL`
   correcto y no a `localhost`.
2. **Confirmación** — `/ajustes` → «Reenviar la liga de confirmación».
3. **Recibo con adjunto** — cobra algo a un cliente con tu correo y pulsa
   enviar. **Abre el PDF.** Es la prueba que ninguna unitaria sustituye: la
   codificación base64 puede estar mal y el correo llegar igual, con un adjunto
   corrupto.
4. **Revisa la carpeta de spam** en Gmail y en Outlook. Con dominio gestionado
   es donde suele acabar, y saberlo cambia qué se le dice al emisor.

### Dos cosas que hay que resolver aquí, no antes

Están marcadas como pendientes de verificación en el plan porque no las doy por
supuestas:

- **Que abandonar el *poller* no cancele el envío.** `send()` espera al POST
  inicial y sale del `async with` sin llamar a `result()`. Lo esperado es que el
  mensaje ya esté encolado tras el 202. Si en la prueba de humo **no llega
  ningún correo pese a no haber error**, es esto: entonces hay que esperar al
  poller y aceptar los segundos de más.
- **Si `senderAddress` acepta `Nombre <dirección>`.** Prueba a poner
  `ACS_SENDER_ADDRESS="Recibo <DoNotReply@...>"`. Si Azure lo acepta, el nombre
  visible se controla desde la config; si lo rechaza, se queda como en el paso 2
  y `ACS_SENDER_ADDRESS` debe ser la dirección pelada.

### Cuando falle por cuota

Con 10 correos/hora, agotar el cupo probando es fácil. El síntoma es
`(TooManyRequests)` en el log y, en pantalla, el 502 con el mensaje que empuja a
WhatsApp. **No es un bug**: es la rama que se construyó para esto. Espera a la
hora siguiente.

---

## 8. Qué mirar cuando algo no sale

| Síntoma | Causa probable |
|---|---|
| El contenedor arranca y muere | Falta `ACS_CONNECTION_STRING` o `ACS_SENDER_ADDRESS` con `MAIL_BACKEND=azure`. El log lo nombra. |
| `Unauthorized`, `sender domain not linked` | Falta el paso 4, el enlace. Comprueba `linkedDomains`. |
| `(InvalidSenderAddress)` | `ACS_SENDER_ADDRESS` no coincide con el `MailFrom` del dominio, o lleva nombre visible y esta versión no lo admite. |
| `(TooManyRequests)` | Cuota agotada: 5/min, 10/hora. No hay arreglo con dominio gestionado. |
| Ningún error pero no llega nada | Mira spam primero. Si tampoco está, ver lo del *poller* en §7. |
| El PDF llega corrupto | Codificación del adjunto. Las unitarias de `test_mail_azure.py` cubren el ida y vuelta de base64; si están verdes, sospecha del PDF antes que del correo. |
| La liga del correo apunta a `localhost` | `PUBLIC_BASE_URL` mal puesto en App Settings. |

Métricas de entrega y rebotes: **portal → `recibo-acs` → Insights → Email**.
Ahí se ven entregas, rebotes y throttling, que es información que la aplicación
no tiene porque no espera al reparto.

---

## 9. Cuando llegue el dominio propio

Queda pendiente y es lo que sube la cuota a 30/min y 100/hora, ampliables a
petición (hasta 72 h de trámite). Resumen del cambio:

1. Crear el dominio:

   ```bash
   az communication email domain create \
     --domain-name recibo.mx \
     --email-service-name recibo-email \
     --resource-group rg-recibo \
     --domain-management CustomerManaged
   ```

2. Publicar los registros DNS que devuelve `az communication email domain show`:
   **TXT** de verificación, **SPF** y **DKIM**.

3. Lanzar la verificación de cada uno por separado —hay que repetir el comando
   cambiando `--verification-type`, que acepta `Domain`, `SPF`, `DKIM`, `DKIM2`
   y `DMARC`:

   ```bash
   az communication email domain initiate-verification \
     --domain-name recibo.mx \
     --email-service-name recibo-email \
     --resource-group rg-recibo \
     --verification-type Domain
   ```

4. Dar de alta el remitente con su nombre visible
   (`sender-username create`, como en el paso 2), enlazar el nuevo dominio
   (paso 4) y cambiar `ACS_SENDER_ADDRESS`.

Y una advertencia que no es de correo pero se cruza aquí: si además el dominio
pasa a servir la aplicación, `PUBLIC_BASE_URL` cambia con él, y **RN-30 dice que
las ligas públicas no caducan mientras exista la cuenta**. El dominio viejo
tiene que seguir resolviendo, o los recibos ya entregados a clientes reales se
rompen.
