-- 0007 — Reportes sobre hechos (documento de fórmulas v1).
--
-- Todo se calcula desde invoke.evento, con la versión del diccionario del
-- estudio y con un instante de corte p_hasta: el mismo reporte pedido con el
-- mismo p_hasta da siempre el mismo número, aunque el estudio siga corriendo.

-- Resumen por llamada (intento). Tipo = 'encuesta' o 'administrativa'.
CREATE VIEW invoke.llamada AS
  SELECT o.llamada_id,
         o.estudio_id,
         o.caso_id,
         COALESCE(con.participante_id, o.participante_id) AS participante_id,
         (o.datos ->> 'telefono_id')::bigint AS telefono_id,
         o.datos ->> 'numero'               AS numero,
         o.datos ->> 'modo'                 AS modo,
         COALESCE(o.datos ->> 'tipo', 'encuesta') AS tipo,
         o.ocurrido_en                      AS originada_en,
         ate.ocurrido_en                    AS atendida_en,
         con.ocurrido_en                    AS conectada_en,
         fin.ocurrido_en                    AS finalizada_en,
         (fin.datos ->> 'causa')            AS causa_fin,
         amd.datos ->> 'resultado'          AS amd,
         (dt.datos ->> 'codigo')::smallint  AS disp_tel_original,
         CASE WHEN rc.id IS NOT NULL THEN 32::smallint ELSE (dt.datos ->> 'codigo')::smallint END AS disp_tel,
         rc.id IS NOT NULL                  AS reclasificada,
         des.id IS NOT NULL                 AS descartada,
         gra.datos ->> 'archivo'            AS grabacion,
         EXTRACT(epoch FROM (fin.ocurrido_en - con.ocurrido_en))::integer AS segundos_conectada,
         EXTRACT(epoch FROM (fin.ocurrido_en - o.ocurrido_en))::integer   AS segundos_total
  FROM invoke.evento o
  LEFT JOIN LATERAL (SELECT * FROM invoke.evento x WHERE x.llamada_id = o.llamada_id AND x.tipo = 'llamada_atendida'   ORDER BY id LIMIT 1) ate ON true
  LEFT JOIN LATERAL (SELECT * FROM invoke.evento x WHERE x.llamada_id = o.llamada_id AND x.tipo = 'llamada_conectada'  ORDER BY id LIMIT 1) con ON true
  LEFT JOIN LATERAL (SELECT * FROM invoke.evento x WHERE x.llamada_id = o.llamada_id AND x.tipo = 'llamada_finalizada' ORDER BY id LIMIT 1) fin ON true
  LEFT JOIN LATERAL (SELECT * FROM invoke.evento x WHERE x.llamada_id = o.llamada_id AND x.tipo = 'amd_resultado'      ORDER BY id LIMIT 1) amd ON true
  LEFT JOIN LATERAL (SELECT * FROM invoke.evento x WHERE x.llamada_id = o.llamada_id AND x.tipo = 'disposicion_telefono' LIMIT 1) dt ON true
  LEFT JOIN LATERAL (SELECT * FROM invoke.evento x WHERE x.llamada_id = o.llamada_id AND x.tipo = 'reclasificacion_amd' LIMIT 1) rc ON true
  LEFT JOIN LATERAL (SELECT * FROM invoke.evento x WHERE x.llamada_id = o.llamada_id AND x.tipo = 'llamada_descartada' LIMIT 1) des ON true
  LEFT JOIN LATERAL (SELECT * FROM invoke.evento x WHERE x.llamada_id = o.llamada_id AND x.tipo = 'grabacion' ORDER BY id DESC LIMIT 1) gra ON true
  WHERE o.tipo = 'llamada_originada';

