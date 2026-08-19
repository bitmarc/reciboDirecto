# Plan — envío de correo con Azure Communication Services

**Estado:** propuesta, sin implementar.
**Cierra:** `docs/pendientes.md` §1.2 («No hay adaptador de correo real»).
**Fecha:** 13 de agosto de 2026.

---

## 0. De qué tamaño es el trabajo

El puerto ya existe y está probado. `Mailer` es un `Protocol` de un solo método,
`Message` ya lleva adjuntos desde el hito 4, y hay 549 pruebas verdes que usan
`RecordingMailer`. Lo que falta es **una clase que implemente `send()`** contra
Azure, un valor más en un `Literal`, y —esta es la parte que no es mecánica— **decidir
qué pasa cuando un envío falla**, porque hoy no puede fallar.

`ConsoleMailer` escribe al log y devuelve. Nunca lanza. Los cuatro puntos que
mandan correo están escritos bajo ese supuesto, y ninguno lo dice en voz alta.
Enchufar un proveedor real convierte cuatro rutas felices en cuatro rutas con
un modo de fallo nuevo, y en una de ellas —la recuperación de contraseña— el
fallo choca de frente con una garantía de seguridad que ya está en el código.

Ahí está el diseño. El adaptador es la parte fácil.

---

## 1. La decisión que condiciona todo: qué dominio remitente

Azure ofrece dos caminos y **no son equivalentes**. Cifras de la documentación
de límites de servicio, verificadas hoy:

| | Dominio gestionado por Azure | Dominio propio |
|---|---|---|
| Remitente | `DoNotReply@<guid>.azurecomm.net` | `no-responder@recibo.mx` |
| Alta | minutos, sin DNS | horas o días (TXT, SPF, DKIM) |
| Envíos por minuto | 5 | 30 |
| **Envíos por hora** | **10** | **100** |
| ¿Se puede ampliar? | **No** | Sí, a petición (hasta 72 h) |

Dos consecuencias que importan a este producto en concreto:

**La cuota es por suscripción, no por dominio.** Diez correos por hora es el
total de todo lo que la suscripción manda: recibos, ligas de recuperación y
confirmaciones de alta comparten el mismo cubo. Un plomero que emite diez
recibos en una mañana ocupada agota la cuota entera y deja sin liga de
recuperación a cualquiera que la pida esa hora. No es un límite holgado con el
que se pueda arrancar y ya veremos: es un límite que un solo usuario real
alcanza en un día normal.

**El correo del recibo va a un desconocido.** No al emisor —a *su cliente*, que
acaba de pagar en efectivo y no ha oído hablar de nosotros. Un remitente en
`azurecomm.net`, con un PDF adjunto y una liga a `*.azurewebsites.net`, tiene
exactamente la forma de un intento de phishing. `services/sharing.py` ya razona
justo eso al explicar por qué el mensaje lleva monto y folio: «un mensaje con
una liga pelada parece un fraude». Un remitente que nadie reconoce es el mismo
problema un escalón más arriba.

### Decidido: dominio gestionado por ahora

**Se arranca con el dominio gestionado y el tope de 10 correos por hora.** No
hay dominio propio todavía; la fase C queda pendiente y con ella la posibilidad
de enseñárselo a un emisor real.

Esto no es un detalle de configuración que se cambie luego sin consecuencias.
Cambia lo que hay que construir, en dos sitios:

**El 429 deja de ser un caso raro.** Con 10 por hora, agotar la cuota no es un
imprevisto de infraestructura: es el comportamiento esperado de una mañana con
trabajo. El manejo de fallos de §4 —sobre todo el 502 de §4.4, que empuja al
emisor hacia WhatsApp y la liga— no es defensa contra lo improbable, es la ruta
que se va a recorrer de verdad. Se construye completo desde el primer día, con
su prueba, y no se recorta «porque casi nunca pasa».

**La liga es el canal principal; el correo es el secundario.** Copiar la liga y
compartir por WhatsApp no dependen del proveedor, no tienen cuota y salen del
número del propio emisor, que es lo que el cliente espera de alguien a quien
acaba de pagar. Mientras el remitente sea `@azurecomm.net` con tope de 10/hora,
el correo al cliente es la opción de respaldo, no la recomendada — y la interfaz
no debería sugerir lo contrario.

