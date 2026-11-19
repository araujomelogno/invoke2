# INVOKE 2 — reglas duras

CATI propio de Equipos Consultores. Antes de tocar nada, leé `specs/INVOKE 2.0 — PRD por fases y decisiones de arquitectura.md`
y, para cualquier cosa que toque disposiciones o reportes, `docs/Diccionario de disposiciones — INVOKE 2.md` y
`docs/Fórmulas de reporte y mapeo AAPOR — INVOKE 2.md`. Esos documentos se implementan **literalmente**.
Las decisiones de implementación están en `docs/decisiones.html`.

## Reglas que no se discuten

1. **Postgres es la fuente de verdad** (ADR-001). Firebase solo autentica y pinta presencia. Nunca se lee estado de Firebase para decidir algo.
2. **Nada se cuenta con contadores mutables** (ADR-002). Todo hecho es una fila en `invoke.evento`, que es inmutable (trigger + privilegios).
   Un reporte es una consulta sobre eventos. `caso_estado` y `participante_estado` son proyecciones operativas: las escribe solo el trigger
   de eventos, se pueden reconstruir (`invoke.reconstruir_caso_estado`) y **ningún reporte las lee**.
3. **Las invariantes viven en la base**: exclusividad de asignación, conteo de cuotas, tope de intentos, lista negra, "solo 4 habilita
   encuesta", motivo obligatorio, promoción de parciales, llamadas sin caso. No se repiten en Python; Python llama a la función SQL.
4. **Migraciones**: SQL numeradas en `db/migrations/`. Una migración aplicada no se edita: se agrega otra. `scripts/migrar.py` falla si
   cambió el checksum.
5. **Accesos**: ningún privilegio a PUBLIC. Todo GRANT nuevo va en el mismo cambio a `db/accesos_esperados.txt`
   (`python scripts/verificar_accesos.py --dsn ... --regenerar` y revisar el diff). La aplicación (`invoke_app`) nunca puede modificar
   un hecho ni escribir una proyección: el verificador falla si pasa.
6. **La aplicación nunca le pregunta a Asterisk el estado de una llamada** (ADR-003). Lo sabe por los eventos persistidos.
   `services/ari` es el único consumidor de ARI; los demás encolan en `invoke.comando_telefonia`.
7. **Toda llamada pertenece a un caso y todo caso declara su origen** (ADR-008), salvo la llamada administrativa, que no es intento.
8. **El participante es una abstracción** (ADR-007): el asignador y el pacer hablan de `participante`, nunca de "usuario en navegador".
9. **Expresiones del cuestionario**: AST restringido en JSON, dos implementaciones gemelas (`invoke/dominio/expresiones.py`,
   `web/js/expresiones.js`) y lo mismo para el motor (`cuestionario.py` / `motor.js`). Un cambio de semántica se hace en las dos y en la
   batería compartida `tests/compartidos/`. Nunca JavaScript arbitrario.
10. **Frontend sin paso de build** (como `paneles`): HTML, CSS y JS plano con módulos ES. Tipos con JSDoc y `tsc -p .` en CI. SIP.js
    está vendoreado en `web/vendor/sip.js`.
11. **Sin secretos en el repositorio**. Todo por variables de entorno (`invoke/config.py`) y Secret Manager. `INVOKE_AUTH_DEV=1` es
    solo para desarrollo local y nunca se configura en un despliegue.
12. **Versionado de definiciones**: un estudio fija su versión del diccionario y de fórmulas al activarse; un reporte histórico se
    calcula con esas versiones. Cambiar una fórmula es una versión nueva, no una edición.
13. **Alcance de la Fase 1**: solo los reportes que el piloto usa. No portar la biblioteca de reportes del legacy.

## Cómo verificar antes de proponer un cambio

```bash
INVOKE_TEST_DSN=postgresql://postgres@localhost:5432/postgres pytest -q
node --test tests/js/*.test.mjs
tsc -p .
```

Cada fórmula del documento de fórmulas tiene su test en `tests/test_formulas.py`. Si cambiás una disposición o una fórmula, el test
que corresponde tiene que cambiar en el mismo PR, con la referencia al documento.

## Mapa

- `db/migrations/` esquema, invariantes, asignador, reportes y vista CDR (con fecha de caducidad, ADR-009)
- `invoke/` núcleo Python compartido: `dominio/` (lógica pura), `servicios/` (casos de uso sobre SQL), `api.py` (rutas)
- `functions/` entrada de Cloud Functions (superficie administrativa)
- `services/ari`, `services/sala`, `services/pacer` procesos de Cloud Run (única desviación del stack de paneles)
- `web/` puesto, consola de supervisión, administración, editor de cuestionario
- `infra/` Asterisk, coturn, Dockerfiles · `docs/` manual, despliegue y decisiones
