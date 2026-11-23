# Registro de decisiones — INVOKE 2

Nov 20, 2026 · @Equipos · actualizado con el encargo de cierre de la Fase 1 y la validación contra Asterisk

## Cómo leer este registro

Una decisión vale hasta que aparezca evidencia que la contradiga; si cambia, se registra acá con su motivo (PRD).

| Prefijo | Qué es |
| --- | --- |
| **ADR** | Decisiones de arquitectura: las 10 del PRD más ADR-011 a ADR-013, nacidas al implementar |
| **DI** | Decisiones de implementación: detalles que los documentos no fijaban y el código tuvo que resolver. Las marcadas *provisoria* esperan a investigación |
| **P** | Pendientes: lo que sigue abierto, quién lo resuelve y qué bloquea |

Las definiciones de disposiciones y fórmulas no están acá: viven en el `Diccionario de disposiciones` y en `Fórmulas de reporte y mapeo AAPOR`, que la implementación lee literalmente.

### Arquitectura

```mermaid
flowchart LR
  N[Navegador<br/>puesto, sala, admin, editor] -->|HTTPS| F[Firebase<br/>Hosting, Auth, Functions]
  N -->|WebSocket| CR[Cloud Run<br/>sala, ari, pacer]
  N -->|SIP/WSS, SRTP| T[coturn]
  T -->|audio relay| A[Asterisk 22]
  F -->|psycopg 3, VPC| PG[(Cloud SQL<br/>Postgres)]
  CR --> PG
  CR -->|ARI| A
  A -->|realtime PJSIP| PG
  A --> TR[Trunk SIP existente<br/>PSTN]
```

Postgres decide; Cloud Run reacciona. Ningún componente le pregunta a Asterisk ni a Firebase el estado de nada.

## Decisiones de arquitectura del PRD

### ADR-001 — Postgres es la fuente de verdad; Firebase no

**Estado:** implementada.

- **Decisión.** Muestra, cuotas, respuestas y reportes viven en Cloud SQL Postgres. Firebase autentica y, opcionalmente, pinta presencia.
- **Dónde.** `db/migrations/0003`–`0006`. La asignación usa `SELECT … FOR UPDATE SKIP LOCKED` en `invoke.asignar_caso`, más un índice único parcial sobre `caso_estado.asignado_a` (un participante, un caso). Los roles salen de `invoke.usuario`, no de custom claims.
- **Excepción.** El estado del encuestador vive en `invoke.participante_estado` y se espeja a Firestore `presencia/` solo con `INVOKE_ESPEJO_FIREBASE=1`.
- **Verificación.** `test_asignacion_concurrente_skip_locked`: dos conexiones reales compiten por un caso; una lo obtiene y la otra sigue sin esperar.

### ADR-002 — Log de eventos inmutable, sin contadores mutables

**Estado:** implementada.

- **Decisión.** Toda transición es una fila de `invoke.evento` (32 tipos). Ningún reporte lee contadores.
- **Dónde.** `0004_eventos.sql`. Un trigger rechaza UPDATE, DELETE y TRUNCATE aunque lo intente el dueño, y la aplicación solo tiene INSERT y SELECT. Respuestas y calidad de red son tablas de hechos con la misma protección. No existen `surveyCount` ni `completedCount`: el avance de cuotas y el "estudio completo" son vistas sobre completas.
- **Matiz.** El asignador necesita una proyección operativa, que no es un contador de reporte (ADR-012).
- **Verificación.** `test_evento_inmutable`, `test_accesos`.

### ADR-003 — Asterisk moderno con ARI, no AMI

**Estado:** implementada y validada contra Asterisk 20.6 y después contra Asterisk 22.5, la versión de producción, en el PBX de laboratorio (44 de 44, ver «Hallazgos»). Falta el trunk del proveedor y el audio de un navegador real (P-01).

- **Decisión.** Asterisk 20+ con `chan_pjsip`, WSS hacia el navegador, trunk existente hacia la PSTN y control por ARI.
- **Dónde.** `infra/asterisk/` (AMI deshabilitado en `modules.conf`). `services/ari/traductor.py` convierte eventos ARI en hechos. Cada llamada se origina con `channelId = llamada_id`, así que no hay correlación manual por `actionId`/`uniqueId` (las 956 líneas del `CallManagerImpl` legacy).
- **Regla de acoplamiento.** La aplicación nunca consulta a Asterisk el estado de una llamada. Al reiniciar, `services/ari` reconstruye qué canal es de qué puesto leyendo la base.
- **Verificación.** `tests/test_ari.py` con un puerto doble: preview, no contesta, abandono, máquina y canal ajeno. Contra Asterisk real, `scripts/probar_pbx.py` (`docs/despliegue.md`, apéndice A).

### ADR-004 — SIP.js con interfaz propia; teclado según el modo

**Estado:** implementada; el codec sigue abierto (P-02).