Cuando llegue el dominio propio (`docs/despliegue-azure.md` §10.3 ya tiene
anotado el trámite), sube a 100/hora ampliables y la relación se invierte. El
dominio de correo no tiene por qué ser el mismo que el de la aplicación —son
trámites independientes—, aunque un remitente `@recibo.mx` con ligas a
`azurewebsites.net` vuelve a oler raro, así que conviene resolver los dos a la
vez.

---

## 2. Qué hay que dar de alta en Azure

Hacen falta **dos recursos distintos**, y es el tropiezo habitual: la cadena de
conexión no la da el servicio de correo.

1. **Email Communication Service** — aloja el dominio remitente. Al crearlo se
   elige la *ubicación de datos*, que es una decisión con consecuencias legales:
   el aviso de privacidad pendiente (`docs/pendientes.md` §1.1) tiene que
   nombrar en qué país viven los datos, y esto añade un país más a esa lista.
2. **Dominio** dentro del recurso anterior — gestionado o propio (§1).
3. **Communication Services** — el recurso «madre». Es el que emite la **cadena
   de conexión** que usa el SDK.
4. **Enlazar** el dominio al recurso de Communication Services. Sin este paso el
   envío falla con un error de remitente no autorizado que no dice que falte el
   enlace.

La cadena de conexión es un secreto del mismo rango que la de Storage: va en
*App Settings*, nunca al repositorio.

> **Bloqueante de seguridad.** `infra/envs.txt` **no está en `.gitignore`** y ya
> contiene credenciales reales. Hoy está sin rastrear, así que no ha entrado al
> historial. Añadir un tercer secreto ahí lo empeora. Hay que meterlo en
> `.gitignore` **antes** de que esta fase toque nada.

---

## 3. El adaptador

Módulo nuevo `src/receipt/infra/mail_azure.py`, para que `infra/mail.py` siga
siendo el puerto sin dependencias de proveedor. Espeja la estructura de
`infra/blob.py`: cliente creado por llamada dentro de un `async with`, sin
singleton de conexión.

```python
import base64
from typing import Any

from azure.communication.email.aio import EmailClient
from azure.core.exceptions import AzureError

from receipt.infra.mail import Attachment, MailDeliveryError, Message

# El límite de Azure son 10 MB por petición, adjuntos incluidos, y base64 infla
# los bytes un tercio. Se corta en 7 MB para fallar aquí, con un mensaje que
# dice qué pasa, en vez de en el servicio con un error opaco.
MAX_ATTACHMENT_BYTES = 7 * 1024 * 1024


def to_payload(message: Message, *, sender: str) -> dict[str, Any]:
    """Traduce el `Message` del puerto a la forma que espera el SDK.

    Es una función pura y aparte del cliente a propósito: el mapeo es lo único
    que se puede equivocar en silencio, y así se prueba sin red.
    """
    payload: dict[str, Any] = {
        "senderAddress": sender,
        "recipients": {"to": [{"address": message.to}]},
        "content": {"subject": message.subject, "plainText": message.body},
    }
    if message.attachments:
        payload["attachments"] = [
            {
                "name": a.filename,
                "contentType": a.content_type,
                "contentInBase64": base64.b64encode(a.content).decode("ascii"),
            }
            for a in message.attachments
        ]
    return payload


class AzureMailer:
    def __init__(self, connection_string: str, sender: str) -> None:
        self._connection_string = connection_string
        self._sender = sender

    async def send(self, message: Message) -> None:
        _check_size(message.attachments)
        try:
            async with EmailClient.from_connection_string(self._connection_string) as client:
                await client.begin_send(to_payload(message, sender=self._sender))
        except AzureError as exc:
            # El destinatario no se registra: la dirección del cliente de un
            # emisor es dato personal y el log de App Service no es el sitio.
            logger.exception("El proveedor rechazó un envío")
            raise MailDeliveryError(
                "No pudimos enviar el correo en este momento."
            ) from exc
```

### Por qué `begin_send` y no `poller.result()`

