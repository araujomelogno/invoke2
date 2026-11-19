# INVOKE 2 — Encargo de cierre de la Fase 1

Nov 19, 2026 · @Equipos

La Fase 1 está construida y verde: 121 tests de Python, 67 de JavaScript y el chequeo de tipos pasan. Quedan cuatro requisitos de la especificación sin implementar y cinco defectos encontrados leyendo el código, uno de ellos con efecto directo sobre el costo de infraestructura del piloto.

## Alcance de la revisión

Se revisó el repositorio `github.com/araujomelogno/invoke2` en el commit `45a5854`, contra los requisitos P0 de la *Especificación Fase 1* y los cuatro documentos de Fase 0. Se leyeron las ocho migraciones, el núcleo Python, los tres servicios de Cloud Run, el frontend sin build y la configuración de Asterisk y coturn.

**Lo que se verificó ejecutando, no leyendo:**

| Comprobación | Resultado |
| --- | --- |
| `pytest -q` contra un Postgres 16 real | 121 tests, todos verdes |
| `node --test tests/js/*.test.mjs` | 67 tests, todos verdes |
| `tsc -p .` | sin errores |

Los tests de base no son de fachada: `test_asignacion_concurrente_skip_locked` abre dos conexiones reales que compiten por el mismo caso y comprueba que una lo obtiene y la otra sigue sin esperar.

**Lo que quedó confirmado como correcto** y no hace falta volver a mirar: la cascada del asignador con `SELECT ... FOR UPDATE SKIP LOCKED`; reintentos con espera y tope por disposición, tope global por caso, tope por franja y rotación de franja; sustitutos por ranura; lista negra transversal a la casa; el flujo completo de casos fuera de marco, con diálogo de tres opciones, opción deshabilitada con su motivo a la vista, tope por guía y herencia editable del estrato; cuotas por pregunta, área y propiedad sin contadores mutables; el log inmutable de 32 tipos de evento con sus invariantes como triggers; la reclasificación como evento nuevo con motivo obligatorio impuesto por la base; la exportación en `.sav`, CSV y Excel con el origen del caso en cada registro; y la vista de compatibilidad CDR con `SIN_EQUIVALENTE`.

El repositorio documenta además diez pendientes propios en `docs/decisiones.html`. Ninguno de los nueve puntos de este encargo figura entre ellos.

## Huecos de alcance

Requisitos que la especificación pide y el código no tiene.

### H1 — Mezcla de frescos y reintentos

Es la omisión completa: buscar «fresco» o «mezcla» en el repositorio no devuelve nada. `invoke.pool_elegible` trata por igual a los casos nunca llamados y a los ya intentados cuya espera venció, y los ordena por prioridad de guía y orden aleatorio. Es exactamente el comportamiento del legacy que la especificación decidió cambiar.

Qué hay que construir:

- Columna `proporcion_frescos numeric(5,4) NOT NULL DEFAULT 0.5` en `invoke.estudio`, editable desde la administración del estudio como el resto de los parámetros.
- `invoke.pool_elegible` devuelve además si el caso es fresco. Fresco = sin ningún evento `disposicion_telefono` que cuente intento; la condición se deriva del log o de `caso_estado.intentos = 0`, no de una columna nueva.
- `invoke.asignar_caso` y `invoke.reservar_casos_pacer` sortean la población **en cada pedido**, con probabilidad `proporcion_frescos`, y recién después eligen dentro de ella por prioridad y orden. No es un cupo acumulado.
- Si la población sorteada está vacía, se usa la otra. Ningún encuestador espera porque su mitad se agotó.
- El tablero de supervisión muestra el reparto **efectivo** del día, que puede diferir del configurado.

Criterios de aceptación:

- [ ] Con 1.000 asignaciones sobre un pool que tiene frescos y reintentos de sobra, la proporción observada de frescos cae dentro de ±3 puntos de la configurada (test con semilla fija).
- [ ] Con `proporcion_frescos = 1` y el pool de frescos agotado, el asignador sigue devolviendo reintentos y nunca `NULL`.
- [ ] La consola muestra el reparto efectivo del día junto al configurado.

### H2 — Se graban todas las llamadas

El código implementa muestreo: `estudio.muestreo_grabacion` por defecto 0,10, y tanto `estacion.llamar` como `invoke.reservar_casos_pacer` deciden con `random()`. La decisión vigente es grabar todas las llamadas conectadas.