- **Decisión.** SIP.js embebido. El control de llamada va en la pantalla del cuestionario. El teclado es un panel que aparece solo en modo manual.
- **Dónde.** `web/js/telefono.js`; SIP.js 0.21.2 vendoreado como ESM en `web/vendor/sip.js`, sin build. En manual el sistema sí ve la llamada: pasa por Asterisk y su disposición se registra como en los otros modos, sin forzar "Contesta" como el `CatiControlManualMode` legacy.
- **Codec.** G.711 (`alaw,ulaw`) en todos los puestos y en el trunk: Asterisk solo puentea. Opus se habilita **por encuestador** (`invoke.participante.opus_habilitado`), y `credenciales.rotar_sip` escribe `ps_endpoints.allow` en cada inicio de sesión: `alaw,ulaw` por defecto, `opus,alaw,ulaw` para quien lo tenga. La consola muestra el códec que negoció cada puesto y cuántos están transcodificando. Hasta la migración 0009 el valor por defecto incluía Opus en todos los puestos (encargo de cierre, D1).

### ADR-005 — TURN propio desde el día uno

**Estado:** implementada.

- **Dónde.** `infra/coturn/turnserver.conf` con `use-auth-secret` y cuotas para 30 sesiones. Solo retransmite hacia el PBX: no es un proxy abierto. La API entrega credenciales efímeras (HMAC del secreto compartido) en `invoke/servicios/credenciales.py`.
- **Dónde corre.** En una VM propia, no en la del PBX: coturn solo retransmite hacia la IP pública del PBX, y en la misma VM ese tráfico tendría que salir y volver a entrar por la misma IP pública (*hairpin*), algo que GCE no garantiza. Una e2-small cuesta menos que averiguarlo en el piloto.
- **Monitoreo.** El puesto reporta en cada muestra de `getStats()` si el candidato elegido es `relay`. La consola lo muestra como "TURN" y la proporción se consulta sobre `invoke.calidad_red`.

### ADR-006 — Cuestionario como JSON con esquema, editado gráficamente

**Estado:** implementada.

- **Dónde.** `schemas/cuestionario.schema.json` (JSON Schema 2020-12). Cubre el piso del DTD legacy: simple y múltiple (con "otro" y opción exclusiva), grilla, texto con patrón, número, código, fecha, hora, fórmula, validaciones por tipo y de pantalla, saltos por opción y por regla, fin con disposición, aleatorización de bloques, opciones y filas, y sustitución `{ficha.x}` / `{VAR}` / `{VAR.texto}`. Editor gráfico en `web/editor.html`, con vista previa que corre el motor real.
- **Versiones.** Una versión publicada no se modifica (trigger). Cada respuesta guarda el cuestionario con el que se registró.
- **Sin PYR.** No hay conversor. Los códigos 1998 y −1, reservados en el legacy, el esquema los rechaza.

### ADR-007 — El participante de la llamada es una abstracción

**Estado:** implementada como frontera.

- **Dónde.** Tabla `invoke.participante` con `tipo` (`encuestador_webrtc`, `agente_ia`). El asignador, el pacer y las proyecciones hablan de participante. El protocolo está en `invoke/dominio/participante.py`. El motor de cuestionario corre sin interfaz (`Motor.recorrer`): un agente de IA consumiría el mismo JSON.
- **Costo.** Una frontera de interfaces; el agente de IA no se construye (Fase 4).

### ADR-008 — Toda llamada pertenece a un caso, y el caso declara su origen

**Estado:** implementada.

- **Dónde.** `invoke.caso.origen` (`marco`, `derivado`, `reemplazo_manual`, `lista_externa`), `caso_origen`, `usuario_creador` y `motivo`, con CHECKs que exigen los campos según el origen. `invoke.clasificar_numero` detecta el caso límite (el "derivado" ya estaba en la muestra). `invoke.crear_caso_fuera_de_marco` exige el flag del estudio y el tope de la guía, y hereda estrato y propiedades como valor inicial editable. El diálogo de tres opciones aparece antes de discar y no se puede saltear.
- **No hay huérfanas.** Una disposición de teléfono sin caso se rechaza en la base, salvo en una llamada originada como administrativa, que no cuenta como intento.
- **Verificación.** `test_fuera_de_marco`, `test_tope_fuera_de_marco_por_guia`, `test_llamada_huerfana_rechazada`.

### ADR-009 — Vista de compatibilidad CDR, con fecha de caducidad

**Estado:** implementada; equivalencias pendientes (P-04).

- **Dónde.** `invoke.cdr_compat` (`0008_cdr_compat.sql`) y `GET /api/cdr`. Tiene campaña, agente, las dos disposiciones en un campo separado por `|`, intentos por franja, estrato, tipo de marcador y la marca `es_estudio` (en lugar de la lista de nombres en el script). El rol `invoke_lectura` es para el script del tablero.
- **Sin equivalente.** Los códigos 30, 31 y 32 salen como `SIN_EQUIVALENTE:<código>`, nunca forzados a otro valor. Mientras la columna de equivalencias del diccionario siga vacía, sale la etiqueta propia de INVOKE 2.
- **Retiro.** Escrito en la cabecera de la migración: la vista y el tablero viejo se retiran juntos el día que no quede ningún estudio en la solución anterior.

### ADR-010 — El stack es el de `paneles`, con una desviación acotada

**Estado:** implementada.

