# Pendientes y deuda técnica

**Estado:** hitos 0–6 completos, 549 pruebas en verde.
**Fecha de este corte:** 12 de agosto de 2026.
**Para qué sirve:** retomar el proyecto sin tener que reconstruir de memoria qué
quedó a medias y por qué. Cada punto dice **dónde vive**, **por qué importa** y
**qué significa terminarlo**.

Lo que está aquí se verificó contra el código el día del corte, no se recuerda.

---

## 0. ~~Un archivo con secretos fuera de `.gitignore`~~ — RESUELTO

**Cerrado el 13 de agosto de 2026.** `infra/envs.txt` está en `.gitignore`.
Verificado con `git check-ignore -v` (línea 18) y con
`git log --all -- infra/envs.txt`, que sale vacío: **nunca llegó al historial**,
así que no hay nada que rotar.

Sigue siendo cierto lo que decía el aviso original: si algún día entrara al
historial, borrarlo del árbol no bastaría — habría que rotar el secreto de
sesión, la contraseña de la base y la llave de la cuenta de almacenamiento. A la
lista se le añade ahora la **cadena de conexión de Communication Services**.

> Nota: `.env` sí está ignorado desde el hito 0. El agujero es solo `infra/`,
> que nació después.

---

## 1. Bloqueantes antes del primer usuario real

Estos dos no son deuda: son requisitos que el PRD pone antes de que alguien
ajeno use el producto.

### 1.1 Aviso de privacidad y términos — §10

**No existe ninguna ruta.** §10 lo declara obligatorio *antes del primer usuario
real* y fija el contenido mínimo: el **emisor** es el responsable del tratamiento
de los datos de sus clientes, y la plataforma el **encargado**.

Dos cosas lo complican más de lo que parece:

- **Con Neon como base de datos** (ver `docs/despliegue-azure.md`, opción B) se
  incorpora un **subencargado**. La LFPDPPP admite la transferencia; lo que no
  admite es omitirla. El aviso tiene que nombrarlo y decir en qué país viven los
  datos.
- **Depende de §14.2**, que sigue abierta (§2 de este documento).

**Terminado significa:** dos rutas públicas (`/privacidad`, `/terminos`), enlace
desde el pie de las pantallas de registro y login, y el texto revisado por
alguien con criterio legal. El PRD advierte explícitamente que su orientación
sobre §14.2 *no es asesoría legal*.

### 1.2 Adaptador de correo — código listo, infraestructura sin dar de alta

**Actualizado el 13 de agosto de 2026.** El código está terminado y probado:

```python
mail_backend: Literal["console", "memory", "azure"] = "console"
```

`infra/mail_azure.py` implementa el puerto contra Communication Services, y las
cuatro rutas que mandan correo tratan el fallo de envío, que antes no podía
ocurrir. 29 pruebas nuevas. Diseño en
[`plan-correo-azure.md`](plan-correo-azure.md).

**Lo que falta es dar de alta los recursos en Azure**, con el paso a paso ya
escrito en [`correo-azure-alta.md`](correo-azure-alta.md). Mientras tanto,
`MAIL_BACKEND=console` sigue siendo el valor por omisión y no sale ningún
correo.

Dos cosas que **solo se pueden resolver con el servicio delante** y quedan
marcadas en el plan:

- Si abandonar el *poller* tras el 202 cancela el envío ya aceptado.
- Si `senderAddress` acepta la forma `Nombre <dirección>`.

**Y una limitación que se asumió a conciencia:** con dominio gestionado por
Azure el tope es de **10 correos por hora en toda la suscripción**, sin
posibilidad de ampliarlo. Recibos, ligas de recuperación y confirmaciones
comparten ese cubo. Es suficiente para probar y **no lo es para un usuario
real**: el dominio propio (§9 de la guía de alta) sigue pendiente y es lo que
sube el tope a 100/hora.