`begin_send` es una operación de larga duración: hace el POST inicial, Azure
responde 202 con un identificador, y el *poller* consulta después hasta que la
entrega termina. Esperar el resultado añade segundos a la petición HTTP.

Se espera **solo al POST inicial**. Ahí es donde aparecen todos los errores
sobre los que podemos hacer algo —remitente no autorizado, credencial mala,
cuota agotada (429 con `Retry-After`), adjunto demasiado grande— y ninguno de
los que aparecen después, que son rebotes y van a las métricas de Azure, no a
una pantalla. La pantalla de resultado de cobro tiene un presupuesto de un
minuto para el flujo entero (§1.4); gastarlo esperando a que Azure termine de
repartir sería pagar mucho por información que el emisor no puede usar.

> **Verificar empíricamente:** que abandonar el *poller* al salir del
> `async with` no cancela el envío ya aceptado. El comportamiento esperado es
> que no lo cancele —el mensaje ya está encolado tras el 202—, pero es la clase
> de suposición que conviene comprobar contra el servicio real en la fase B, no
> deducir del README.

### El nombre visible del remitente

`senderAddress` en el SDK es una dirección pelada. El nombre que ve el
destinatario se configura en el portal, en el *sender username* del dominio.
`MAIL_FROM` (`ReciboDirecto <no-responder@recibo.mx>`) se queda como está para que
`ConsoleMailer` siga siendo legible en local, y la dirección de Azure viaja en
un ajuste propio.

> **Verificar:** si `senderAddress` acepta la forma `Nombre <dirección>`. Hay
> versiones de la API que sí. Si la acepta, se simplifica a un solo ajuste; si
> no, el nombre se queda en el portal. No lo doy por hecho.

---

## 4. Qué pasa cuando un envío falla

Esta es la parte que hay que decidir, no programar. Error nuevo en
`infra/mail.py`, junto al puerto:

```python
class MailDeliveryError(RuntimeError):
    """El proveedor no aceptó el mensaje.

    **No hereda de `DomainError`.** No es un dato mal capturado por el usuario:
    es el sistema que falló. Mezclarlos haría que las rutas contestaran 400 y
    culparan al emisor de una cuota agotada que no es suya.
    """
```

Los cuatro puntos de envío, y qué debe hacer cada uno:

### 4.1 Alta de cuenta — `routes/auth.py:131`

**El alta se completa igual.** La cuenta ya existe cuando se manda el correo, y
`send_email_verification` ya documenta que la verificación «no bloquea el uso»:
tumbar un registro por un correo caído sería un autogol. Se atrapa, se registra
en el log y se sigue; el usuario entra con sesión iniciada.

El token ya emitido se compromete sin problema: `/ajustes` tiene el botón
«Reenviar la liga de confirmación», que lo invalida y emite otro.

La decisión de política va en la ruta, no en el servicio: `auth_tokens.py` no
tiene por qué saber que a un registro le da igual y a un reenvío no.

### 4.2 Recuperación de contraseña — `routes/auth.py:179`

**El caso incómodo.** La ruta contesta hoy *siempre lo mismo* —`_RESET_SENT`—
exista o no el correo, y el comentario explica por qué: decir «ese correo no
existe» convertiría el formulario en un detector de cuentas dadas de alta.

Un fallo de envío rompe eso. Si mostramos un error de transporte, solo puede
aparecer cuando *sí* hubo un intento de envío, o sea cuando la cuenta existe. El
mensaje de error se vuelve la respuesta distinguible que el diseño evita.

**Decidido: se mantiene la respuesta única y el fallo va al log.** El usuario
espera un correo que no llega y reintenta; la garantía anti-enumeración es una
propiedad de seguridad deliberada y un fallo de transporte es transitorio —un
429 se resuelve en minutos—. Se pierde comodidad en un caso poco frecuente para
no perder una garantía en todos.

Dos matices:

- El log tiene que ser ruidoso (nivel `ERROR`) y sin la dirección en claro, o el
  operador no se entera de que nadie puede recuperar su contraseña.
- `auth_limiter.record_attempt` se llama **antes** del envío, así que reintentar
  consume intentos. Con 8 por cada 5 minutos hay margen suficiente; no propongo
  tocarlo, porque devolver el intento en caso de fallo le daría a quien pueda
  provocar fallos una forma de reiniciar su propio contador.

