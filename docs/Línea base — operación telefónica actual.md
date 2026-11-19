# Línea base — operación telefónica actual

Nov 18, 2026 · @Equipos

## Qué es este documento

La foto del "antes": cómo rinde hoy la operación telefónica de Equipos sobre la solución actual, para poder decir después si INVOKE 2 mejoró algo.

Tiene fecha de vencimiento. **Solo se puede medir mientras la solución actual siga operando.** Una vez que se corta, no hay forma de reconstruirlo.

### Origen de los datos

| Qué | Detalle |
| --- | --- |
| Fuente | Export del CDR, tabla de registro de llamadas |
| Estudio | Un operativo de clientes de alto valor para un cliente bancario |
| Período | 29 y 30 de octubre de 2026 |
| Llamadas | 466 registros, 462 con encuestador asignado |
| Casos | 273 números distintos |
| Encuestadores | 3 |
| Modo de discado | Preview en el 100% de las llamadas |

### Alcance y advertencia

**Esto es una primera medición, no la línea base definitiva.** Un estudio, dos días, tres encuestadores y un solo modo de discado no representan la operación completa.

En particular:

- Es un estudio sobre **base de clientes entregada por el cliente**, no una muestra probabilística de población general. Sus tasas no son comparables con las de un estudio de opinión pública.
- Es público de **alto valor**, con un perfil de respuesta propio. La tasa de agendamiento que se ve más abajo lo refleja.
- Dos días de semana, sin fin de semana, y la franja de tarde está sub-representada.

La sección final dice qué hace falta para cerrar una línea base defendible.

### Cómo leer los números

Las tasas de acá **no siguen todavía las definiciones del documento de fórmulas**, porque esas definiciones no están cerradas. Son indicadores operativos directos: contactos sobre intentos, completas sobre contactos.

Cuando el documento de fórmulas pase a versión 1, esta línea base se recalcula con esas definiciones. Mientras tanto sirve para dimensionar, no para declarar a un cliente.

## Contactabilidad

De cada 100 llamadas, 41 terminan con alguien del otro lado.

| Resultado de la llamada | Llamadas | % de intentos |
| --- | --- | --- |
| No contesta | 213 | 46,1% |
| Contesta | 189 | 40,9% |
| Contestador telefónico | 56 | 12,1% |
| Sin clasificar | 3 | 0,6% |
| Desconectado | 1 | 0,2% |
| **Total** | **462** | **100%** |

**Tasa de contacto: 40,9%** sobre intentos.

Llama la atención lo que no aparece: ni un solo "ocupado" clasificado como tal en el campo de disposición, aunque el registro técnico de Asterisk reporta 10 llamadas con señal de ocupado. Es un primer indicio concreto de desalineación entre lo que el sistema detecta y lo que queda registrado como disposición.

## Qué pasa cuando contestan

De los 189 contactos:

| Resultado de la encuesta | Casos | % de contactos |
| --- | --- | --- |
| Agendada | 111 | 58,7% |
| Completa | 50 | 26,5% |
| Rechazada | 17 | 9,0% |
| Interrumpida | 11 | 5,8% |
| **Total** | **189** | **100%** |

### El dato más importante de esta página

**Casi seis de cada diez contactos terminan en agenda, no en entrevista.** Solo uno de cada cuatro contactos produce una completa en el momento.

Eso cambia dónde está el costo real del operativo. No se gasta principalmente en discar hasta encontrar a alguien: se gasta en el **ciclo de reagendamiento**, volver a llamar a la hora pactada, y volver a intentar si no estaba.

Tiene dos consecuencias para el proyecto:

1. **El discado predictivo rinde menos de lo que promete en este tipo de operativo.** El predictivo optimiza el tiempo de búsqueda de contacto, que acá no es el cuello. Para un público que agenda, lo que rinde es un buen manejo de la agenda.
2. **El motor de reagendamiento merece tanta atención como el discador.** Respetar la ventana horaria pactada, mantener al mismo encuestador cuando corresponde, y no perder la cita son palancas de productividad directas.

Esto hay que confirmarlo con otros estudios antes de darlo por general: un operativo de alto valor bancario probablemente agende mucho más que uno de opinión pública.

### Tasa de rechazo

**9,0% de los contactos rechaza.** Es una cifra baja, coherente con un público de clientes con relación previa con el patrocinador del estudio.

## Productividad por encuestador

| Encuestador | Intentos | Contactos | Completas | Agendadas | Tasa de contacto | Horas | Completas/hora | Intentos/hora |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| fnegrin | 189 | 73 | 24 | 39 | 38,6% | 5,81 | **4,13** | 32,5 |
| vgancio | 123 | 55 | 15 | 33 | 44,7% | 4,75 | 3,16 | 25,9 |
| eblanco | 150 | 61 | 11 | 39 | 40,7% | 3,89 | 2,83 | 38,6 |
| **Total** | **462** | **189** | **50** | **111** | **40,9%** | **14,45** | **3,46** | **32,0** |