**Terminado significa:** los cinco pasos de alta hechos, la prueba de humo
pasada —incluido abrir el PDF que llega— y el dominio propio verificado.

---

## 2. Decisiones abiertas del PRD

Las tres que quedan. §14.1 (firma de terceros) y §14.4 (frontend) se resolvieron
en los hitos 3 y 0.

### §14.2 — Periodo de gracia tras borrado de cuenta

Sin resolver. El PRD señala el conflicto: se mencionó una gracia de 10 años, y
retener una década no es gracia sino retención, lo que choca con el derecho de
cancelación bajo la LFPDPPP. La orientación del documento es separar **gracia de
reversión** (30–90 días) de **retención posterior**, que solo procede con causa
jurídica declarada y limitada al comprobante, no al perfil completo.

**Bloquea:** la publicación de términos (§1.1). No bloquea desarrollo.
**Necesita:** un abogado, no una decisión de producto.

### §14.5 — Pasarela de pago: Mercado Pago vs Stripe

Sin resolver, y **por decisión explícita del hito 6 no se construyó nada**.
§11.4 lo respalda: construir la pasarela antes de saber si alguien quiere pagar
gasta las horas del presupuesto más escaso. Mientras tanto el plan de pago se
concede con cupón o a mano (§3.3 de este documento).

Los términos del intercambio, tal como los plantea §11.4: Mercado Pago tiene
mejor aceptación en México para recurrentes de bajo monto; Stripe tiene mejor
experiencia de desarrollo. A $80 MXN la comisión deja ~$70 netos.

**Se desbloquea con un dato, no con una discusión:** que alguien pida pagar.

### §14.3 — Calibración del límite gratuito

La decisión está tomada (bloqueo a los 10/mes) y así está implementada. Lo que
el hito 6 añadió es la **instrumentación para poder revisarla con datos**, que
es lo que §11.2 exige.

El riesgo que el PRD anticipa: el usuario de validación hace 15–20 recibos al
mes, así que topará el muro a mitad de mes y con el efectivo en la mano. Se
mitigó parcialmente con un aviso desde el recibo 9 (`FREE_WARNING_FROM = 8`),
pero el muro sigue ahí.

**Cómo leer el dato cuando haya:**

```sql
-- ¿Cuántas veces topó, quién, y cuánto insistió?
SELECT account_id, period, COUNT(*) AS intentos, MIN(occurred_at) AS primera_vez
FROM plan_block_event
GROUP BY account_id, period
ORDER BY intentos DESC;
```

Muchos intentos en el mismo periodo dicen que el muro le estorbó de verdad. Cero
eventos y cero recibos dicen otra cosa completamente distinta —que el producto
no se usó—, y §17 advierte que **son conclusiones opuestas y hay que
distinguirlas antes de tocar el producto**.

---

## 3. Funcionalidad no construida

### 3.1 Pasarela de pago

Ver §14.5 arriba. Cuando exista, entra en `/planes` y no debería tocar nada más:
`domain/plans.py` ya centraliza qué habilita cada plan, y `entitlements_for()`
lo decide leyendo `plan_valid_until`. Un webhook que extienda esa fecha es todo
lo que hace falta del lado del dominio.

### 3.2 Cupones de porcentaje y monto fijo

Los tres tipos de §11.3 existen en el modelo (`DiscountType`), pero **solo se
canjea `free_months`**. Los otros dos descuentan sobre un precio, y sin pasarela
no hay precio: aceptarlos habría guardado un descuento que nadie puede aplicar,
que es el andamiaje preparatorio que §2.2 prohíbe.

Hoy se rechazan con una explicación (`services/plans.py`, `redeem_coupon`).
Cuando exista la pasarela, ese `raise` se cambia por la lógica de descuento.

### 3.3 No hay superficie de administración

Ni cupones ni eventos de bloqueo tienen pantalla. Es deliberado —§2.2 no la pide
y construirla sería alcance que nadie pidió— pero conviene dejar el SQL escrito:

