-- 0011 — Los reportes leen el diccionario de la versión del estudio (encargo de cierre D5).
--
-- 0007 cableaba códigos del diccionario v1 como literales (4, 5, 30 en la
-- clasificación de casos; 3, 7, 4, 5 en productividad; 30 y 4/30/32 en abandono y
-- contacto). Con un diccionario v2 el SQL habría seguido aplicando la semántica de
-- v1 en silencio, contra la regla 12 de CLAUDE.md. Ahora cada decisión sale de las
-- columnas del catálogo de la versión del estudio:
--   disposicion_telefono.estado   'habilita_encuesta' (contestó), 'final' (cierra el caso)
--   disposicion_telefono.simbolo  'AB' (abandono), 'UD' (elegibilidad desconocida)
--   disposicion_encuesta.simbolo  C, I, R, NC, NE
--   disposicion_encuesta.efecto_caso 'agendar'
-- Literales que quedan, cada uno con su test: 'UD' para el caso nunca resuelto al
-- cierre (documento de fórmulas, no del diccionario) y 31 "contestador detectado
-- por el sistema" en la tasa de abandono, porque el catálogo no tiene un atributo
-- que lo distinga del 9.

CREATE OR REPLACE FUNCTION invoke.clasificar_casos(p_estudio bigint, p_corte text, p_hasta timestamptz DEFAULT clock_timestamp())
RETURNS TABLE (caso_id bigint, origen text, estrato text, simbolo text, ultima_disp_enc smallint,
               ultima_disp_tel smallint, intentos integer, participante_cierre bigint)
LANGUAGE sql STABLE AS $$
  WITH est AS (
    SELECT * FROM invoke.estudio WHERE id = p_estudio
  ),
  ev AS (
    SELECT e.* FROM invoke.evento e WHERE e.estudio_id = p_estudio AND e.ocurrido_en <= p_hasta
  ),
  enc AS (
    SELECT DISTINCT ON (ev.caso_id) ev.caso_id, (ev.datos ->> 'codigo')::smallint AS codigo, ev.participante_id
    FROM ev WHERE ev.tipo = 'disposicion_encuesta' ORDER BY ev.caso_id, ev.id DESC
  ),
  tel AS (
    SELECT ev.caso_id, ev.llamada_id, (ev.datos ->> 'codigo')::smallint AS codigo, ev.id
    FROM ev WHERE ev.tipo = 'disposicion_telefono' AND ev.caso_id IS NOT NULL
  ),
  rec AS (
    -- Reclasificación de AMD: el código al que pasó la llamada (32 en v1).
    SELECT ev.llamada_id, (ev.datos ->> 'a')::smallint AS a FROM ev WHERE ev.tipo = 'reclasificacion_amd'
  ),
  tel_resumen AS (
    SELECT t.caso_id,
           (array_agg(t.codigo ORDER BY t.id DESC))[1] AS ultima,
           (array_agg(dt.estado ORDER BY t.id DESC))[1] AS ultima_estado,
           (array_agg(dt.simbolo ORDER BY t.id DESC))[1] AS ultima_simbolo,
           count(*) FILTER (WHERE dt.cuenta_intento)::int AS intentos,
           bool_or(dt.simbolo = 'AB' OR dr.simbolo = 'AB') AS abandonada,
           bool_or(dt.estado = 'habilita_encuesta') AS contesto
    FROM tel t
    CROSS JOIN est
    JOIN invoke.disposicion_telefono dt ON dt.version_diccionario = est.version_diccionario AND dt.codigo = t.codigo
    LEFT JOIN rec ON rec.llamada_id = t.llamada_id
    LEFT JOIN invoke.disposicion_telefono dr ON dr.version_diccionario = est.version_diccionario AND dr.codigo = rec.a
    GROUP BY t.caso_id
  ),
  cerrado AS (
    SELECT DISTINCT ev.caso_id FROM ev WHERE ev.tipo = 'caso_cerrado'
  )
  SELECT c.id, c.origen, c.estrato,
    CASE
      WHEN de.simbolo IS NOT NULL AND de.efecto_caso <> 'agendar' THEN de.simbolo            -- disposición final clasificada
      WHEN de.codigo IS NOT NULL AND de.simbolo IS NULL AND de.efecto_caso = 'cerrar' THEN 'SC'  -- extendido sin clasificar
      WHEN de.codigo IS NOT NULL AND de.efecto_caso = 'reabrir' AND p_corte = 'parcial' THEN 'EP'
      WHEN de.efecto_caso = 'agendar' THEN                                                  -- agendada sin cierre
        CASE WHEN p_corte = 'cierre' OR ce.caso_id IS NOT NULL THEN de.simbolo ELSE 'EP' END
      WHEN tr.abandonada THEN
        CASE WHEN p_corte = 'cierre' OR ce.caso_id IS NOT NULL THEN 'AB' ELSE 'EP' END
      WHEN tr.ultima_estado = 'final' THEN tr.ultima_simbolo                               -- ej. No volver a llamar
      WHEN tr.contesto THEN
        CASE WHEN p_corte = 'cierre' OR ce.caso_id IS NOT NULL THEN 'NC' ELSE 'EP' END
      WHEN ce.caso_id IS NOT NULL THEN COALESCE(tr.ultima_simbolo, 'UD')                    -- tope alcanzado
      WHEN p_corte = 'cierre' THEN 'UD'                                                     -- nunca se supo (fórmulas)
      ELSE 'EP'
    END,
    enc.codigo, tr.ultima, COALESCE(tr.intentos, 0), enc.participante_id
  FROM invoke.caso c
  CROSS JOIN est
  LEFT JOIN enc ON enc.caso_id = c.id
  LEFT JOIN invoke.disposicion_encuesta de ON de.version_diccionario = est.version_diccionario AND de.codigo = enc.codigo
  LEFT JOIN tel_resumen tr ON tr.caso_id = c.id
  LEFT JOIN cerrado ce ON ce.caso_id = c.id
  WHERE c.estudio_id = p_estudio AND c.creado_en <= p_hasta