- **Dónde.** Python 3; Cloud Functions con único punto de entrada y ruteo propio (`functions/main.py` → `invoke/enrutador.py`); Cloud SQL con psycopg 3; migraciones SQL numeradas; Firebase Auth; HTML, CSS y JS plano sobre Hosting; pytest; región `southamerica-east1`. Tres procesos en Cloud Run (`services/ari`, `sala`, `pacer`), que son la única desviación.
- **Convenciones.** Las invariantes viven en la base, hay un `CLAUDE.md` con reglas duras y `scripts/verificar_accesos.py` falla si una migración abre un acceso de más. Tipos del frontend con JSDoc y `tsc --checkJs` en CI, sin `dist/`.
- **Aviso.** El repositorio `paneles` no estuvo accesible durante la implementación. El stack se siguió según su descripción en el PRD (P-09).

## Decisiones de arquitectura nuevas

### ADR-011 — Una pata de audio permanente por puesto

- **Decisión.** Al empezar el turno el navegador hace una sola llamada SIP a `sala-<participante>`, que entra a Stasis y queda en un puente mixto propio (`puesto-<id>`). Cada respondente se suma a ese puente desde ARI; colgar corta la pata del respondente, no la del puesto.
- **Por qué.** El camino H del PRD tiene un presupuesto de menos de 1 s desde el "hola" hasta la pantalla lista. Con la pata permanente el audio ya está negociado (ICE, DTLS, TURN) cuando alguien atiende, y el traspaso es agregar un canal a un puente. La ficha viaja en el mismo aviso de la sala. Además, los cuatro modos de discado usan el mismo mecanismo.
- **Seguridad.** El dialplan solo acepta `sala-N` si el endpoint autenticado es `pN`. La credencial SIP es efímera y en la base solo queda el hash md5.
- **Costo.** Un canal por puesto conectado (30 en pico). Si el puesto pierde la red, la pata se cae y el navegador reconecta solo.

### ADR-012 — Proyección operativa del caso, escrita solo por el trigger de eventos

- **Decisión.** `invoke.caso_estado` guarda lo que el asignador necesita leer rápido: estado, próximo intento, agenda, intentos por franja, sustituto activo y si hubo abandono. La escribe exclusivamente el trigger de `invoke.evento`, en la misma transacción y con `SECURITY DEFINER`; la aplicación no tiene privilegio de escritura sobre ella.
- **Por qué no contradice ADR-002.** No es un contador de reporte: ningún reporte la lee. Además es reconstruible: `invoke.reconstruir_caso_estado(caso)` la rehace reproduciendo los eventos, y `test_proyeccion_reconstruible` verifica que coincide con la incremental.
- **Cierres por tope.** Cuando la proyección cierra un caso por tope de intentos, de disposición o de franjas, emite el hecho `caso_cerrado`, así el cierre también queda en el log (DI-02).

### ADR-013 — El PBX: Asterisk 22 LTS de Ubuntu 26.04, en Docker bajo systemd, configurado como código

**Estado:** implementada y validada con la prueba de laboratorio (44 de 44 en Asterisk 22.5.2). El procedimiento está en `docs/despliegue.md`, pasos 6 a 8.