```sql
-- Dar de alta un cupón de 3 meses gratis, 20 canjes, para los primeros usuarios
INSERT INTO coupon (code, discount_type, discount_value, max_redemptions, active, created_at)
VALUES ('GRACIAS3', 'free_months', 3, 20, true, now());

-- Activar el plan de pago a mano (§11.4 lo propone para la validación)
UPDATE account SET plan = 'paid', plan_valid_until = '2027-01-01' WHERE id = 1;
```

### 3.4 Borrado de cuenta

**No existe ninguna ruta.** RN-30 dice que la liga pública no caduca *mientras la
cuenta exista* y que solo el borrado dispara eliminación con periodo de gracia;
§10 lo lista entre los requisitos de privacidad. Nada de eso está construido.

Depende de §14.2 para saber cuánto dura la gracia, así que hoy está
correctamente bloqueado. Pero es un derecho del usuario bajo la LFPDPPP y no
puede quedarse fuera indefinidamente.

**Terminado significa:** una ruta que marque la cuenta, un periodo de gracia
durante el cual se puede revertir, y un proceso que al vencerlo elimine datos
personales conservando lo que la causa jurídica declarada permita retener.
Ojo con RN-31: las ligas ya entregadas a terceros son de esos terceros.

### 3.5 La señal de éxito de §17 no tiene dónde mirarse

§17 fija que el producto funciona si el emisor emite **en sitio** y no en
diferido por la noche, y pide *instrumentar la diferencia entre `paid_at` y
`created_at`*. Los dos campos se guardan por separado y hay una prueba que
impide que uno se copie del otro (`test_charging.py`), pero **nadie los
compara**: no hay reporte ni consulta.

```sql
-- Minutos entre el cobro y su captura. Mediana por mes.
SELECT date_trunc('month', p.paid_at AT TIME ZONE 'America/Mexico_City') AS mes,
       COUNT(*) AS pagos,
       percentile_cont(0.5) WITHIN GROUP (
         ORDER BY EXTRACT(EPOCH FROM (p.created_at - p.paid_at)) / 60
       ) AS mediana_minutos
FROM payment p
GROUP BY 1 ORDER BY 1;
```

Una mediana de minutos dice que se usa en la puerta. Una de horas dice que se
convirtió en tarea administrativa, y §17 avisa de que si eso pasa **ninguna
funcionalidad adicional lo arregla**.

---

## 4. Deuda técnica

### 4.1 `alembic revision --autogenerate` no puede escribir

Verificado el día del corte:

```
PermissionError: [Errno 13] Permission denied:
  '/app/migrations/versions/20260812_2346_prueba_de_permisos.py'
```

El contenedor corre como uid 1000 y no puede escribir en el volumen montado de
`migrations/`. Todas las migraciones de los hitos 0–4 se escribieron **a mano**.
Funciona, pero es una trampa: quien agregue un modelo asumirá que autogenerate
funciona, verá el error y perderá el rato.

`alembic check` y `alembic upgrade` sí funcionan —solo leen y escriben en la
base—, así que la CI detecta migraciones faltantes sin problema.

**Arreglo:** ajustar el uid del contenedor al del host en `compose.yaml`, o
generar la migración fuera del contenedor. Media hora. **Prioridad baja hasta
que haya que tocar el esquema otra vez**, que con los hitos 5 y 6 ya cerrados
podría no pasar pronto.

### 4.2 El limitador de intentos vive en memoria del proceso

Ya documentado en `security/rate_limit.py` y es una limitación consciente: se
pierde al reiniciar y no se comparte entre instancias. Con una sola instancia de
App Service B1 alcanza, y evita pagar un Redis para llevar un contador.

**Umbral:** el día que se escale horizontalmente. Un atacante que reparta
intentos entre réplicas multiplica el límite por el número de réplicas.