- Se elimina el muestreo: el campo `grabar` del evento `llamada_originada` es siempre verdadero para las llamadas de encuesta, y falso para las administrativas.
- La columna `muestreo_grabacion` se retira del formulario de administración. Si se conserva en la tabla por compatibilidad, queda sin uso y así documentada.
- El control de casos fuera de marco deja de hablar de muestreo: esas completas quedan marcadas para revisión prioritaria de su grabación, que es otra cosa.

Esto convierte en bloqueante el pendiente P-05 del propio repositorio: hoy Asterisk graba en el disco local de su VM y falta el trabajo que sube el archivo al bucket. Con una de cada diez llamadas eso aguanta; con todas, el disco se llena en días. **El cierre de H2 incluye la subida al bucket, no solo quitar el `random()`.**

### H3 — Orden aleatorio reproducible e indexado

`invoke.caso.orden_aleatorio` es `DEFAULT random()` por fila: no hay semilla guardada en la guía, así que el orden en que se trabajó la muestra no se puede reconstruir ni auditar. Tampoco hay ningún índice que lo incluya, de modo que asignar un caso ordena en vez de leer la primera fila disponible del índice.

- Columna `semilla bigint NOT NULL` en `invoke.guia`, fijada al cargar la guía y registrada en el evento `muestra_cargada`.
- `orden_aleatorio` se calcula de forma determinista a partir de la semilla de la guía y la posición de la fila en el archivo, no con `random()`.
- Índice que cubra el escalón: `(estudio_id, guia_id, orden_aleatorio)` en `invoke.caso`, revisado contra el plan real de `pool_elegible` con `EXPLAIN`.

Criterio de aceptación: cargar dos veces la misma guía con la misma semilla produce el mismo orden; con semillas distintas, órdenes distintos.

### H4 — Códec elegible por encuestador

La especificación quedó en G.711 (PCMA) como preferido y Opus habilitable **por encuestador**, no de forma global, para las conexiones domésticas que lo justifiquen. Hoy no existe esa decisión por encuestador: el códec del puesto es una columna con un único valor por defecto para todos (ver D1, que además lo tiene mal).

- Campo por usuario o por participante que indique si ese puesto habilita Opus.
- `credenciales.rotar_sip` escribe `allow` en `asterisk.ps_endpoints` según ese campo: `alaw,ulaw` por defecto, `opus,alaw,ulaw` para quien lo tenga habilitado.
- La consola de supervisión muestra con qué códec está hablando cada puesto, junto a la calidad de red. `invoke.calidad_red` ya tiene la columna `codec`; falta mostrarla.

Por qué importa verlo: si diez encuestadores terminan en Opus, eso es transcodificación permanente en Asterisk, no un caso excepcional. Conviene enterarse mirando el tablero y no por el consumo de CPU de la VM.

## Defectos de código

Estos no son huecos de alcance: el código existe y hace algo distinto de lo que dice hacer.

### D1 — Opus está activado en todos los puestos

`asterisk.ps_endpoints.allow` tiene `DEFAULT 'alaw,ulaw,opus'` en la migración `0005`, y `credenciales.rotar_sip` nunca escribe esa columna, así que **todos** los puestos nacen aceptando Opus. Como Chrome ofrece Opus primero, en la práctica cada llamada se negocia en Opus del lado del navegador mientras el trunk habla G.711: Asterisk transcodifica en los dos sentidos, en todas las llamadas.

El propio `pjsip.conf` dice lo contrario en su comentario: «G.711 extremo a extremo siempre que sea viable, para que Asterisk solo puentee. Si la prueba de campo pasa a Opus, se agrega opus en los puestos». Ya está agregado.

**Por qué importa ahora:** el perfil de despliegue del piloto pone Asterisk y coturn juntos en una VM de 2 vCPU, dimensionada para puentear RTP, no para transcodificar. Con este defecto, la primera prueba de carga va a medir el costo equivocado y la conclusión sobre el códec va a estar viciada.

Arreglo: `DEFAULT 'alaw,ulaw'`, y que `rotar_sip` escriba `allow` según el campo de H4. Las dos cosas son el mismo cambio.