- **Contexto.** El PRD decide *qué* (Asterisk 20 o superior con ARI, ADR-003) pero no *cómo se instala y opera*. El PBX es parte del alcance: lo despliega y lo opera Equipos, así que su configuración se versiona y se prueba como el resto.
- **Versión y origen.** Asterisk 22 LTS, del paquete de Ubuntu 26.04 LTS (`asterisk`, `asterisk-modules`, `asterisk-core-sounds-es`; 22.5.2 al momento de escribir esto). Trae todo lo que el sistema usa (`chan_pjsip`, `res_ari`, `res_stasis_snoop`, `app_amd`, `res_config_pgsql`), verificado módulo por módulo, e incluye `codec_opus_open_source`: Asterisk oficial no trae un Opus libre (el de Sangoma es un binario cerrado), y sin él no hay Opus por encuestador (H4).
- **Por qué 22 y no 20.** La primera versión de este ADR usaba Asterisk 20.6 de Ubuntu 24.04, porque era el paquete disponible y fue lo primero que se validó. Asterisk 20 ya está en su etapa de solo parches de seguridad, que termina en octubre de 2027: el piloto habría tenido que migrar el PBX con el sistema funcionando. Asterisk 22 tiene soporte completo hasta 2028 y de seguridad hasta 2029, y como todavía no hay nada en producción, cambiar ahora cuesta lo mínimo. La migración se validó con la misma prueba y dejó los hallazgos de la tabla de abajo; el código de `services/ari` no cambió.
- **No se compila.** El paquete es lo que se prueba, y compilar suma un proceso de construcción que mantener y el problema del Opus libre. **Costo:** el paquete está en *universe* (parches de la comunidad, no de Canonical) y va atrasado respecto de Asterisk oficial (22.5.2 contra 22.10). Se mitiga reconstruyendo la imagen todos los meses y ante cada aviso de seguridad (`docs/despliegue.md`, «Operar»).
- **Empaquetado.** Una imagen Docker propia (`infra/asterisk/Dockerfile`, base `ubuntu:26.04`) con las plantillas `.conf` del repositorio; `entrypoint.sh` las completa con variables de entorno al arrancar y crea lo que en un contenedor nadie crea (el directorio del socket de control y el de grabaciones). El contenedor usa la red del host (sin NAT de Docker sobre 10.000 puertos RTP) y lo gobierna una unidad de systemd (`invoke-pbx.service`) en una VM Ubuntu 26.04 LTS. No se usó Container-Optimized OS porque el host necesita `gcloud` (secretos), `certbot` y systemd.
- **Secretos.** `cargar_entorno.sh` los trae de Secret Manager a `/run` (tmpfs) en cada arranque, con una cuenta de servicio que solo lee esos tres secretos. No hay secretos en la imagen ni en el disco de la VM.
- **Superficie.** Solo `chan_pjsip` (en Asterisk 22 ya no existe `chan_sip`), AMI apagado, CDR y CEL apagados (el registro es `invoke.evento`, ADR-002), sin MWI, música en espera, ADSI ni STIR/SHAKEN, ARI en la IP privada, SIP 5060 solo desde el proveedor, WSS con certificado de Let's Encrypt y renovación que espera a que no haya canales. Lo que se carga está en `modules.conf` con el motivo de cada exclusión, y el arranque no deja errores ni advertencias en el log.
- **Endpoints.** Los de los puestos son realtime desde Postgres, con un usuario de solo lectura, porque la API rota la credencial en cada sesión (DI-12) y Asterisk no tiene que recargar nada. El trunk y los transportes van en archivo: no cambian en operación.
- **NAT.** GCE hace NAT 1:1, así que el PBX declara su IP pública (`external_media_address` y `external_signaling_address` hacia afuera de `10.0.0.0/8`) y publica como candidato ICE de host la IP pública mapeada (`rtp.conf`). No hace falta STUN en el PBX (se quitó `STUN_HOST`).
- **Prueba.** Hay un dialplan de laboratorio (`laboratorio/extensions_laboratorio.conf`) que simula la PSTN con canales Local: persona, ocupado, no contesta, contestador e inexistente. Con él, `scripts/probar_pbx.py` recorre los flujos de ARI contra la misma imagen. Se activa solo con `INVOKE_PBX_LABORATORIO=1`, nunca en producción. Se corre antes de cada cambio en `infra/asterisk` o en `services/ari`, y después de cada reconstrucción de la imagen.
- **Alternativas descartadas.** Quedarse en Asterisk 20: obliga a migrar en pleno piloto. Compilar Asterisk 22 oficial: parches más rápidos, pero un proceso de construcción que mantener y sin Opus libre; queda como salida si un aviso de seguridad no llega al paquete. Asterisk instalado directo en la VM sin contenedor: la configuración quedaría fuera del ciclo de versiones del repositorio. Un PBX administrado de terceros: contradice ADR-003 (control por ARI) y el costo de licencia que motiva el proyecto.

## Decisiones de implementación

Lo que los documentos dejaron sin precisar y el código resolvió. Las provisorias se cambian por configuración o con una migración nueva, sin tocar lógica.