-- Clasificación de cada caso del estudio en los grupos del documento de fórmulas.
--   p_corte 'parcial': los casos sin resolver quedan 'EP' (en proceso) y fuera de las tasas.
--   p_corte 'cierre' : todo caso sin resolver se resuelve (nunca se supo → UD).
-- Simbolos: C I R NC AB NE UD, más EP (en proceso) y SC (estado extendido sin clasificar).
CREATE FUNCTION invoke.clasificar_casos(p_estudio bigint, p_corte text, p_hasta timestamptz DEFAULT clock_timestamp())
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
    SELECT ev.llamada_id FROM ev WHERE ev.tipo = 'reclasificacion_amd'
  ),
  tel_resumen AS (
    SELECT t.caso_id,
           (array_agg(t.codigo ORDER BY t.id DESC))[1] AS ultima,
           count(*) FILTER (WHERE dt.cuenta_intento)::int AS intentos,
           bool_or(t.codigo = 30 OR rec.llamada_id IS NOT NULL) AS abandonada,
           bool_or(t.codigo = 4) AS contesto
    FROM tel t
    CROSS JOIN est
    JOIN invoke.disposicion_telefono dt ON dt.version_diccionario = est.version_diccionario AND dt.codigo = t.codigo
    LEFT JOIN rec ON rec.llamada_id = t.llamada_id
    GROUP BY t.caso_id
  ),
  cerrado AS (
    SELECT DISTINCT ev.caso_id FROM ev WHERE ev.tipo = 'caso_cerrado'
  )
  SELECT c.id, c.origen, c.estrato,
    CASE
      WHEN de.simbolo IS NOT NULL AND enc.codigo <> 5 THEN de.simbolo                       -- disposición final clasificada
      WHEN de.codigo IS NOT NULL AND de.simbolo IS NULL AND de.efecto_caso = 'cerrar' THEN 'SC'  -- extendido sin clasificar
      WHEN de.codigo IS NOT NULL AND de.efecto_caso = 'reabrir' AND p_corte = 'parcial' THEN 'EP'
      WHEN enc.codigo = 5 THEN                                                              -- Agendada sin cierre
        CASE WHEN p_corte = 'cierre' OR ce.caso_id IS NOT NULL THEN 'NC' ELSE 'EP' END
      WHEN tr.abandonada THEN
        CASE WHEN p_corte = 'cierre' OR ce.caso_id IS NOT NULL THEN 'AB' ELSE 'EP' END
      WHEN tr.ultima = 5 THEN 'UD'                                                         -- No volver a llamar
      WHEN tr.contesto THEN
        CASE WHEN p_corte = 'cierre' OR ce.caso_id IS NOT NULL THEN 'NC' ELSE 'EP' END
      WHEN ce.caso_id IS NOT NULL THEN 'UD'                                                -- tope alcanzado
      WHEN p_corte = 'cierre' THEN 'UD'
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

-- Conteos por símbolo sobre los casos del marco, y fuera de marco aparte.
CREATE FUNCTION invoke.conteo_tasas(p_estudio bigint, p_corte text, p_hasta timestamptz DEFAULT clock_timestamp())
RETURNS jsonb LANGUAGE sql STABLE AS $$
  WITH k AS (SELECT * FROM invoke.clasificar_casos(p_estudio, p_corte, p_hasta))
  SELECT jsonb_build_object(
    'marco', (SELECT jsonb_object_agg(s, n) FROM (
                SELECT s, count(k.caso_id) FILTER (WHERE k.origen = 'marco') AS n
                FROM unnest(ARRAY['C','I','R','NC','AB','NE','UD','EP','SC']) s
                LEFT JOIN k ON k.simbolo = s GROUP BY s) x),
    'fuera_marco', (SELECT COALESCE(jsonb_object_agg(origen, n), '{}') FROM (
                SELECT origen, jsonb_build_object('casos', count(*), 'completas', count(*) FILTER (WHERE simbolo = 'C')) AS n
                FROM k WHERE origen <> 'marco' GROUP BY origen) y),
    'tamano_marco', (SELECT count(*) FROM k WHERE origen = 'marco'),
    'casos_trabajados', (SELECT count(*) FROM k WHERE origen = 'marco' AND intentos > 0),
    'disposiciones_finales', (SELECT COALESCE(jsonb_object_agg(cod, n), '{}') FROM (
                SELECT COALESCE(ultima_disp_enc::text, 'tel_' || ultima_disp_tel::text, 'sin_intentos') AS cod, count(*) AS n
                FROM k WHERE origen = 'marco' GROUP BY 1) z),
    'corte', p_corte,
    'hasta', p_hasta,
    'version_diccionario', (SELECT version_diccionario FROM invoke.estudio WHERE id = p_estudio),
    'version_formulas', (SELECT version_formulas FROM invoke.estudio WHERE id = p_estudio)
  )
$$;

-- Tasa de abandono sobre llamadas en una ventana (la única tasa sobre llamadas).
--   TAB_reportada = AB / (AB + conectadas)
--   TAB_real      = (AB + FP_amd) / (AB + conectadas + FP_amd)
CREATE FUNCTION invoke.tasa_abandono(p_estudio bigint, p_desde timestamptz, p_hasta timestamptz)
RETURNS TABLE (abandonadas integer, conectadas integer, fp_amd integer, amd_maquina integer,
               tab_reportada numeric, tab_real numeric)