**Horas** es el tiempo entre la primera y la última llamada de cada día. Es una aproximación por arriba: incluye pausas y no distingue sesión real de tiempo transcurrido. Es la mejor medida disponible desde el CDR, y una de las cosas que INVOKE 2 va a medir bien.

### Lo que muestra la dispersión

**El mejor encuestador produce 46% más completas por hora que el último**, con tres personas en el mismo estudio y los mismos días.

Y la explicación no es la obvia. Mirá el caso de `eblanco`: hizo **más intentos por hora que nadie** (38,6 contra 32,5 del primero), tiene una tasa de contacto normal, y sin embargo produce la mitad de completas. Sus contactos se van a agenda, no a entrevista.

La diferencia no está en discar más ni en que le atiendan más. Está en **qué pasa una vez que alguien atendió** — que es exactamente lo que mide la tasa de cooperación y no la de contacto.

Es el argumento empírico de la decisión 7 del documento de fórmulas: si la sala se evaluara por intentos por hora, el ranking quedaría al revés del valor que cada uno aporta.

### Ocupación del tiempo

| Indicador | Valor |
| --- | --- |
| Tiempo total en llamada | 8,1 horas |
| Tiempo total de sesión aproximado | 14,5 horas |
| **Ocupación** | **56,2%** |

Algo más de la mitad del tiempo del encuestador transcurre con una llamada conectada. El resto es discado sin respuesta, clasificación, navegación entre casos y pausas.

Para discado preview es una cifra razonable, y es la medida contra la que habrá que comparar cuando entren power y predictivo. **El objetivo de esos modos es justamente mover este número.**

Con la salvedad de la sección anterior: en un operativo donde seis de cada diez contactos agendan, el techo de mejora por discado es más bajo de lo habitual.

## Franja horaria

| Franja | Intentos | Contactos | Completas | Tasa de contacto | Completas c/100 intentos |
| --- | --- | --- | --- | --- | --- |
| Mañana (hasta 16:30) | 352 | 141 | 38 | 40,1% | 10,8 |
| Tarde (desde 16:30) | 110 | 48 | 12 | 43,6% | 10,9 |

**Las dos franjas rinden prácticamente igual.** La diferencia en tasa de contacto es de 3,5 puntos a favor de la tarde, y las completas por cada 100 intentos son idénticas.

Es un resultado contraintuitivo que vale confirmar con más datos: la franja de tarde tiene menos de un tercio de los intentos, así que la diferencia puede ser ruido. Pero si se sostiene, cuestiona el supuesto de que la tarde rinde más, al menos para este público.

No hay datos de fin de semana en este período.

## Intentos por caso

| Intentos | Casos | % |
| --- | --- | --- |
| 1 | 112 | 41,0% |
| 2 | 133 | 48,7% |
| 3 | 28 | 10,3% |

**Media: 1,69 intentos por caso. Máximo observado: 3.**

El operativo estaba en curso cuando se tomó el corte, así que estos números son de un estudio abierto, no de cierre. La distribución final va a tener una cola más larga.

Aun así el dato sirve para una cosa: **no hay evidencia de un tope de intentos configurado**. Eso confirma lo que el diccionario señala como decisión pendiente, y explica por qué hoy no se puede calcular una tasa de respuesta defendible: sin tope, no hay frontera entre "no se trabajó" y "no se pudo".

## Duración

| Tipo de llamada | Duración media | Mediana |
| --- | --- | --- |
| Entrevista completa | 365 s (6,1 min) | 356 s |
| Llamada sin contacto | 16 s | — |

La entrevista completa dura seis minutos y la mediana está muy cerca de la media, lo que indica un cuestionario de duración estable y poca dispersión entre encuestadores.

Los 16 segundos de las llamadas sin contacto son esencialmente tiempo de timbre: el dialplan corta a los 20 segundos, y la mayoría llega a ese tope.

**Ese parámetro de 20 segundos es una palanca directa de productividad** que INVOKE 2 debería exponer por estudio. Bajarlo a 15 recupera tiempo de sala; subirlo a 25 puede recuperar contactos de gente que tarda en atender. Hoy está fijo en el dialplan y nadie lo mide.

## Lo que no se puede medir hoy

Tres de las métricas de éxito del PRD no tienen valor de línea base, y eso es un hallazgo, no una falta de datos.