Con el dominio gestionado esto pesa más de lo que pesaría con cuota holgada:
una tanda de recibos puede dejar sin liga de recuperación a quien la pida esa
hora, y el usuario no verá ninguna señal de por qué. **Mientras dure la fase B,
el log es el único aviso que hay**, así que revisarlo deja de ser opcional. Es
otro argumento para no quedarse en el dominio gestionado más de lo necesario.

### 4.3 Reenvío de la liga de confirmación — `routes/settings.py:150`

**Error honesto.** Aquí no hay nada que filtrar: el usuario está autenticado y
es su propia dirección. `_back("No pudimos enviar el correo. Inténtalo en unos
minutos.")` y listo.

### 4.4 Envío del recibo al cliente — `routes/receipts.py:142`

**Error visible, y que diga qué hacer en su lugar.** El emisor tiene al cliente
delante; necesita saber ya que el correo no salió. La ruta ya tiene la
maquinaria: `_detail_context` con `error`. Se añade un segundo `except`:

- Estado **502**, no 400. El 400 de la rama `DomainError` dice «corrige el dato»
  y aquí no hay dato que corregir.
- El mensaje señala la salida que sí funciona: compartir por WhatsApp o copiar
  la liga, que no dependen del proveedor de correo.

Dos detalles que ya están resueltos y conviene no romper:

- **Sin `db.rollback()` en la rama de error.** El commit solo ocurre tras el
  éxito y `get_db_session` deshace lo demás. Un rollback explícito aquí es lo
  que hizo fallar la ruta de cierre por incobrable en el hito 5: con el
  `join_transaction_mode="create_savepoint"` de las pruebas, tumba la
  transacción entera del test y con ella los datos de los *fixtures*.
- **El PDF ya subido queda huérfano** si el envío falla después de generarlo.
  Es correcto y esperado: `infra/blob.py` lo declara explícitamente, igual que
  los huecos de folio de RN-23.

---

## 5. Configuración

```python
# config.py
mail_backend: Literal["console", "memory", "azure"] = "console"
mail_from: str = f"{APP_NAME} <no-responder@recibo.mx>"

acs_connection_string: str = ""
acs_sender_address: str = ""
```

Con un validador `model_validator(mode="after")`: si `mail_backend == "azure"` y
falta cualquiera de los dos, **la aplicación no arranca**. Es coherente con lo
que ya hace hoy un `MAIL_BACKEND` inválido, y está documentado en
`docs/despliegue-azure.md` como causa conocida de que el contenedor «arranque y
muera al instante». Un despliegue que arranca creyendo que manda correo y no lo
manda es mucho peor que uno que no arranca.

`get_mailer()` pasa a tres ramas, con la importación del adaptador dentro de la
suya para que quien corra en local sin nada de Azure no cargue el SDK.

Dependencia nueva en `pyproject.toml`: `azure-communication-email`. El
transporte asíncrono ya está (`aiohttp`, que entró por `azure.storage.blob.aio`).
`azure.*` ya figura en las excepciones de `ignore_missing_imports` de mypy.

---

## 6. Pruebas

Ninguna toca la red. `tests/conftest.py` fija `MAIL_BACKEND=memory` y no se
cambia, así que las 549 actuales siguen igual.

**Unitarias** — `tests/unit/test_mail_azure.py`

1. `to_payload` coloca asunto, cuerpo, destinatario y remitente donde el SDK los
   espera.
2. Un adjunto viaja en base64 y **vuelve a los bytes originales** al decodificar
   — es lo que rompería el PDF sin que nada avise.
3. Sin adjuntos no aparece la clave `attachments`.
4. Un adjunto por encima del límite lanza `MailDeliveryError` **antes** de tocar
   la red.
5. Un `AzureError` del cliente se traduce a `MailDeliveryError`, y no se filtra
   la excepción del SDK hacia arriba.

**De integración** — con un `mailer` que siempre falla, como fixture

6. Registro: 303, la cuenta existe y la sesión se emite pese al fallo.
7. `POST /auth/forgot-password`: 200 y **exactamente el mismo** `_RESET_SENT`
   que en el caso bueno. La prueba compara los dos cuerpos, que es la única
   forma de que la garantía no se erosione en un refactor futuro.
