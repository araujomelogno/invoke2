-- 0006 — Cuotas (sin contadores), asignador con la cascada del legacy, reserva
-- para el pacer y casos fuera de marco (ADR-008).

-- Última disposición de encuesta por caso: una consulta, no una columna.
CREATE VIEW invoke.caso_ultima_disp_enc AS
  SELECT DISTINCT ON (caso_id) caso_id, (datos ->> 'codigo')::smallint AS codigo,
         datos ->> 'origen' AS origen, participante_id, ocurrido_en, id AS evento_id
  FROM invoke.evento
  WHERE tipo = 'disposicion_encuesta'
  ORDER BY caso_id, id DESC;

-- Casos completos (incluye promovidas y casos fuera de marco: estos cuentan
-- para cuota, decisión 7 del diccionario).
CREATE VIEW invoke.caso_completo AS
  SELECT c.*
  FROM invoke.caso c
  JOIN invoke.caso_ultima_disp_enc u ON u.caso_id = c.id AND u.codigo = 3;

-- ¿El caso cae dentro de la cuota? Las respuestas múltiples son arreglos JSON.
CREATE FUNCTION invoke.caso_en_cuota(q invoke.cuota, c invoke.caso) RETURNS boolean
LANGUAGE sql STABLE AS $$
  SELECT CASE q.tipo
    WHEN 'area'      THEN c.area = ANY (q.valores)
    WHEN 'propiedad' THEN (c.propiedades ->> q.propiedad) = ANY (q.valores)
    WHEN 'pregunta'  THEN EXISTS (
      SELECT 1 FROM invoke.respuesta_vigente r
      WHERE r.caso_id = c.id AND r.variable = q.pregunta
        AND (CASE jsonb_typeof(r.valor)
               WHEN 'array' THEN EXISTS (SELECT 1 FROM jsonb_array_elements_text(r.valor) v WHERE v = ANY (q.valores))
               ELSE (r.valor #>> '{}') = ANY (q.valores)
             END))
  END
$$;

CREATE VIEW invoke.cuota_avance AS
  SELECT q.id AS cuota_id, q.estudio_id, q.nombre, q.tipo, q.pregunta, q.propiedad, q.valores, q.activa,
         COALESCE(q.objetivo, round(e.objetivo_completas * q.porcentaje / 100)::int) AS objetivo,
         (SELECT count(*) FROM invoke.caso c JOIN invoke.caso_ultima_disp_enc u ON u.caso_id = c.id AND u.codigo = 3
           WHERE c.estudio_id = q.estudio_id AND invoke.caso_en_cuota(q, c))::int AS logradas,
         (SELECT count(*) FROM invoke.caso c JOIN invoke.caso_ultima_disp_enc u ON u.caso_id = c.id AND u.codigo = 3
           WHERE c.estudio_id = q.estudio_id AND c.origen <> 'marco'
             AND invoke.caso_en_cuota(q, c))::int AS logradas_fuera_marco
  FROM invoke.cuota q JOIN invoke.estudio e ON e.id = q.estudio_id;

CREATE VIEW invoke.cuota_llena AS
  SELECT * FROM invoke.cuota_avance
  WHERE activa AND objetivo IS NOT NULL AND logradas >= objetivo;

-- Evaluación en vivo durante la entrevista: recibe las respuestas en curso
-- (todavía no completas) y devuelve las cuotas de pregunta ya llenas en las que
-- el caso caería. La estación la consulta en cada pantalla, no una sola vez.
CREATE FUNCTION invoke.evaluar_cuotas(p_caso bigint, p_respuestas jsonb) RETURNS TABLE (cuota_id bigint, nombre text)
LANGUAGE sql STABLE AS $$
  SELECT l.cuota_id, l.nombre
  FROM invoke.cuota_llena l
  JOIN invoke.caso c ON c.id = p_caso AND c.estudio_id = l.estudio_id
  WHERE CASE l.tipo
    WHEN 'area'      THEN c.area = ANY (l.valores)
    WHEN 'propiedad' THEN (c.propiedades ->> l.propiedad) = ANY (l.valores)
    WHEN 'pregunta'  THEN p_respuestas ? l.pregunta AND (
      CASE jsonb_typeof(p_respuestas -> l.pregunta)
        WHEN 'array' THEN EXISTS (SELECT 1 FROM jsonb_array_elements_text(p_respuestas -> l.pregunta) v
                                  WHERE v = ANY (l.valores))
        ELSE (p_respuestas -> l.pregunta #>> '{}') = ANY (l.valores)
      END)
  END
$$;

-- Teléfono a discar para un caso: el no bloqueado con menos intentos.
CREATE FUNCTION invoke.telefono_a_discar(p_caso bigint) RETURNS invoke.telefono
LANGUAGE sql STABLE AS $$
  SELECT t.*
  FROM invoke.telefono t
  WHERE t.caso_id = p_caso
    AND NOT EXISTS (SELECT 1 FROM invoke.lista_negra n WHERE n.numero_normalizado = t.numero_normalizado)
  ORDER BY (SELECT count(*) FROM invoke.evento e
            WHERE e.caso_id = p_caso AND e.tipo = 'llamada_originada'
              AND (e.datos ->> 'telefono_id')::bigint = t.id), t.orden, t.id
  LIMIT 1
$$;

-- Casos elegibles del pool común (paso 5 de la cascada). Sirve al asignador y al pacer.
CREATE FUNCTION invoke.pool_elegible(p_estudio bigint, p_ahora timestamptz)
RETURNS TABLE (caso_id bigint, prioridad integer, orden double precision, abandonada_previa boolean)
LANGUAGE sql STABLE AS $$
  WITH llenas AS (SELECT * FROM invoke.cuota_llena WHERE estudio_id = p_estudio AND tipo IN ('area', 'propiedad'))
  SELECT ce.caso_id, g.prioridad, c.orden_aleatorio, ce.abandonada_previa
  FROM invoke.caso_estado ce
  JOIN invoke.caso c ON c.id = ce.caso_id
  JOIN invoke.guia g ON g.id = ce.guia_id AND g.activa
  WHERE ce.estudio_id = p_estudio
    AND ce.activo
    AND ce.asignado_a IS NULL
    AND (ce.estado = 'pendiente' OR (ce.estado = 'agendado' AND ce.agenda_hasta < p_ahora))
    AND ce.disponible_desde <= p_ahora
    AND EXISTS (SELECT 1 FROM invoke.telefono t WHERE t.caso_id = c.id
                AND NOT EXISTS (SELECT 1 FROM invoke.lista_negra n WHERE n.numero_normalizado = t.numero_normalizado))
    AND NOT EXISTS (SELECT 1 FROM llenas l WHERE
          (l.tipo = 'area' AND c.area = ANY (l.valores))
       OR (l.tipo = 'propiedad' AND (c.propiedades ->> l.propiedad) = ANY (l.valores)))
$$;

-- ¿El estudio ya alcanzó su objetivo? (legacy StudyCompletedException)
CREATE FUNCTION invoke.estudio_completo(p_estudio bigint) RETURNS boolean
LANGUAGE sql STABLE AS $$
  SELECT e.objetivo_completas IS NOT NULL
     AND (SELECT count(*) FROM invoke.caso_completo c WHERE c.estudio_id = e.id) >= e.objetivo_completas
  FROM invoke.estudio e WHERE e.id = p_estudio
$$;

-- Asignador para manual y preview, y para los casos que en power y predictivo
-- van igual en preview (agendados y casos con una abandonada previa).
--
-- Cascada heredada del FIFOScheduler legacy (salvo que el estudio ignore agendados):
--   1. agendado para este participante, ventana estrecha (±30 min)
--   2. agendado sin participante, ventana estrecha
--   3. agendado para este participante, cualquier ventana abierta
--   4. agendado sin participante, cualquier ventana abierta
--   5. pool común: guía de mayor prioridad, después orden aleatorio estable
-- Exclusividad: SELECT ... FOR UPDATE SKIP LOCKED más el índice único de
-- caso_estado.asignado_a. Devuelve NULL si no hay caso.
CREATE FUNCTION invoke.asignar_caso(p_participante bigint, p_estudio bigint)
RETURNS bigint LANGUAGE plpgsql AS $$
DECLARE
  est      invoke.estudio;
  pe       invoke.participante_estado;
  v_ahora  timestamptz := clock_timestamp();
  v_caso   bigint;
  v_paso   integer;
BEGIN
  SELECT * INTO est FROM invoke.estudio WHERE id = p_estudio;
  IF est.estado <> 'activo' THEN
    RAISE EXCEPTION 'el estudio % no está activo', est.codigo;
  END IF;
  SELECT * INTO pe FROM invoke.participante_estado WHERE participante_id = p_participante;
  IF pe.estudio_id IS DISTINCT FROM p_estudio OR pe.estado = 'desconectado' THEN
    RAISE EXCEPTION 'el participante % no tiene sesión en el estudio', p_participante;
  END IF;

  -- Idempotente: si ya tiene un caso, es ese.
  SELECT caso_id INTO v_caso FROM invoke.caso_estado WHERE asignado_a = p_participante;
  IF FOUND THEN RETURN v_caso; END IF;

  IF invoke.estudio_completo(p_estudio) THEN RETURN NULL; END IF;

  IF NOT est.ignorar_agendados THEN
    SELECT ce.caso_id,
           CASE
             WHEN ce.agenda_participante = p_participante AND ce.agenda_desde BETWEEN v_ahora - interval '30 min' AND v_ahora
                  AND ce.agenda_hasta BETWEEN v_ahora AND v_ahora + interval '30 min' THEN 1
             WHEN ce.agenda_participante IS NULL AND ce.agenda_desde BETWEEN v_ahora - interval '30 min' AND v_ahora
                  AND ce.agenda_hasta BETWEEN v_ahora AND v_ahora + interval '30 min' THEN 2
             WHEN ce.agenda_participante = p_participante THEN 3
             ELSE 4
           END
      INTO v_caso, v_paso
    FROM invoke.caso_estado ce
    JOIN invoke.guia g ON g.id = ce.guia_id AND g.activa
    WHERE ce.estudio_id = p_estudio AND ce.estado = 'agendado' AND ce.activo AND ce.asignado_a IS NULL
      AND ce.agenda_desde <= v_ahora AND ce.agenda_hasta >= v_ahora
      AND ce.disponible_desde <= v_ahora
      AND (ce.agenda_participante IS NULL OR ce.agenda_participante = p_participante)
    ORDER BY 2, ce.agenda_desde
    LIMIT 1
    FOR UPDATE OF ce SKIP LOCKED;
  END IF;

  IF v_caso IS NULL THEN
    v_paso := 5;
    SELECT p.caso_id INTO v_caso
    FROM invoke.pool_elegible(p_estudio, v_ahora) p
    JOIN invoke.caso_estado ce ON ce.caso_id = p.caso_id
    -- En power y predictivo el pool lo disca el pacer; acá solo llegan las
    -- abandonadas previas, que nunca vuelven en predictivo (diccionario).
    WHERE est.modo_discado IN ('manual', 'preview') OR p.abandonada_previa
    ORDER BY p.abandonada_previa DESC, p.prioridad DESC, p.orden
    LIMIT 1
    FOR UPDATE OF ce SKIP LOCKED;
  END IF;

  IF v_caso IS NULL THEN RETURN NULL; END IF;

  INSERT INTO invoke.evento (tipo, estudio_id, caso_id, participante_id, datos)
  VALUES ('caso_asignado', p_estudio, v_caso, p_participante,
          jsonb_build_object('paso', v_paso, 'modo', est.modo_discado));
  RETURN v_caso;
END $$;

-- Reserva para el pacer (power y predictivo): toma hasta p_cantidad casos del
-- pool, elige el teléfono y registra 'llamada_originada' sin participante. El
-- caso se asigna recién cuando alguien atiende (inversión del orden, Fase 2).
CREATE FUNCTION invoke.reservar_casos_pacer(p_estudio bigint, p_cantidad integer)
RETURNS TABLE (llamada_id uuid, caso_id bigint, telefono_id bigint, numero text)
LANGUAGE plpgsql AS $$
DECLARE
  est    invoke.estudio;
  r      record;
  t      invoke.telefono;
  v_llam uuid;
  v_grab boolean;
BEGIN
  SELECT * INTO est FROM invoke.estudio WHERE id = p_estudio;
  IF est.estado <> 'activo' OR est.modo_discado NOT IN ('power', 'predictivo') OR p_cantidad <= 0 THEN
    RETURN;
  END IF;
  IF invoke.estudio_completo(p_estudio) THEN RETURN; END IF;
  FOR r IN
    SELECT p.caso_id
    FROM invoke.pool_elegible(p_estudio, clock_timestamp()) p
    JOIN invoke.caso_estado ce ON ce.caso_id = p.caso_id
    WHERE NOT p.abandonada_previa
    ORDER BY p.prioridad DESC, p.orden
    LIMIT p_cantidad
    FOR UPDATE OF ce SKIP LOCKED
  LOOP
    t := invoke.telefono_a_discar(r.caso_id);
    CONTINUE WHEN t.id IS NULL;
    v_llam := gen_random_uuid();
    v_grab := random() < est.muestreo_grabacion;
    INSERT INTO invoke.evento (tipo, estudio_id, caso_id, llamada_id, datos)
    VALUES ('llamada_originada', p_estudio, r.caso_id, v_llam,
            jsonb_build_object('telefono_id', t.id, 'numero', t.numero, 'modo', est.modo_discado,
                               'tipo', 'encuesta', 'grabar', v_grab));
    llamada_id := v_llam; caso_id := r.caso_id; telefono_id := t.id; numero := t.numero;
    RETURN NEXT;
  END LOOP;
END $$;

-- Cuando atiende un humano en power/predictivo: toma un participante disponible
-- del estudio (el que más tiempo lleva esperando) y le asigna el caso. NULL si no
-- hay ninguno: la llamada es una abandonada.
CREATE FUNCTION invoke.tomar_participante_para(p_llamada uuid)
RETURNS bigint LANGUAGE plpgsql AS $$
DECLARE
  o       invoke.evento;
  v_part  bigint;
BEGIN
  SELECT * INTO o FROM invoke.evento WHERE llamada_id = p_llamada AND tipo = 'llamada_originada' ORDER BY id LIMIT 1;
  SELECT pe.participante_id INTO v_part
  FROM invoke.participante_estado pe
  WHERE pe.estudio_id = o.estudio_id AND pe.estado = 'disponible' AND pe.canal_audio IS NOT NULL
    AND pe.caso_id IS NULL
  ORDER BY pe.desde
  LIMIT 1
  FOR UPDATE SKIP LOCKED;
  IF v_part IS NULL THEN RETURN NULL; END IF;
  INSERT INTO invoke.evento (tipo, estudio_id, caso_id, llamada_id, participante_id, datos)
  VALUES ('llamada_conectada', o.estudio_id, o.caso_id, p_llamada, v_part, '{}');
  RETURN v_part;
END $$;

-- ─────────────────────────── Fuera de marco ───────────────────────────

-- Antes de discar un número a mano: ¿dónde cae? (política, "El flujo de clasificación")
CREATE FUNCTION invoke.clasificar_numero(p_estudio bigint, p_numero text, p_caso_actual bigint)
RETURNS jsonb LANGUAGE plpgsql STABLE AS $$
DECLARE
  v_norm  text := invoke.normalizar_telefono(p_numero);
  v_caso  bigint;
  est     invoke.estudio;
BEGIN
  SELECT * INTO est FROM invoke.estudio WHERE id = p_estudio;
  IF EXISTS (SELECT 1 FROM invoke.lista_negra WHERE numero_normalizado = v_norm) THEN
    RETURN jsonb_build_object('situacion', 'lista_negra');
  END IF;
  IF p_caso_actual IS NOT NULL AND EXISTS (
       SELECT 1 FROM invoke.telefono WHERE caso_id = p_caso_actual AND numero_normalizado = v_norm) THEN
    RETURN jsonb_build_object('situacion', 'en_caso_actual', 'caso_id', p_caso_actual);
  END IF;
  SELECT caso_id INTO v_caso FROM invoke.telefono
  WHERE estudio_id = p_estudio AND numero_normalizado = v_norm ORDER BY caso_id LIMIT 1;
  IF v_caso IS NOT NULL THEN
    -- Caso límite: el "derivado" ya estaba en la muestra. Es el tipo 1, no el 2.
    RETURN jsonb_build_object('situacion', 'en_otro_caso', 'caso_id', v_caso);
  END IF;
  RETURN jsonb_build_object(
    'situacion', 'desconocido',
    'puede_mismo_caso', p_caso_actual IS NOT NULL,
    'puede_derivado', est.fuera_marco_habilitado,
    'motivo_derivado', CASE WHEN NOT est.fuera_marco_habilitado
      THEN 'Este estudio no admite casos fuera de marco: su muestra es cerrada.' END);
END $$;

CREATE FUNCTION invoke.agregar_telefono(p_caso bigint, p_numero text, p_autor text)
RETURNS bigint LANGUAGE plpgsql AS $$
DECLARE
  c     invoke.caso;
  v_tel bigint;
BEGIN
  SELECT * INTO c FROM invoke.caso WHERE id = p_caso;
  INSERT INTO invoke.telefono (caso_id, estudio_id, numero, orden, agregado_por)
  VALUES (p_caso, c.estudio_id, p_numero,
          (SELECT COALESCE(max(orden), 0) + 1 FROM invoke.telefono WHERE caso_id = p_caso), p_autor)
  RETURNING id INTO v_tel;
  INSERT INTO invoke.evento (tipo, estudio_id, caso_id, autor, datos)
  VALUES ('telefono_agregado', c.estudio_id, p_caso, p_autor,
          jsonb_build_object('telefono_id', v_tel, 'numero', p_numero));
  RETURN v_tel;
END $$;

-- Crea un caso fuera de marco. Hereda estrato, área y propiedades del caso
-- origen como valor inicial editable (política, decisión 5).
CREATE FUNCTION invoke.crear_caso_fuera_de_marco(
  p_caso_origen bigint, p_numero text, p_origen text, p_autor text, p_motivo text,
  p_estrato text DEFAULT NULL, p_propiedades jsonb DEFAULT NULL
) RETURNS bigint LANGUAGE plpgsql AS $$
DECLARE
  o        invoke.caso;
  est      invoke.estudio;
  g        invoke.guia;
  v_total  integer;
  v_fuera  integer;
  v_caso   bigint;
  v_tel    bigint;
BEGIN
  IF p_origen NOT IN ('derivado', 'reemplazo_manual') THEN
    RAISE EXCEPTION 'desde el puesto solo se crean casos derivado o reemplazo_manual';
  END IF;
  SELECT * INTO o FROM invoke.caso WHERE id = p_caso_origen;
  SELECT * INTO est FROM invoke.estudio WHERE id = o.estudio_id;
  IF NOT est.fuera_marco_habilitado THEN
    RAISE EXCEPTION 'el estudio % no admite casos fuera de marco', est.codigo;
  END IF;
  SELECT * INTO g FROM invoke.guia WHERE id = o.guia_id FOR UPDATE;
  IF g.tope_fuera_marco IS NOT NULL THEN
    SELECT count(*), count(*) FILTER (WHERE origen <> 'marco') INTO v_total, v_fuera
    FROM invoke.caso WHERE guia_id = g.id;
    IF (v_fuera + 1)::numeric / (v_total + 1) > g.tope_fuera_marco THEN
      RAISE EXCEPTION 'la guía % alcanzó su tope de casos fuera de marco (% por ciento)', g.nombre, g.tope_fuera_marco * 100;
    END IF;
  END IF;
  IF EXISTS (SELECT 1 FROM invoke.telefono WHERE estudio_id = o.estudio_id
             AND numero_normalizado = invoke.normalizar_telefono(p_numero)) THEN
    RAISE EXCEPTION 'el número ya pertenece a un caso del estudio: no se duplica';
  END IF;
  INSERT INTO invoke.caso (estudio_id, guia_id, origen, caso_origen, usuario_creador, motivo,
                           propiedades, estrato, area)
  VALUES (o.estudio_id, o.guia_id, p_origen, o.id, p_autor, p_motivo,
          COALESCE(p_propiedades, o.propiedades), COALESCE(p_estrato, o.estrato), o.area)
  RETURNING id INTO v_caso;
  INSERT INTO invoke.telefono (caso_id, estudio_id, numero, agregado_por)
  VALUES (v_caso, o.estudio_id, p_numero, p_autor) RETURNING id INTO v_tel;
  INSERT INTO invoke.evento (tipo, estudio_id, caso_id, autor, datos)
  VALUES ('caso_creado', o.estudio_id, v_caso, p_autor,
          jsonb_build_object('origen', p_origen, 'caso_origen', o.id, 'motivo', p_motivo, 'telefono_id', v_tel));
  RETURN v_caso;
END $$;

-- Alerta de composición derivada por estrato (umbral propiedad del estudio).
CREATE VIEW invoke.alerta_derivados AS
  SELECT c.estudio_id, c.estrato,
         count(*) AS completas,
         count(*) FILTER (WHERE c.origen <> 'marco') AS completas_fuera_marco,
         round(count(*) FILTER (WHERE c.origen <> 'marco')::numeric / count(*), 4) AS proporcion,
         e.umbral_alerta_derivados AS umbral,
         e.umbral_alerta_derivados IS NOT NULL
           AND count(*) FILTER (WHERE c.origen <> 'marco')::numeric / count(*) > e.umbral_alerta_derivados AS en_alerta
  FROM invoke.caso_completo c JOIN invoke.estudio e ON e.id = c.estudio_id
  GROUP BY c.estudio_id, c.estrato, e.umbral_alerta_derivados;

ALTER FUNCTION invoke.asignar_caso(bigint, bigint)           SECURITY DEFINER SET search_path = invoke, pg_temp;
ALTER FUNCTION invoke.reservar_casos_pacer(bigint, integer)  SECURITY DEFINER SET search_path = invoke, pg_temp;
ALTER FUNCTION invoke.tomar_participante_para(uuid)          SECURITY DEFINER SET search_path = invoke, pg_temp;

GRANT SELECT ON invoke.caso_ultima_disp_enc, invoke.caso_completo, invoke.cuota_avance, invoke.cuota_llena,
  invoke.alerta_derivados TO invoke_app, invoke_lectura;
GRANT EXECUTE ON FUNCTION invoke.caso_en_cuota(invoke.cuota, invoke.caso), invoke.evaluar_cuotas(bigint, jsonb),
  invoke.telefono_a_discar(bigint), invoke.pool_elegible(bigint, timestamptz), invoke.estudio_completo(bigint),
  invoke.asignar_caso(bigint, bigint), invoke.reservar_casos_pacer(bigint, integer),
  invoke.tomar_participante_para(uuid), invoke.clasificar_numero(bigint, text, bigint),
  invoke.agregar_telefono(bigint, text, text),
  invoke.crear_caso_fuera_de_marco(bigint, text, text, text, text, text, jsonb) TO invoke_app;
GRANT EXECUTE ON FUNCTION invoke.caso_en_cuota(invoke.cuota, invoke.caso) TO invoke_lectura;