### D2 — El traductor de ARI pierde el estado de las llamadas al reiniciar

`services/ari/traductor.py` guarda el estado de cada llamada en memoria (`self.llamadas`: atendida, conectada, amd, abandonada). Al arrancar, `reconstruir_puestos` recupera de la base el mapa de canales de puesto, pero **no** reconstruye `self.llamadas`.

Escenario de falla: el servicio se reinicia (despliegue, reciclado de instancia, caída) mientras hay llamadas conectadas. Cuando esas llamadas terminan, `_destruido` llama a `disposicion_por_fin(atendida=False, conectada=False, amd=None)` porque la memoria está vacía, y escribe automáticamente una disposición de no-contacto sobre una llamada que sí fue atendida y conversada. Queda en el log inmutable, cuenta como intento y entra a los reportes.

Esto contradice dos cosas que el proyecto ya decidió: el requisito de M1 — «al reiniciar la aplicación, ninguna llamada activa queda en estado inconsistente» — y el ADR-003, porque el estado ya está en Postgres y el código elige no mirarlo.

Arreglo: `_destruido` deriva `atendida` y `conectada` de la vista `invoke.llamada` (que tiene `atendida_en` y `conectada_en`) en lugar de `self.llamadas`, y el AMD del evento `amd_resultado`. La memoria pasa a ser una caché, no la fuente.

### D3 — `llamada_timbrando` no se emite nunca

El guard es `elif tipo == "ChannelStateChange" and canal in self.llamadas`, pero `self.llamadas` se puebla en `_cliente`, que corre en `StasisStart`, es decir **después** de que el respondente atendió. Cuando llega el estado `Ringing`, el canal todavía no está en el diccionario, así que el evento se descarta.

El tipo de evento existe en el esquema, la sala lo reenvía al navegador y el puesto lo espera: el encuestador nunca ve el estado «timbrando».

Arreglo: registrar la llamada en `self.llamadas` al originarla (cuando se ejecuta el comando `originar`, que ya conoce el `llamada_id`), no al entrar a Stasis.

### D4 — Tres rutas del puesto no verifican que el caso sea del encuestador

La mayoría de las rutas comprueban `asignado_a == participante` antes de actuar. Tres no lo hacen:

| Ruta | Qué permite hoy |
| --- | --- |
| `GET /api/estacion/casos/{id}/respuestas` | Leer las respuestas de cualquier caso de cualquier estudio |
| `POST /api/estacion/casos/{id}/iniciar` | Registrar `encuesta_iniciada` sobre un caso ajeno |
| `POST /api/estacion/finalizar` | Clasificar un caso ajeno: el `caso_id` viene del cuerpo y no se contrasta |

De las tres, la tercera es la que importa: escribe disposiciones en el log inmutable. Las invariantes de la base impiden que la disposición sea incoherente — exige que la llamada tenga disposición 4, por ejemplo — pero no que el caso sea de quien la envía.

Es un sistema interno y todos los usuarios son del equipo, así que el riesgo real es un error de la interfaz o un reintento con el caso cambiado, no un ataque. Igual conviene cerrarlo: el patrón ya existe en el resto del archivo y son tres líneas.

### D5 — El diccionario de disposiciones de teléfono no se lee para clasificar

`invoke.disposicion_telefono` tiene una columna `simbolo` con los valores `UD` y `AB`, poblada en la migración del diccionario y **que no lee nadie**. En su lugar, `invoke.clasificar_casos` cablea los códigos 4, 5 y 30 como literales, y el reporte de productividad cablea 3, 7, 4 y 5.

Hoy los números salen bien, porque los literales coinciden con el diccionario v1. El problema es el día que haya un v2: un estudio registra qué versión usó y los reportes dicen calcularse con ella, pero el SQL va a seguir aplicando la semántica de v1 en silencio. Es justo lo que la regla 12 de `CLAUDE.md` promete que no pasa.

Arreglo: `clasificar_casos` lee `dt.simbolo` y `dt.estado` del diccionario de la versión del estudio, igual que ya hace con `de.simbolo` para las disposiciones de encuesta. Los literales que queden deben tener su test.

## Observaciones menores

Ninguna bloquea el piloto. Se cierran si hay tiempo, o cuando se toque el archivo por otra razón.