8. `POST /recibos/{id}/enviar`: 502, la pantalla nombra WhatsApp y la liga, y el
   recibo no queda marcado como enviado.
9. `POST /ajustes/verificar-correo`: vuelve con el aviso de fallo.

**De configuración**

10. `MAIL_BACKEND=azure` sin cadena de conexión: `Settings()` revienta.

---

## 7. Orden de trabajo

**Fase A — código (no necesita cuenta de Azure, ni gasta un peso)**

`MailDeliveryError`, `mail_azure.py`, config y validador, las cuatro rutas, las
diez pruebas, `azure-communication-email` en `pyproject.toml`. Todo verificable
con `MAIL_BACKEND=memory`. Sale verde, se puede comprometer y deja el árbol en
un estado mejor que el actual aunque la fase B se retrase semanas.

**Fase B — Azure con dominio gestionado (una tarde)**

Los cuatro pasos de §2 con dominio gestionado, `MAIL_BACKEND=azure` en local
contra el servicio real, y una prueba de humo enviándose a uno mismo los tres
tipos de correo: recuperación, confirmación y recibo con PDF adjunto. Aquí se
resuelven las dos verificaciones marcadas en §3 —el *poller* abandonado y el
nombre en `senderAddress`— y se comprueba lo único que ninguna prueba puede
comprobar: que el PDF llega abrible y que el correo no cae en spam.

**Fase C — dominio propio**

Alta del dominio, registros TXT/SPF/DKIM, verificación, enlace al recurso,
cambio de `ACS_SENDER_ADDRESS`. Es la fase lenta y la que decide si esto se
puede enseñar a un usuario real. Si además el dominio pasa a servir la
aplicación, `PUBLIC_BASE_URL` cambia con él y hay que releer RN-30: las ligas
públicas ya emitidas no caducan mientras exista la cuenta, así que el dominio
viejo tiene que seguir resolviendo o los recibos ya entregados se rompen.

**Documentación, al cerrar C**

`docs/despliegue-azure.md` §8.1 («El correo no sale de verdad») deja de ser
cierto y se sustituye por el alta real; la tabla de variables gana dos filas;
`docs/pendientes.md` §1.2 se cierra; el `README` corrige la nota de
`MAIL_BACKEND=console`.

---

## 8. Lo que este plan deja fuera a propósito

- **Reintentos y cola.** Un fallo se reporta, no se reencola. Una cola de
  salida es infraestructura nueva —tabla, proceso, reintentos con retroceso— y
  no hay ninguna AC que la pida. Si la cuota resulta ser el cuello de botella
  real, se replantea entonces, con datos.
- **Correo en HTML.** Los cuerpos actuales son texto plano y están bien
  escritos. Un HTML mejoraría la pinta del correo al cliente —y algo ayudaría
  contra la lectura de phishing—, pero es trabajo de plantillas que §2.2 no pide.
  Anotado como candidato, no incluido.
- **Métricas de entrega y rebotes.** Azure las trae en *Email Insights*. Mirar
  ahí es gratis; construir un panel propio no.
- **Recordatorios automáticos.** RN-32 fija que el envío es siempre manual y
  §2.2 los declara no-objetivo permanente. No entran nunca, no solo ahora.

---

## 9. Decisiones tomadas

| Decisión | Resuelta | Dónde |
|---|---|---|
| Dominio remitente | Gestionado por Azure, con su tope de 10/hora. Dominio propio aplazado. | §1 |
| Fallo en recuperación de contraseña | Respuesta única; el fallo solo al log. | §4.2 |
| Esperar la entrega | No: solo el POST inicial de `begin_send`. | §3 |

Pendiente de resolver **con el servicio delante**, en la fase B, no antes:

- Si abandonar el *poller* tras el 202 cancela o no el envío ya aceptado.
- Si `senderAddress` acepta la forma `Nombre <dirección>`.

Y un bloqueante que no es de este plan pero lo toca: **`infra/envs.txt` fuera de
`.gitignore`** (§2). La cadena de conexión de ACS sería el tercer secreto ahí.