$$;

CREATE OR REPLACE FUNCTION invoke.tasa_abandono(p_estudio bigint, p_desde timestamptz, p_hasta timestamptz)
RETURNS TABLE (abandonadas integer, conectadas integer, fp_amd integer, amd_maquina integer,
               tab_reportada numeric, tab_real numeric)
LANGUAGE sql STABLE AS $$
  WITH l AS (
    SELECT l.*, dt.simbolo, dt.estado
    FROM invoke.llamada l
    JOIN invoke.estudio e ON e.id = l.estudio_id
    LEFT JOIN invoke.disposicion_telefono dt ON dt.version_diccionario = e.version_diccionario AND dt.codigo = l.disp_tel_original
    WHERE l.estudio_id = p_estudio AND l.tipo = 'encuesta' AND l.originada_en >= p_desde AND l.originada_en < p_hasta
  ), n AS (
    -- Abandono "reportado": intentos cuya disposición original es de abandono (AB) y no
    -- una revisión posterior (la 32 nace de reclasificar, se cuenta como falso positivo).
    SELECT count(*) FILTER (WHERE simbolo = 'AB' AND estado <> 'revision')::int AS ab,
           count(*) FILTER (WHERE conectada_en IS NOT NULL)::int AS con,
           count(*) FILTER (WHERE reclasificada)::int AS fp,
           count(*) FILTER (WHERE disp_tel_original = 31)::int AS maq
    FROM l
  )
  SELECT ab, con, fp, maq,
         CASE WHEN ab + con > 0 THEN round(ab::numeric / (ab + con), 4) END,
         CASE WHEN ab + con + fp > 0 THEN round((ab + fp)::numeric / (ab + con + fp), 4) END
  FROM n
$$;

CREATE OR REPLACE FUNCTION invoke.productividad(p_estudio bigint, p_desde timestamptz, p_hasta timestamptz)
RETURNS TABLE (participante_id bigint, nombre text, horas_sesion numeric, horas_en_llamada numeric,
               intentos integer, contactos integer, completas integer, interrumpidas integer, rechazos integer,
               agendadas integer, fuera_marco_creados integer)