| # | Qué | Dónde |
| --- | --- | --- |
| M1 | Una expresión de cuestionario muy anidada (dentro del límite de 500 nodos) puede superar el límite de recursión de Python y devolver un 500 en vez de un 422 con mensaje. Límite de profundidad, no solo de nodos | `invoke/dominio/expresiones.py` |
| M2 | El punto de entrada no atrapa excepciones inesperadas: lo que no sea `ErrorHttp` ni un error conocido de psycopg sube sin convertirse en respuesta | `functions/main.py` |
| M3 | `concurrency=40` por instancia contra un pool de 5 conexiones sin tiempo de espera explícito. Bajo carga, las peticiones se encolan en el pool sin señal clara | `functions/main.py`, `invoke/db.py` |
| M4 | Ante cualquier evento, la consola recalcula la foto de **todos** los estudios con supervisor conectado, no solo el que cambió. Una consulta por segundo por supervisor, de más | `services/sala/main.py` |
| M5 | `.DS_Store` versionado | raíz del repositorio |

## Orden de trabajo

El orden no es por tamaño sino por lo que bloquea a lo siguiente.

1. **D1 y H4 juntos.** Son el mismo cambio y hay que hacerlos antes de la prueba de audio y de cualquier prueba de carga: con Opus activado en todos los puestos, lo que se mida no es lo que se va a operar. Es el único punto de este encargo que puede invalidar una medición.
2. **D2 y D3.** Tocan el mismo archivo. D2 corrompe datos y conviene cerrarlo antes de que haya llamadas reales en el log.
3. **H2 completo**, con la subida al bucket. Antes de la primera jornada del piloto, no después: lo que no se grabó no se recupera.
4. **H3.** Antes de cargar la primera guía real, porque cambia cómo nacen los casos.
5. **H1.** Antes de la primera jornada: cambia qué casos se trabajan y, por lo tanto, los números que el piloto va a comparar contra la línea base.
6. **D4 y D5.** No bloquean el piloto. D5 bloquea la versión 2 del diccionario.
7. **Las menores**, si hay tiempo.

### Cómo se verifica

Cada cambio entra con su test, en el mismo commit, siguiendo lo que ya hace el repositorio:

- H1, H3 y D5 son invariantes de la base: test en `tests/test_base.py` contra Postgres real, no simulacro.
- D2 y D3 son del traductor: `tests/test_ari.py` ya tiene el doble de puerto, alcanza con un caso de reinicio — estado en memoria vacío, llamada conectada en la base — y uno de timbre.
- D4 son tres tests de 403 o 409 en `tests/test_api.py`.
- D1 y H4 se verifican en el PBX de pruebas, no con un test: una llamada de un puesto sin Opus y otra de un puesto con Opus, mirando qué códec negoció cada una y si Asterisk transcodificó.

La suite completa — `pytest -q`, `node --test tests/js/*.test.mjs` y `tsc -p .` — tiene que seguir verde. Hoy lo está, así que cualquier rojo es del cambio.

Al terminar, `docs/decisiones.html` se actualiza: los puntos cerrados dejan de ser decisiones provisorias y H2 obliga a reescribir la decisión de implementación que hoy dice «grabación por muestreo».

## Preguntas abiertas

Ninguna la puede responder quien implementa.

| # | Pregunta | Quién | Bloquea |
| --- | --- | --- | --- |
| 1 | Plazo de retención de las grabaciones. Con todas las llamadas grabadas son del orden de 25 GB por mes de operativo a sala llena, acumulativos: a US$0,02 por GB-mes el primer año es trivial, a tres años no | Equipos | El cierre de H2 |
| 2 | Qué encuestadores arrancan con Opus habilitado, si alguno. Puede ser ninguno hasta que la prueba de audio diga lo contrario | Campo | Nada: el valor por defecto es sin Opus |
| 3 | Cómo se declara al cliente un «No volver a llamar». El diccionario lo clasifica como elegibilidad desconocida y no como rechazo; con `e = 1` la tasa de respuesta no cambia, pero la de rechazo sí. Está implementado según el documento de fórmulas, así que esto es una confirmación, no un cambio | Investigación | Nada |

Las dos primeras ya estaban planteadas en la especificación de Fase 1 y siguen sin respuesta. La tercera aparece al leer el código y conviene confirmarla antes del primer informe con tasas, no después.