| # | Decisión | Dónde | Estado |
| --- | --- | --- | --- |
| DI-01 | Los reportes se calculan con un **instante de corte** (`p_hasta`): el mismo pedido da el mismo número aunque el estudio siga corriendo. Por defecto, el reloj del momento. | `invoke.clasificar_casos`, `conteo_tasas` | Tomada |
| DI-02 | El cierre por tope queda como evento `caso_cerrado`, para que el corte en el tiempo sepa cuándo un no-contacto pasó a definitivo. | `invoke.cerrar_caso` | Tomada |
| DI-03 | Casos sin resolver: en un reporte **parcial** quedan "en proceso" (EP) y fuera de las tasas. En el de **cierre**: nunca llamado o sin contacto → UD; agendada sin cierre o contactado sin disposición → NC; con abandono → AB. | `0007_reportes.sql` | Tomada |
| DI-04 | Un caso con un abandono (30) o un falso positivo de AMD (32) y sin disposición de encuesta posterior se clasifica AB. | `clasificar_casos` | Tomada |
| DI-05 | Abandonada: espera de 24 h, en franja rotada, tope 2. Nunca vuelve al pacer: el asignador la ofrece en **preview** aunque el estudio esté en power o predictivo (regla del diccionario). | `regla_reintento_defecto`, `asignar_caso` | Provisoria |
| DI-06 | Topes por defecto: 8 intentos por caso y 3 por franja; ninguna disposición tiene tope propio salvo la abandonada. Franjas por defecto de la línea base: mañana 09:00–16:30 y tarde 16:30–21:00 (hora de Montevideo). | `invoke.estudio`, trigger de defectos | Provisoria |
| DI-07 | La disposición 50 "No aplica" no está en los grupos del documento de fórmulas: provisoriamente es **NE**. | `disposicion_encuesta` | Provisoria |
| DI-08 | Estados extendidos 13–18: sin símbolo de cálculo. Las finales se reportan aparte como "SC", y Recall, Retomar y Corregir (15, 16, 18) reabren el caso. Solo los aplica un supervisor. | `disposicion_encuesta.efecto_caso` | Tomada |
| DI-09 | Cascada del asignador heredada del `FIFOScheduler`: agenda propia en ventana estrecha (±30 min) → agenda libre estrecha → agenda propia → agenda libre → pool común. En el pool, cada pedido sortea primero la población (frescos o reintentos, DI-24) y dentro de ella elige por prioridad de guía y **orden aleatorio reproducible** (DI-25), en lugar del `rand()` por consulta del legacy. La agenda es una ventana única, no la ventana diaria repetida (ambigua en el legacy). | `invoke.asignar_caso`, `invoke.pedir_al_pool` | Tomada |
| DI-10 | Cuotas: tres tipos como el legacy (pregunta, área, propiedad de la ficha), con objetivo absoluto o % del objetivo del estudio. Las de área y propiedad filtran la asignación. Las de pregunta se evalúan **en cada pantalla**, no como foto al cargar el caso. Una completa que excede la cuota por concurrencia se acepta: no se descarta una entrevista hecha. | `cuota_avance`, `evaluar_cuotas` | Tomada |
| DI-11 | Teléfonos normalizados al número nacional significativo uruguayo (sin 598 ni 0). Hace que la lista negra sea transversal de verdad: `099 123 456` y `+598 99 123 456` son el mismo número. | `invoke.normalizar_telefono` | Tomada |
| DI-12 | Credenciales SIP efímeras: se rotan en cada inicio de sesión y en la base solo queda `md5(usuario:realm:clave)`, sin el texto plano que tenía el legacy. TURN con credenciales REST de 12 h. | `credenciales.py`, `asterisk.ps_auths` | Tomada |
| DI-13 | El servidor no confía en el navegador: antes de aceptar una completa, recorre la entrevista entera con el motor Python. Si hay errores, o el flujo termina con otra disposición, la completa se rechaza. | `estacion._validar_completa` | Tomada |
| DI-14 | Aleatorización reproducible: la semilla es el id del caso (FNV-1a + mulberry32, idénticos en Python y JS). Retomar una entrevista muestra el mismo orden. | `cuestionario.py`, `motor.js` | Tomada |
| DI-15 | Comandos de telefonía por una cola en Postgres (`comando_telefonia`, LISTEN/NOTIFY + SKIP LOCKED) con un único consumidor ARI. La API y el pacer nunca hablan con Asterisk. | `services/ari/main.py` | Tomada |
| DI-16 | La sala se alimenta de `pg_notify` del trigger de eventos. Hay como mucho una foto por segundo por estudio para la consola, y la ficha del caso viaja dentro del aviso de conexión. | `services/sala/main.py` | Tomada |
| DI-17 | "Llamada descartada": el pacer corta llamadas que todavía timbran porque la sala se vació. No es intento ni abandono, y el caso vuelve tal cual. | `services/pacer`, trigger | Tomada |
| DI-18 | Power: N = 1 / tasa de contacto observada en la franja (últimos 14 días, mínimo 30 intentos; si no, N inicial), acotado por N máximo. Predictivo: baja R 25 % si el abandono real supera el objetivo y sube 0,05 si hay ocio de más de 15 s. Actúa solo con 20 o más conexiones en la ventana y registra como mucho un ajuste por minuto. | `invoke/dominio/pacer.py` | A calibrar |
| DI-19 | La tasa de contacto se implementó literal: Agendada y Fuera de Cuota entran como NC en el denominador pero no en el numerador, aunque hubo contacto. **A revisar con investigación**: probablemente deberían contarse como contacto. | `formulas.tasas_marco` | A revisar |
| DI-20 | Exportación: una variable 0/1 por opción en las múltiples y una por fila en las grillas. Cada registro lleva `origen` y `ponderable` (0 para fuera de marco), y el .sav lleva etiquetas de variable y de valor. | `exportacion.py` | Tomada |
| DI-21 | El supervisor escucha con `snoop` de ARI sobre la pata del encuestador (oye a las dos partes). Con "susurrar" le habla solo al encuestador. Para escuchar, conecta su propia pata de audio desde la consola. | `supervision.escuchar` | Tomada |
| DI-22 | La lista externa se carga como guía con `origen = lista_externa`: queda fuera del marco (decisión 8 del diccionario) y lleva quién la cargó. | `muestra.cargar_guia` | Tomada |
| DI-23 | Un estudio fija sus versiones de diccionario y fórmulas al activarse: un trigger impide cambiarlas después. | `tg_estudio_versiones_fijas` | Tomada |
| DI-24 | **Mezcla de frescos y reintentos** (encargo H1). `estudio.proporcion_frescos` (0,5 por defecto) es la probabilidad de sortear la población de casos nunca intentados en **cada pedido** al pool, del asignador y del pacer; no es un cupo acumulado. Fresco = `caso_estado.intentos = 0`, que el trigger deriva del log. Si la población sorteada está vacía se usa la otra: nadie espera porque "su mitad" se agotó. Cada entrega registra la población en el evento (`poblacion`, `sorteo`) y la consola muestra el reparto efectivo del día junto al configurado. | `invoke.pedir_al_pool`, `invoke.reparto_frescos` | Tomada |
| DI-25 | **Orden aleatorio reproducible** (encargo H3). Cada guía tiene una `semilla` fijada al cargarla y registrada en `muestra_cargada`; el orden de cada caso es `orden_determinista(semilla, número de línea)`, un hash MD5 (los casos creados desde el puesto usan su id). La misma guía con la misma semilla da el mismo orden. El orden no se modifica después (trigger). El pool lee el índice `caso_pool_idx (estudio_id, guia_id, orden_aleatorio)` guía por guía: el plan es `Index Scan using caso_pool_idx` + `LockRows`, sin `Sort` (verificado con `EXPLAIN` sobre 20.000 casos, `test_el_pool_lee_el_indice`). | `0010_orden_y_frescos.sql` | Tomada |
| DI-26 | **Se graban todas las llamadas de encuesta conectadas** (encargo H2); reemplaza al muestreo por estudio. `llamada_originada.grabar` es siempre verdadero para encuesta y falso para administrativas; `estudio.muestreo_grabacion` queda sin uso. `services/ari` baja cada grabación por ARI, la sube al bucket (`<estudio>/<AAAA>/<MM>/<DD>/<llamada>.wav`) con verificación de MD5, registra `grabacion_subida` y recién entonces la borra del PBX. Si el proceso se reinicia retoma desde `invoke.grabacion_pendiente`. La consola escucha con una URL firmada de 15 minutos. Las completas de origen fuera de marco ya no "entran con prioridad al muestreo": quedan marcadas para **revisión prioritaria** de su grabación. | `services/ari/grabaciones.py`, `invoke/servicios/grabaciones.py` | Tomada |
| DI-27 | El traductor de ARI deriva el estado de una llamada que termina (atendida, conectada, AMD, abandono) de `invoke.llamada`, no de la memoria: un reinicio con llamadas en curso ya no escribe un no-contacto sobre una llamada conversada (encargo D2). La llamada se registra al originarla, así el estado «timbrando» llega a la sala (D3). | `services/ari/traductor.py` | Tomada |
| DI-28 | Las rutas del puesto que leen respuestas, inician la encuesta, evalúan cuotas o clasifican exigen que el caso sea del encuestador, o que la llamada clasificada sea suya (encargo D4). | `estacion._exigir_caso_propio` | Tomada |
| DI-29 | Los reportes (clasificación de casos, productividad, abandono, contacto observado) leen el diccionario de la versión del estudio: `estado`, `simbolo` y `efecto_caso`, sin códigos literales (encargo D5). Quedan dos literales con su test: `UD` para el caso nunca resuelto al cierre (definición del documento de fórmulas) y 31 en el conteo de contestadores detectados, porque el catálogo no tiene un atributo que lo distinga del 9. Siguen con literales, también testeados, dos invariantes de la base: "solo 4 habilita encuesta" y la marca de abandonada previa (30). | `0011_reportes_leen_diccionario.sql` | Tomada |
| DI-30 | Robustez: las expresiones del cuestionario tienen un límite de profundidad (64) además del de nodos, gemelo en Python y JS, y una definición anidada de más da 422 con mensaje (M1). La API convierte toda excepción en una respuesta JSON (500, o 503 si el pool de conexiones se agotó) (M2). El pool tiene tamaño y espera explícitos (`INVOKE_DB_POOL`, `INVOKE_DB_ESPERA_S`) y la concurrencia de Functions coincide con él (M3). La sala recalcula solo la foto de los estudios que cambiaron (M4). | `expresiones.py`/`.js`, `api.py`, `db.py`, `services/sala` | Tomada |
| DI-31 | **El AMD entra por ARI, no por el dialplan.** Toda llamada se origina hacia la aplicación Stasis (`amd,<llamada>` o `cliente,<llamada>`). En la de power y predictivo, `ari` registra la atención y le pasa el canal al dialplan con `continue` (`invoke-amd`), que corre `AMD()` y vuelve a Stasis con el resultado (`cliente,<llamada>,<AMDSTATUS>`). Originar directo al dialplan perdía los eventos de la atención (verificado contra Asterisk 20.6 y 22.5). | `services/ari/main.py`, `traductor.py`, `extensions.conf` | Tomada |
| DI-32 | El resultado `NOTSURE` del AMD se trata como persona: es preferible un encuestador que oye un contestador (y lo clasifica como 32, falso positivo) a cortarle a una persona. | `traductor.py` | Tomada |
| DI-33 | En un abandono, el mensaje de disculpas se reproduce entero y recién al terminar (`PlaybackFinished`) se corta. Antes se cortaba al empezar y la persona no oía nada. Si el mensaje no se puede reproducir, se corta igual. En producción el PBX no arranca sin el archivo. | `traductor.py`, `entrypoint.sh` | Tomada |
| DI-34 | Un encuestador con un caso sin clasificar no puede tomar casos de otro estudio ni iniciar sesión en otro estudio hasta clasificarlo. Antes, el caso pendiente "cruzaba" de estudio. | `0012_caso_pendiente_de_otro_estudio.sql`, `estacion.iniciar_sesion` | Tomada |
| DI-35 | `ari` solo trata como llamada a los canales cuyo id es un UUID (los que origina con `channelId = llamada_id`). Los demás canales (patas de puesto, snoop, Local del laboratorio) nunca generan hechos de llamada. | `services/ari/main.py` (`es_llamada`) | Tomada |
| DI-36 | Las plantillas se completan con una lista explícita de variables: sin ella, `envsubst` vaciaba las variables del dialplan (`${EXTEN:5}`, `${CHANNEL(endpoint)}`, `${LLAMADA}`) y el PBX arrancaba roto sin avisar. El contenedor no arranca si falta una variable o el certificado. | `entrypoint.sh` | Tomada |