### 4.3 La contabilidad agrega en Python, no en SQL

`services/accounting.py` trae entidades y suma en memoria. Es deliberado: un
`GROUP BY date_trunc(... AT TIME ZONE ...)` sería una segunda implementación del
corte contable de §3.4 en otro lenguaje, sin las pruebas de `AC-07`.

**Umbral:** con 15–20 pagos al mes, la ventana de seis meses del panel son ~120
filas. A un orden de magnitud más por cuenta —200+ pagos mensuales— conviene
mover las sumas a SQL, y entonces hay que replicar el corte local con cuidado y
probarlo contra los mismos casos de `AC-07`.

### 4.4 La comprobación del límite mensual no es atómica

`assert_can_issue` lee el contador y decide antes de escribir nada. Dos
emisiones simultáneas podrían pasar las dos y dejar 11 recibos en el mes.

En la práctica está acotado: el doble envío real viene del doble toque, y eso lo
resuelve la idempotencia de §6; y §4.1 fija **un usuario por cuenta**, así que no
hay otro origen de concurrencia.

**Umbral:** el día que llegue el multiusuario (§2.2 lo declara no-objetivo hoy).
El arreglo natural ya está a mano: `record_issuance` devuelve el contador
después de incrementar de forma atómica, así que bastaría comprobar ahí y dejar
que la transacción se deshaga.

### 4.5 `docs/` e `infra/` siguen sin versionar

`git status` los muestra como no rastreados desde el despliegue. `docs/` debería
entrar al repositorio; `infra/envs.txt`, nunca (§0).

---

## 5. Decisiones que van más allá del PRD

Todas están tomadas y funcionando. Se anotan porque **el PRD no las respalda del
todo** y conviene poder revisarlas con datos en lugar de redescubrirlas leyendo
código.

| Decisión | Qué dice el PRD | Qué se hizo | Por qué revisarla |
|---|---|---|---|
| El CSV es función de pago | §9.2 dice que en gratuito *"la consulta se limita"*, no que desaparezca | En gratuito no se descarga | Va un paso más allá. Si el reporte resulta ser el gancho que engancha, cobrarlo puede costar más de lo que rinde |
| Aviso desde el recibo 9 | El PRD no lo menciona | `FREE_WARNING_FROM = 8` | Número inventado para mitigar §14.3. Se calibra con los datos de `plan_block_event` |
| Ventana de analítica: 1 mes | §11.1 dice "ventana limitada" sin fijar tamaño | 1 mes, decisión del responsable | Es el recorte más duro posible. Si ahuyenta sin convertir, se afloja |
| Sobrepago rechazado | RN-12 exige motivo para todo aumento | El cobro se rechaza y se apunta al monto extra | Decisión de producto tomada en el hito 2. Añade fricción justo en el momento de §1.4 |
| Trabajo sin total acordado queda `settled` con su primer pago | Regla derivada, no explícita | `resolve_service_status` | Sin ella quedaría `open` para siempre con saldo indefinido, contaminando "por cobrar" |
| "Quién me debe" no se acota nunca | RN-35 limita "funciones derivadas" y podría incluirla | Libre en los dos planes | §17 la usa como señal de éxito: cobrarla apagaría la medición |

---

## 6. Resumen para retomar

Si hay poco tiempo, este es el orden:

1. **`infra/envs.txt` al `.gitignore`.** Cinco minutos, y evita un problema que
   no se deshace.
2. **Adaptador de correo real.** Desbloquea tres flujos ya construidos, y la
   verificación de dominio tarda días de calendario: conviene empezarla antes de
   necesitarla.
3. **Aviso de privacidad y términos.** Requisito de §10 antes del primer usuario
   real, y depende de una consulta legal que también tarda.
4. **Versionar `docs/`.**
5. Lo demás espera a tener datos: la pasarela espera a que alguien pida pagar, y
   la calibración del muro espera a `plan_block_event`.