LANGUAGE sql STABLE AS $$
  WITH est AS (
    SELECT * FROM invoke.estudio WHERE id = p_estudio
  ), s AS (
    SELECT participante_id,
           sum(EXTRACT(epoch FROM (least(fin, p_hasta) - greatest(inicio, p_desde)))) / 3600.0 AS horas
    FROM invoke.sesion
    WHERE estudio_id = p_estudio AND fin > p_desde AND inicio < p_hasta
    GROUP BY participante_id
  ), l AS (
    SELECT l.participante_id,
           sum(COALESCE(l.segundos_conectada, 0)) / 3600.0 AS horas_llamada,
           count(*) FILTER (WHERE l.disp_tel IS NOT NULL AND l.tipo = 'encuesta') AS intentos,
           count(*) FILTER (WHERE dt.estado = 'habilita_encuesta') AS contactos
    FROM invoke.llamada l
    CROSS JOIN est
    LEFT JOIN invoke.disposicion_telefono dt ON dt.version_diccionario = est.version_diccionario AND dt.codigo = l.disp_tel
    WHERE l.estudio_id = p_estudio AND l.originada_en >= p_desde AND l.originada_en < p_hasta
      AND l.participante_id IS NOT NULL
    GROUP BY l.participante_id
  ), d AS (
    SELECT ev.participante_id,
           count(*) FILTER (WHERE de.simbolo = 'C') AS c,
           count(*) FILTER (WHERE de.simbolo = 'I') AS i,
           count(*) FILTER (WHERE de.simbolo = 'R') AS r,
           count(*) FILTER (WHERE de.efecto_caso = 'agendar') AS ag
    FROM invoke.evento ev
    CROSS JOIN est
    JOIN invoke.disposicion_encuesta de ON de.version_diccionario = est.version_diccionario
                                       AND de.codigo = (ev.datos ->> 'codigo')::smallint
    WHERE ev.estudio_id = p_estudio AND ev.tipo = 'disposicion_encuesta'
      AND COALESCE(ev.datos ->> 'origen', 'encuestador') IN ('encuestador', 'sistema')
      AND ev.ocurrido_en >= p_desde AND ev.ocurrido_en < p_hasta
    GROUP BY ev.participante_id
  ), f AS (
    SELECT p.id AS participante_id, count(*) AS n
    FROM invoke.caso c JOIN invoke.participante p ON p.usuario_uid = c.usuario_creador
    WHERE c.estudio_id = p_estudio AND c.origen <> 'marco' AND c.creado_en >= p_desde AND c.creado_en < p_hasta
    GROUP BY p.id
  )
  SELECT p.id, p.nombre,
         round(COALESCE(s.horas, 0)::numeric, 2), round(COALESCE(l.horas_llamada, 0)::numeric, 2),
         COALESCE(l.intentos, 0)::int, COALESCE(l.contactos, 0)::int,
         COALESCE(d.c, 0)::int, COALESCE(d.i, 0)::int, COALESCE(d.r, 0)::int, COALESCE(d.ag, 0)::int,
         COALESCE(f.n, 0)::int
  FROM invoke.participante p
  LEFT JOIN s ON s.participante_id = p.id
  LEFT JOIN l ON l.participante_id = p.id
  LEFT JOIN d ON d.participante_id = p.id
  LEFT JOIN f ON f.participante_id = p.id
  WHERE s.horas IS NOT NULL OR l.intentos IS NOT NULL OR d.c IS NOT NULL
$$;

CREATE OR REPLACE FUNCTION invoke.tasa_contacto_observada(p_estudio bigint, p_franja text, p_desde timestamptz)
RETURNS TABLE (intentos integer, contactos_humanos integer, tasa numeric)
LANGUAGE sql STABLE AS $$
  WITH l AS (
    SELECT l.*, dt.estado, dt.simbolo
    FROM invoke.llamada l
    JOIN invoke.estudio e ON e.id = l.estudio_id
    LEFT JOIN invoke.disposicion_telefono dt ON dt.version_diccionario = e.version_diccionario AND dt.codigo = l.disp_tel
    WHERE l.estudio_id = p_estudio AND l.tipo = 'encuesta' AND l.originada_en >= p_desde
      AND NOT l.descartada AND l.disp_tel IS NOT NULL
      AND (p_franja IS NULL OR invoke.franja_de(p_estudio, l.originada_en) = p_franja)
  )
  -- Contacto humano: contestó o fue un abandono (alguien atendió del otro lado).
  SELECT count(*)::int,
         count(*) FILTER (WHERE estado = 'habilita_encuesta' OR simbolo = 'AB')::int,
         CASE WHEN count(*) > 0
              THEN round(count(*) FILTER (WHERE estado = 'habilita_encuesta' OR simbolo = 'AB')::numeric / count(*), 4) END
  FROM l
$$;