### Hallazgos de la validación contra Asterisk real

El código de telefonía se había escrito contra la documentación de ARI. Se corrió dos veces contra Asterisk real, con la configuración renderizada igual que en producción: primero contra Asterisk 20.6 (Ubuntu 24.04) y después contra Asterisk 22.5.2 (Ubuntu 26.04), la versión de producción (ADR-013). Aparecieron estos problemas, todos corregidos. Resultado final en las dos versiones: `scripts/probar_pbx.py`, 44 de 44.

| Hallazgo | Efecto | Corrección |
| --- | --- | --- |
| **Asterisk 20.6** | | |
| `noload => manager.so` no apaga AMI: el módulo es parte del núcleo | AMI quedaba en su configuración por defecto | `manager.conf` con `enabled = no` |
| `res_pgsql.conf` sin la sección `[general]` | Realtime no conectaba: ningún puesto podía registrarse | Sección `[general]` y usuario `asterisk_rt_login` |
| `sorcery.conf` sin las fuentes de archivo | El trunk y los transportes de `pjsip.conf` desaparecían al activar realtime | Fuente `config` antes de `realtime` para cada objeto |
| `chan_sip` y `chan_pjsip` cargados juntos | Peleaban por 5060 | `chan_sip` y los demás canales en `noload` |
| CDR y CEL activos | Ruido en el log y un segundo registro de llamadas | Apagados (ADR-002) |
| Canales que no son llamadas (patas, snoop) | Hechos de llamada sobre ids que no existen | DI-35 |
| Originar al dialplan para el AMD | Se perdía la atención: el caso quedaba como no contesta | DI-31 |
| `Busy()` del laboratorio devolvía causa 0 | El ocupado se registraba como falla técnica | El laboratorio usa `Hangup(17)`, la causa que manda la red real |
| Corte inmediato en el abandono | La persona no oía el mensaje | DI-33 |
| Caso pendiente en otro estudio | El encuestador arrastraba un caso de un estudio a otro | DI-34 |
| `CREATE OR REPLACE` de una función `SECURITY DEFINER` | Perdía el atributo y la aplicación no podía asignar | La migración `0012` lo vuelve a fijar; queda como regla para las próximas |
| `envsubst` sin lista | Dialplan roto en silencio | DI-36 |
| **Asterisk 22.5** | | |
| Sin init, nadie crea `/var/run/asterisk` | El contenedor no arrancaba | Lo crea `entrypoint.sh` |
| El paquete de Ubuntu 26.04 no trae `/var/spool/asterisk/recording` | ARI respondía 500 al grabar: llamadas sin grabación | Lo crea `entrypoint.sh` |
| `res_pjsip_mwi` consulta la columna `mailboxes` de los endpoints realtime al arrancar | Error de la base en cada arranque | MWI fuera (no hay buzones) |
| El paquete ya no trae música en espera; `users.conf`, ADSI y STIR/SHAKEN generan advertencias | Ruido en el log | Módulos fuera y `users.conf` borrado en la imagen. El arranque queda sin errores ni advertencias |
| La prueba usaba la CLI de Asterisk del host | No servía con el PBX en un contenedor | `LAB_ASTERISK_CLI` en `scripts/probar_pbx.py` |