| Métrica | Valor base | Por qué |
| --- | --- | --- |
| Tasa de abandono | **No medible** | No existe código de disposición para ella |
| Tiempo ocioso real | **Aproximado** | No hay registro de sesión del encuestador |
| Costo por encuesta completa | **Pendiente** | Falta el costo de licencia |

### Tasa de abandono: el agujero

La solución actual **no tiene un código para "el respondente atendió y no había encuestador"**. No es que el número sea cero: es que no se registra.

En este estudio puntual no importa, porque corrió 100% en preview y en preview no hay abandono por definición: el encuestador ya tenía el caso cuando se discó.

Pero importa mucho para cualquier estudio que haya corrido en PowerDialer. Ahí sí hubo sobre-discado, y por lo tanto hubo abandono, y **no hay forma de saber cuánto**.

Es uno de los argumentos más fuertes del proyecto: la operación está quemando muestra sin poder medir cuánta.

**Acción sugerida:** verificar en el CDR cuántos estudios de los últimos meses corrieron en PowerDialer. Ese volumen dimensiona el problema, aunque no se pueda cuantificar el daño.

### Tiempo ocioso

Las 14,45 horas de esta medición son el lapso entre la primera y la última llamada de cada encuestador por día. Incluye pausas, almuerzo y cualquier interrupción.

No se puede separar "ocioso porque el sistema no le dio trabajo" de "ocioso porque estaba en pausa", que es justamente la distinción que importa para evaluar un discador.

INVOKE 2 lo resuelve con el registro de sesión y los estados del encuestador, que son parte de la consola de supervisión de la Fase 1. **Es una métrica que nace con el sistema nuevo y no tiene comparación histórica posible.**

### Costo por encuesta completa

Con los datos de este estudio: 50 completas en 14,45 horas-encuestador.

Falta el otro término. Con el costo anual de licencia para 20 a 30 puestos se puede cerrar el cálculo y, con él, el caso de negocio del proyecto entero. Es el supuesto abierto número 2 del PRD.

### Calidad de audio

Tampoco hay línea base. Hoy no se mide la calidad de red por puesto, y con sala 100% remota es información relevante.

Se puede levantar una línea base cualitativa antes del corte: una encuesta breve a los encuestadores sobre cuántas veces por turno tienen problemas de audio. Toma una semana y da con qué comparar.

## Cómo cerrar esta línea base

Lo de arriba dimensiona. Para una línea base defendible falta ampliar el corte.

### Qué agregar

| Falta | Por qué |
| --- | --- |
| Al menos un estudio en **PowerDialer** | El único analizado corrió 100% en preview; el sobre-discado es justamente lo que INVOKE 2 cambia |
| Un estudio de **opinión pública** | Para tener un contraste con el perfil de alto valor, que agenda muchísimo |
| Un período de **4 a 8 semanas** | Dos días no capturan variación semanal ni estacional |
| Datos de **fin de semana** | Es una franja del protocolo y no está medida |
| Estudios **cerrados**, no en curso | Los intentos por caso y la tasa de respuesta solo son válidos al cierre |
| Más de **tres encuestadores** | Para que la dispersión de productividad sea representativa |

### El corte sugerido

Todos los estudios cerrados de los últimos tres meses, excluyendo campañas que no son estudios — pruebas, capacitaciones e internas.

Con ese volumen se pueden calcular las mismas métricas de acá abiertas por tipo de marcador, por tipo de público y por franja, que es lo que hace comparables los números de INVOKE 2 cuando llegue el momento.

### Consultas que lo reproducen

Sobre la tabla de registro de llamadas, con el campo de campaña como estudio, la cuenta del agente como encuestador, y el campo de disposición partido en sus dos mitades:

1. **Contactabilidad**: conteo por mitad izquierda del campo de disposición, agrupado por estudio.
2. **Resultado de contactos**: conteo por mitad derecha, filtrando los que contestaron.
3. **Productividad**: por agente y día, conteo de completas y rango entre primera y última llamada.
4. **Franja**: misma agrupación, clasificando por hora de la llamada segun el protocolo de franjas.
5. **Intentos por caso**: conteo de llamadas por número destino, dentro de cada estudio.
6. **Duración**: promedio y mediana del tiempo facturado, separando completas del resto.
7. **Tipo de marcador**: primera parte del identificador de llamada, para separar preview de PowerDialer.

Son las mismas agregaciones que el tablero de productividad ya hace. **Conviene reutilizar su script en vez de escribir consultas nuevas**: así la línea base queda calculada con exactamente la misma lógica con la que después se va a medir INVOKE 2, y la comparación no arrastra diferencias de implementación.

### Antes de que sea tarde

Esta medición solo existe mientras la solución actual opere. **Conviene cerrarla antes de empezar a mover estudios a INVOKE 2**, no después.