LANGUAGE sql STABLE AS $$
  WITH l AS (
    SELECT * FROM invoke.llamada
    WHERE estudio_id = p_estudio AND tipo = 'encuesta' AND originada_en >= p_desde AND originada_en < p_hasta
  ), n AS (
    SELECT count(*) FILTER (WHERE disp_tel_original = 30)::int AS ab,
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

-- Tasa de falsos positivos del AMD medida por escucha: 32 / 31 revisadas.
CREATE VIEW invoke.calibracion_amd AS
  SELECT estudio_id,
         count(*) FILTER (WHERE disp_tel_original = 31) AS detectadas,
         count(*) FILTER (WHERE reclasificada) AS falsos_positivos
  FROM invoke.llamada GROUP BY estudio_id;

-- Sesiones del participante (horas de sesión salen del registro de sesión, no del CDR).
CREATE VIEW invoke.sesion AS
  SELECT i.participante_id, i.estudio_id, i.datos ->> 'sesion_id' AS sesion_id,
         i.ocurrido_en AS inicio,
         COALESCE(f.ocurrido_en,
                  -- sesión sin cierre explícito: se corta en el último evento del participante
                  (SELECT max(x.ocurrido_en) FROM invoke.evento x
                    WHERE x.participante_id = i.participante_id AND x.id > i.id
                      AND x.ocurrido_en < COALESCE(
                        (SELECT min(y.ocurrido_en) FROM invoke.evento y
                          WHERE y.participante_id = i.participante_id AND y.tipo = 'sesion_iniciada' AND y.id > i.id),
                        'infinity')),
                  i.ocurrido_en) AS fin,
         f.id IS NULL AS sin_cierre
  FROM invoke.evento i
  LEFT JOIN LATERAL (
    SELECT * FROM invoke.evento f WHERE f.participante_id = i.participante_id AND f.tipo = 'sesion_finalizada'
      AND f.datos ->> 'sesion_id' = i.datos ->> 'sesion_id' ORDER BY id LIMIT 1) f ON true
  WHERE i.tipo = 'sesion_iniciada';

-- Productividad por participante en un período:
--   completas_por_hora = C / horas_de_sesion ;  ocupacion = tiempo_en_llamada / horas_de_sesion
--   cooperacion        = C / (C + I + R) sobre los casos que ese participante cerró
CREATE FUNCTION invoke.productividad(p_estudio bigint, p_desde timestamptz, p_hasta timestamptz)
RETURNS TABLE (participante_id bigint, nombre text, horas_sesion numeric, horas_en_llamada numeric,
               intentos integer, contactos integer, completas integer, interrumpidas integer, rechazos integer,
               agendadas integer, fuera_marco_creados integer)
LANGUAGE sql STABLE AS $$
  WITH s AS (
    SELECT participante_id,
           sum(EXTRACT(epoch FROM (least(fin, p_hasta) - greatest(inicio, p_desde)))) / 3600.0 AS horas
    FROM invoke.sesion
    WHERE estudio_id = p_estudio AND fin > p_desde AND inicio < p_hasta
    GROUP BY participante_id
  ), l AS (
    SELECT participante_id,
           sum(COALESCE(segundos_conectada, 0)) / 3600.0 AS horas_llamada,
           count(*) FILTER (WHERE disp_tel IS NOT NULL AND tipo = 'encuesta') AS intentos,
           count(*) FILTER (WHERE disp_tel = 4) AS contactos
    FROM invoke.llamada
    WHERE estudio_id = p_estudio AND originada_en >= p_desde AND originada_en < p_hasta
      AND participante_id IS NOT NULL
    GROUP BY participante_id
  ), d AS (
    SELECT participante_id,
           count(*) FILTER (WHERE (datos ->> 'codigo')::int = 3) AS c,
           count(*) FILTER (WHERE (datos ->> 'codigo')::int = 7) AS i,
           count(*) FILTER (WHERE (datos ->> 'codigo')::int = 4) AS r,
           count(*) FILTER (WHERE (datos ->> 'codigo')::int = 5) AS ag
    FROM invoke.evento
    WHERE estudio_id = p_estudio AND tipo = 'disposicion_encuesta'
      AND COALESCE(datos ->> 'origen', 'encuestador') IN ('encuestador', 'sistema')
      AND ocurrido_en >= p_desde AND ocurrido_en < p_hasta
    GROUP BY participante_id
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

-- Tasa de contacto observada por franja: insumo del N del power y del predictivo.
CREATE FUNCTION invoke.tasa_contacto_observada(p_estudio bigint, p_franja text, p_desde timestamptz)
RETURNS TABLE (intentos integer, contactos_humanos integer, tasa numeric)
LANGUAGE sql STABLE AS $$
  WITH l AS (
    SELECT * FROM invoke.llamada
    WHERE estudio_id = p_estudio AND tipo = 'encuesta' AND originada_en >= p_desde
      AND NOT descartada AND disp_tel IS NOT NULL
      AND (p_franja IS NULL OR invoke.franja_de(p_estudio, originada_en) = p_franja)
  )
  SELECT count(*)::int,
         count(*) FILTER (WHERE disp_tel IN (4, 30, 32))::int,
         CASE WHEN count(*) > 0 THEN round(count(*) FILTER (WHERE disp_tel IN (4, 30, 32))::numeric / count(*), 4) END
  FROM l
$$;

GRANT SELECT ON invoke.llamada, invoke.sesion, invoke.calibracion_amd TO invoke_app, invoke_lectura;
GRANT EXECUTE ON FUNCTION invoke.clasificar_casos(bigint, text, timestamptz), invoke.conteo_tasas(bigint, text, timestamptz),
  invoke.tasa_abandono(bigint, timestamptz, timestamptz), invoke.productividad(bigint, timestamptz, timestamptz),
  invoke.tasa_contacto_observada(bigint, text, timestamptz), invoke.franja_de(bigint, timestamptz)
  TO invoke_app, invoke_lectura;