Ninguno bloquea arrancar la prueba de audio; algunos sí bloquean el piloto.

| # | Pendiente | Quién | Bloquea |
| --- | --- | --- | --- |
| P-01 | Integración con Asterisk: **validada en el laboratorio** (originar, timbre, AMD, puentes, snoop, grabación y subida, abandono, reinicio de `ari`: 44 de 44). Falta lo que el laboratorio no puede simular: el trunk del proveedor (0.3 de la guía de despliegue) y el audio de un navegador real (SIP.js, ICE, DTLS-SRTP, TURN y Opus negociado), con los puntos 8 a 10 del paso 14 de la guía de despliegue. | Sistemas | Piloto |
| P-02 | Prueba de audio de una semana con 3 o 4 encuestadores: medir pérdida, jitter y proporción por TURN (ya se registran en `calidad_red`) y decidir el codec de ADR-004. | Sistemas y campo | Piloto |
| P-03 | Valores por defecto de investigación: topes de intentos (DI-06), tratamiento de la abandonada (DI-05), clasificación de "No aplica" (DI-07) y tasa de contacto (DI-19). | Investigación | Reporte al cliente |
| P-04 | Columna de equivalencias con el CDR actual: se completa en `disposicion_*.equivalente_cdr` listando los valores distintos del CDR de estudios reales. | Sistemas | Tablero unificado |
| P-05 | ~~Subida de grabaciones al bucket.~~ **Cerrado** por el encargo de cierre (H2, DI-26). Queda abierta la retención: ver P-11. | — | — |
| P-06 | Mensaje de audio `invoke-disculpas` para llamadas abandonadas (texto y grabación). El PBX de producción no arranca sin él (DI-33). | Investigación | Fase 2 |
| P-07 | Transferencia de llamada: el botón no existe y el comando responde "no soportado". Definir si el piloto la necesita. | Campo | — |
| P-08 | Fecha de renovación y costo anual de la licencia actual (supuestos 1 y 2 del PRD): convierten el proyecto en un caso de negocio con fecha. | Dirección | Presupuesto |
| P-09 | Cotejar con `paneles` las convenciones de despliegue (nombres de secretos, scripts de verificación) que no se pudieron leer. | Sistemas | — |
| P-10 | Línea base defendible: más estudios, fin de semana y franja de tarde sobre la solución actual, antes de cortarla. | Campo | Criterio de salida de Fase 0 |
| P-11 | Plazo de retención de las grabaciones. Con todas las llamadas grabadas son del orden de 25 GB por mes de operativo a sala llena, acumulativos. Se aplica como regla de ciclo de vida del bucket (`docs/despliegue.md`, paso 5). | Equipos | Cierre de H2 en producción |
| P-12 | Qué encuestadores arrancan con Opus habilitado, si alguno. El valor por defecto es ninguno hasta que la prueba de audio (P-02) diga lo contrario. | Campo | — |
| P-13 | Confirmar cómo se declara al cliente un «No volver a llamar»: el diccionario lo clasifica como elegibilidad desconocida (UD) y no como rechazo; con `e = 1` la tasa de respuesta no cambia, pero la de rechazo sí. Está implementado según el documento de fórmulas. | Investigación | Primer informe con tasas |
| P-14 | La política de casos fuera de marco (control 3) todavía dice que las completas derivadas "entran con prioridad al muestreo de grabación". Con H2 ya no hay muestreo: conviene actualizar el texto a "revisión prioritaria de su grabación", que es lo que hace el sistema. | Investigación | — |
| P-15 | ~~Pasar el PBX a Asterisk 22 LTS.~~ **Cerrado**: el PBX usa Asterisk 22 de Ubuntu 26.04 (ADR-013), validado 44 de 44. | — | — |

