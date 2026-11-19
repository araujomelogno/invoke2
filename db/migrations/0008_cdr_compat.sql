-- 0008 — Vista de compatibilidad con forma de CDR (ADR-009), con fecha de caducidad.
--
-- CRITERIO DE RETIRO: esta vista y el tablero viejo se retiran juntos el día que
-- no quede ningún estudio corriendo en la solución anterior. No se mantiene por
-- inercia. Se deriva del log de eventos; no es fuente de verdad de nada.
--
-- Convenciones replicadas del CDR actual (que es lo que el tablero ya lee):
--   * campaña = código del estudio, agente = cuenta del encuestador
--   * las dos disposiciones en un mismo campo, separadas por barra vertical
--   * contadores de intentos por franja y estrato
--   * tipo de marcador
-- Mapeo de ida solamente (INVOKE 2 → vocabulario viejo). Un código sin
-- equivalente aparece como SIN_EQUIVALENTE:<código>, nunca forzado a otro.

CREATE FUNCTION invoke.etiqueta_cdr(p_equivalente text, p_sin boolean, p_codigo smallint, p_etiqueta text)
RETURNS text LANGUAGE sql IMMUTABLE AS $$
  SELECT CASE
    WHEN p_codigo IS NULL THEN ''
    WHEN p_sin THEN 'SIN_EQUIVALENTE:' || p_codigo
    WHEN p_equivalente IS NOT NULL THEN p_equivalente
    ELSE p_etiqueta                                    -- equivalencia pendiente de completar
  END
$$;

CREATE VIEW invoke.cdr_compat AS
  SELECT
    l.originada_en                                     AS calldate,
    e.codigo                                           AS campana,
    e.es_estudio                                       AS es_estudio,
    split_part(u.email, '@', 1)                        AS agente,
    l.numero                                           AS dst,
    l.llamada_id::text                                 AS uniqueid,
    COALESCE(l.segundos_total, 0)                      AS duration,
    COALESCE(l.segundos_conectada, 0)                  AS billsec,
    CASE WHEN l.atendida_en IS NOT NULL THEN 'ANSWERED'
         WHEN l.disp_tel = 3 THEN 'BUSY'
         WHEN l.disp_tel IN (7, 8) THEN 'FAILED'
         ELSE 'NO ANSWER' END                          AS disposition,
    invoke.etiqueta_cdr(dt.equivalente_cdr, dt.sin_equivalente, l.disp_tel, dt.etiqueta)
      || '|' ||
    invoke.etiqueta_cdr(de.equivalente_cdr, de.sin_equivalente, enc.codigo, de.etiqueta)
                                                       AS disposicion,
    (SELECT count(*) FROM invoke.llamada x WHERE x.caso_id = l.caso_id AND x.disp_tel IS NOT NULL
       AND x.originada_en <= l.originada_en AND invoke.franja_de(l.estudio_id, x.originada_en) = 'manana')::int AS intentos_manana,
    (SELECT count(*) FROM invoke.llamada x WHERE x.caso_id = l.caso_id AND x.disp_tel IS NOT NULL
       AND x.originada_en <= l.originada_en AND invoke.franja_de(l.estudio_id, x.originada_en) = 'tarde')::int AS intentos_tarde,
    c.estrato                                          AS estrato,
    c.origen                                           AS origen_caso,
    l.modo                                             AS tipo_marcador,
    l.tipo                                             AS tipo_llamada
  FROM invoke.llamada l
  JOIN invoke.estudio e ON e.id = l.estudio_id
  LEFT JOIN invoke.caso c ON c.id = l.caso_id
  LEFT JOIN invoke.participante p ON p.id = l.participante_id
  LEFT JOIN invoke.usuario u ON u.uid = p.usuario_uid
  LEFT JOIN invoke.disposicion_telefono dt ON dt.version_diccionario = e.version_diccionario AND dt.codigo = l.disp_tel
  LEFT JOIN LATERAL (
    SELECT (x.datos ->> 'codigo')::smallint AS codigo FROM invoke.evento x
    WHERE x.llamada_id = l.llamada_id AND x.tipo = 'disposicion_encuesta' ORDER BY x.id DESC LIMIT 1) enc ON true
  LEFT JOIN invoke.disposicion_encuesta de ON de.version_diccionario = e.version_diccionario AND de.codigo = enc.codigo
  WHERE NOT l.descartada;

GRANT SELECT ON invoke.cdr_compat TO invoke_app, invoke_lectura;
GRANT EXECUTE ON FUNCTION invoke.etiqueta_cdr(text, boolean, smallint, text) TO invoke_app, invoke_lectura;