## Encargo de cierre de la Fase 1

Estado de los puntos de `specs/INVOKE 2 — Encargo de cierre de la Fase 1.md`.

| # | Punto | Estado | Dónde | Verificación |
| --- | --- | --- | --- | --- |
| D1 + H4 | Opus activado en todos los puestos; códec por encuestador | Cerrado. En el laboratorio, el endpoint realtime de cada puesto ofrece `alaw,ulaw` u `opus,alaw,ulaw` según su configuración; falta ver la negociación en un navegador real (P-01) | `0009`, `credenciales.rotar_sip`, administración de usuarios, consola | `test_codec_g711_por_defecto_y_opus_por_puesto`; `scripts/probar_pbx.py` |
| D2 | El traductor de ARI perdía el estado al reiniciar | Cerrado | `services/ari/traductor.py`, `PuertoReal.estado_llamada` | `test_reinicio_con_llamada_conectada_no_inventa_un_no_contacto`, `test_estado_llamada_desde_la_base` |
| D3 | `llamada_timbrando` no se emitía | Cerrado | `Traductor.registrar_originada` | `test_timbre_se_emite`, `test_timbre_despues_de_reiniciar_y_canales_ajenos` |
| H2 | Grabar todas las llamadas y subirlas al bucket | Cerrado; falta definir la retención (P-11) | `0009`, `0010`, `services/ari/grabaciones.py` | `tests/test_grabaciones.py` |
| H3 | Orden aleatorio reproducible e indexado | Cerrado | `0010`, `muestra.cargar_guia` | `test_semilla_reproduce_el_orden`, `test_el_pool_lee_el_indice` |
| H1 | Mezcla de frescos y reintentos | Cerrado | `0010`, consola | `test_mezcla_de_frescos_sorteada_en_cada_pedido` (1.000 pedidos, ±3 puntos), `test_si_la_poblacion_sorteada_esta_vacia_se_usa_la_otra`, `test_pacer_mezcla_y_graba` |
| D4 | Tres rutas del puesto sin control de propiedad | Cerrado (y una cuarta: evaluar cuotas) | `estacion._exigir_caso_propio` | `test_el_puesto_no_toca_casos_ajenos` |
| D5 | Los reportes no leían el diccionario | Cerrado | `0011` | `test_los_reportes_leen_el_diccionario_del_estudio`, `test_literales_que_quedan` |
| M1–M5 | Menores | Cerrados | ver DI-30; `.DS_Store` fuera del repositorio | `test_definicion_anidada_de_mas_da_error_y_no_revienta`, casos de profundidad en la batería compartida, `test_errores_inesperados_y_pool_agotado_son_respuestas`, `test_sala_solo_recalcula_los_estudios_que_cambiaron` |

## Trazabilidad por fase

| Fase | Estado | Qué hay |
| --- | --- | --- |
| 0 | Cargada | Diccionario v1 y fórmulas v1 en la base (`0002`), con un test por decisión del documento de fórmulas. Falta la línea base completa (P-10). |
| 1 — núcleo | Construida | Telefonía ARI, puesto con SIP.js y prueba de audio, consola con calidad de red y escucha, motor y editor de cuestionario, muestra, cuotas, log de eventos, reportes del piloto, exportación .sav, CSV y Excel, y vista CDR. |
| 2 — power | Lógica | Inversión del orden, reserva de casos, abandono, N desde la tasa de contacto, indicadores en la consola y control de N. Calibrar con datos reales. |
| 3 — predictivo | Lógica | Lazo asimétrico sobre abandono real en ventana deslizante, caída a power con menos de 8 agentes, y revisión de AMD (31 → 32) con su tasa de error. |
| 4 | Puerta abierta | La frontera de participante existe, el cuestionario corre sin interfaz y el asignador discrimina por tipo. No se construye nada más, por diseño. |
