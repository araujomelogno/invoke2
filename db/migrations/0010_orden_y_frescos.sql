-- 0010 — Orden aleatorio reproducible e indexado (encargo de cierre H3) y mezcla
-- de casos frescos y reintentos sorteada en cada pedido (H1). Además, el pacer
-- graba todas las llamadas de encuesta (H2).

-- ───────────────────────── H3: semilla y orden determinista ─────────────────────────

-- La semilla se fija al cargar la guía y queda en el evento 'muestra_cargada': el
-- orden en que se trabajó la muestra se puede reconstruir y auditar.
ALTER TABLE invoke.guia ADD COLUMN semilla bigint;
UPDATE invoke.guia SET semilla = floor(random() * 4503599627370496)::bigint WHERE semilla IS NULL;
ALTER TABLE invoke.guia ALTER COLUMN semilla SET NOT NULL;

-- Fila del archivo de la que salió el caso (número de línea, el encabezado es la 1).
-- NULL para casos que no vienen de un archivo (fuera de marco creados desde el puesto).
ALTER TABLE invoke.caso ADD COLUMN posicion integer;

-- Número en [0, 1) que depende solo de (semilla, posición): la misma guía cargada
-- con la misma semilla da exactamente el mismo orden.
CREATE FUNCTION invoke.orden_determinista(p_semilla bigint, p_posicion bigint) RETURNS double precision
LANGUAGE sql IMMUTABLE STRICT AS $$
  SELECT ('x' || substr(md5(p_semilla::text || ':' || p_posicion::text), 1, 13))::bit(52)::bigint
         / 4503599627370496.0::double precision
$$;

ALTER TABLE invoke.caso ALTER COLUMN orden_aleatorio DROP DEFAULT;

CREATE FUNCTION invoke.tg_caso_orden() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  -- Sin posición (caso creado desde el puesto), se usa el id: sigue siendo determinista.
  NEW.orden_aleatorio := invoke.orden_determinista(
    (SELECT semilla FROM invoke.guia WHERE id = NEW.guia_id), COALESCE(NEW.posicion::bigint, -NEW.id));
  RETURN NEW;
END $$;
CREATE TRIGGER caso_orden BEFORE INSERT ON invoke.caso
  FOR EACH ROW EXECUTE FUNCTION invoke.tg_caso_orden();
ALTER FUNCTION invoke.tg_caso_orden() SECURITY DEFINER SET search_path = invoke, pg_temp;

-- El orden no se toca después de nacer el caso.
CREATE FUNCTION invoke.tg_caso_orden_fijo() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.orden_aleatorio IS DISTINCT FROM OLD.orden_aleatorio OR NEW.posicion IS DISTINCT FROM OLD.posicion THEN
    RAISE EXCEPTION 'el orden aleatorio y la posición de un caso no se modifican';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER caso_orden_fijo BEFORE UPDATE ON invoke.caso
  FOR EACH ROW EXECUTE FUNCTION invoke.tg_caso_orden_fijo();

-- Índice que cubre el escalón: dentro de una guía, el primer caso disponible en
-- orden aleatorio se lee del índice en lugar de ordenar el pool (ver EXPLAIN en
-- docs/decisiones.md, H3). Reemplaza al índice (estudio_id, guia_id), que es su prefijo.
DROP INDEX invoke.caso_estudio_idx;
CREATE INDEX caso_pool_idx ON invoke.caso (estudio_id, guia_id, orden_aleatorio);

-- ───────────────────────────── H1: frescos y reintentos ─────────────────────────────

ALTER TABLE invoke.estudio ADD COLUMN proporcion_frescos numeric(5,4) NOT NULL DEFAULT 0.5
  CHECK (proporcion_frescos BETWEEN 0 AND 1);
COMMENT ON COLUMN invoke.estudio.proporcion_frescos IS
  'Probabilidad de sortear un caso fresco (nunca intentado) en cada pedido al pool. No es un cupo acumulado.';

-- Cuotas de área y propiedad llenas, calculadas una vez por pedido.
CREATE FUNCTION invoke.areas_llenas(p_estudio bigint) RETURNS text[]
LANGUAGE sql STABLE AS $$
  SELECT COALESCE(array_agg(DISTINCT v), '{}')
  FROM invoke.cuota_llena l, unnest(l.valores) v
  WHERE l.estudio_id = p_estudio AND l.tipo = 'area'
$$;

CREATE FUNCTION invoke.propiedades_llenas(p_estudio bigint) RETURNS jsonb
LANGUAGE sql STABLE AS $$
  SELECT COALESCE(jsonb_agg(jsonb_build_object('propiedad', l.propiedad, 'valores', to_jsonb(l.valores))), '[]')
  FROM invoke.cuota_llena l
  WHERE l.estudio_id = p_estudio AND l.tipo = 'propiedad'
$$;

-- El pool común ahora dice además si cada caso es fresco. Fresco = ningún intento
-- que cuente (caso_estado.intentos = 0, que el trigger deriva del log).
DROP FUNCTION invoke.pool_elegible(bigint, timestamptz);
CREATE FUNCTION invoke.pool_elegible(p_estudio bigint, p_ahora timestamptz)
RETURNS TABLE (caso_id bigint, guia_id bigint, prioridad integer, orden double precision,
               abandonada_previa boolean, fresco boolean)
LANGUAGE sql STABLE AS $$
  SELECT ce.caso_id, c.guia_id, g.prioridad, c.orden_aleatorio, ce.abandonada_previa, ce.intentos = 0
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
    AND (c.area IS NULL OR c.area <> ALL ((SELECT invoke.areas_llenas(p_estudio))::text[]))
    AND NOT EXISTS (SELECT 1 FROM jsonb_to_recordset((SELECT invoke.propiedades_llenas(p_estudio))) q(propiedad text, valores text[])
                    WHERE (c.propiedades ->> q.propiedad) = ANY (q.valores))
$$;

-- Toma hasta p_cantidad casos de UNA población (frescos o reintentos), guía por
-- guía en orden de prioridad y, dentro de cada guía, en orden aleatorio leído del
-- índice caso_pool_idx. Bloquea con SKIP LOCKED: dos pedidos concurrentes nunca
-- toman el mismo caso.
CREATE FUNCTION invoke.tomar_del_pool(
  p_estudio bigint, p_ahora timestamptz, p_fresco boolean, p_cantidad integer,
  p_solo_abandonadas boolean DEFAULT false, p_excluir_abandonadas boolean DEFAULT false
) RETURNS SETOF bigint LANGUAGE plpgsql AS $$
DECLARE
  v_areas  text[] := invoke.areas_llenas(p_estudio);
  v_props  jsonb  := invoke.propiedades_llenas(p_estudio);
  v_n      integer := 0;
  g        record;
  r        record;
BEGIN
  FOR g IN SELECT id FROM invoke.guia WHERE estudio_id = p_estudio AND activa ORDER BY prioridad DESC, id LOOP
    FOR r IN
      SELECT ce.caso_id
      FROM invoke.caso c
      JOIN invoke.caso_estado ce ON ce.caso_id = c.id
      WHERE c.estudio_id = p_estudio AND c.guia_id = g.id
        AND ce.activo AND ce.asignado_a IS NULL
        AND (ce.estado = 'pendiente' OR (ce.estado = 'agendado' AND ce.agenda_hasta < p_ahora))
        AND ce.disponible_desde <= p_ahora
        AND (ce.intentos = 0) = p_fresco
        AND (NOT p_solo_abandonadas OR ce.abandonada_previa)
        AND (NOT p_excluir_abandonadas OR NOT ce.abandonada_previa)
        AND EXISTS (SELECT 1 FROM invoke.telefono t WHERE t.caso_id = c.id
                    AND NOT EXISTS (SELECT 1 FROM invoke.lista_negra n WHERE n.numero_normalizado = t.numero_normalizado))
        AND (c.area IS NULL OR c.area <> ALL (v_areas))
        AND NOT EXISTS (SELECT 1 FROM jsonb_to_recordset(v_props) q(propiedad text, valores text[])
                        WHERE (c.propiedades ->> q.propiedad) = ANY (q.valores))
      ORDER BY c.orden_aleatorio
      LIMIT p_cantidad - v_n
      FOR UPDATE OF ce SKIP LOCKED
    LOOP
      RETURN NEXT r.caso_id;
      v_n := v_n + 1;
    END LOOP;
    EXIT WHEN v_n >= p_cantidad;
  END LOOP;
END $$;

-- Un pedido al pool: sortea la población con probabilidad proporcion_frescos y,
-- si la sorteada está vacía, usa la otra. Nadie espera porque "su mitad" se agotó.
-- Devuelve el caso y la población de la que salió (para medir el reparto efectivo).
CREATE FUNCTION invoke.pedir_al_pool(
  p_estudio bigint, p_ahora timestamptz, p_solo_abandonadas boolean DEFAULT false,
  p_excluir_abandonadas boolean DEFAULT false, OUT caso_id bigint, OUT fresco boolean, OUT sorteo_fresco boolean
) LANGUAGE plpgsql AS $$
DECLARE
  v_prop numeric;
BEGIN
  SELECT proporcion_frescos INTO v_prop FROM invoke.estudio WHERE id = p_estudio;
  sorteo_fresco := random() < v_prop;
  fresco := sorteo_fresco;
  SELECT t INTO caso_id FROM invoke.tomar_del_pool(p_estudio, p_ahora, fresco, 1, p_solo_abandonadas, p_excluir_abandonadas) t;
  IF caso_id IS NULL THEN
    fresco := NOT sorteo_fresco;
    SELECT t INTO caso_id FROM invoke.tomar_del_pool(p_estudio, p_ahora, fresco, 1, p_solo_abandonadas, p_excluir_abandonadas) t;
  END IF;
  IF caso_id IS NULL THEN fresco := NULL; END IF;
END $$;

-- Asignador: igual que en 0006 salvo el paso 5, que ahora sortea la población.
CREATE OR REPLACE FUNCTION invoke.asignar_caso(p_participante bigint, p_estudio bigint)
RETURNS bigint LANGUAGE plpgsql AS $$
DECLARE
  est      invoke.estudio;
  pe       invoke.participante_estado;
  v_ahora  timestamptz := clock_timestamp();
  v_caso   bigint;
  v_paso   integer;
  v_pedido record;
BEGIN
  SELECT * INTO est FROM invoke.estudio WHERE id = p_estudio;
  IF est.estado <> 'activo' THEN
    RAISE EXCEPTION 'el estudio % no está activo', est.codigo;
  END IF;
  SELECT * INTO pe FROM invoke.participante_estado WHERE participante_id = p_participante;
  IF pe.estudio_id IS DISTINCT FROM p_estudio OR pe.estado = 'desconectado' THEN
    RAISE EXCEPTION 'el participante % no tiene sesión en el estudio', p_participante;
  END IF;

  SELECT ce.caso_id INTO v_caso FROM invoke.caso_estado ce WHERE ce.asignado_a = p_participante;
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
    IF v_caso IS NOT NULL THEN
      INSERT INTO invoke.evento (tipo, estudio_id, caso_id, participante_id, datos)
      VALUES ('caso_asignado', p_estudio, v_caso, p_participante,
              jsonb_build_object('paso', v_paso, 'modo', est.modo_discado));
      RETURN v_caso;
    END IF;
  END IF;

  -- Paso 5, pool común con mezcla de frescos y reintentos (H1). En power y
  -- predictivo el pool lo disca el pacer; acá solo llegan las abandonadas previas,
  -- que nunca vuelven en predictivo (diccionario).
  SELECT * INTO v_pedido FROM invoke.pedir_al_pool(p_estudio, v_ahora,
                                                   p_solo_abandonadas => est.modo_discado IN ('power', 'predictivo'));
  IF v_pedido.caso_id IS NULL THEN RETURN NULL; END IF;

  INSERT INTO invoke.evento (tipo, estudio_id, caso_id, participante_id, datos)
  VALUES ('caso_asignado', p_estudio, v_pedido.caso_id, p_participante,
          jsonb_build_object('paso', 5, 'modo', est.modo_discado,
                             'poblacion', CASE WHEN v_pedido.fresco THEN 'fresco' ELSE 'reintento' END,
                             'sorteo', CASE WHEN v_pedido.sorteo_fresco THEN 'fresco' ELSE 'reintento' END));
  RETURN v_pedido.caso_id;
END $$;

-- Pacer: cada llamada a originar es un pedido con su propio sorteo. Graba siempre (H2).
CREATE OR REPLACE FUNCTION invoke.reservar_casos_pacer(p_estudio bigint, p_cantidad integer)
RETURNS TABLE (llamada_id uuid, caso_id bigint, telefono_id bigint, numero text)
LANGUAGE plpgsql AS $$
DECLARE
  est      invoke.estudio;
  t        invoke.telefono;
  v_llam   uuid;
  v_pedido record;
BEGIN
  SELECT * INTO est FROM invoke.estudio WHERE id = p_estudio;
  IF est.estado <> 'activo' OR est.modo_discado NOT IN ('power', 'predictivo') OR p_cantidad <= 0 THEN
    RETURN;
  END IF;
  IF invoke.estudio_completo(p_estudio) THEN RETURN; END IF;
  FOR i IN 1..p_cantidad LOOP
    SELECT * INTO v_pedido FROM invoke.pedir_al_pool(p_estudio, clock_timestamp(), p_excluir_abandonadas => true);
    EXIT WHEN v_pedido.caso_id IS NULL;
    t := invoke.telefono_a_discar(v_pedido.caso_id);
    CONTINUE WHEN t.id IS NULL;
    v_llam := gen_random_uuid();
    INSERT INTO invoke.evento (tipo, estudio_id, caso_id, llamada_id, datos)
    VALUES ('llamada_originada', p_estudio, v_pedido.caso_id, v_llam,
            jsonb_build_object('telefono_id', t.id, 'numero', t.numero, 'modo', est.modo_discado,
                               'tipo', 'encuesta', 'grabar', true,
                               'poblacion', CASE WHEN v_pedido.fresco THEN 'fresco' ELSE 'reintento' END,
                               'sorteo', CASE WHEN v_pedido.sorteo_fresco THEN 'fresco' ELSE 'reintento' END));
    llamada_id := v_llam; caso_id := v_pedido.caso_id; telefono_id := t.id; numero := t.numero;
    RETURN NEXT;
  END LOOP;
END $$;

-- Reparto efectivo de frescos en un período (la consola muestra el del día).
-- Cuenta las entregas del pool: asignaciones del paso 5 y reservas del pacer.
CREATE FUNCTION invoke.reparto_frescos(p_estudio bigint, p_desde timestamptz)
RETURNS TABLE (frescos integer, reintentos integer, proporcion numeric, configurada numeric)
LANGUAGE sql STABLE AS $$
  WITH e AS (
    SELECT datos ->> 'poblacion' AS poblacion FROM invoke.evento
    WHERE estudio_id = p_estudio AND ocurrido_en >= p_desde AND datos ? 'poblacion'
      AND tipo IN ('caso_asignado', 'llamada_originada')
  )
  SELECT count(*) FILTER (WHERE poblacion = 'fresco')::int,
         count(*) FILTER (WHERE poblacion = 'reintento')::int,
         CASE WHEN count(*) > 0 THEN round(count(*) FILTER (WHERE poblacion = 'fresco')::numeric / count(*), 4) END,
         (SELECT proporcion_frescos FROM invoke.estudio WHERE id = p_estudio)
  FROM e
$$;

ALTER FUNCTION invoke.asignar_caso(bigint, bigint)          SECURITY DEFINER SET search_path = invoke, pg_temp;
ALTER FUNCTION invoke.reservar_casos_pacer(bigint, integer) SECURITY DEFINER SET search_path = invoke, pg_temp;
ALTER FUNCTION invoke.tomar_del_pool(bigint, timestamptz, boolean, integer, boolean, boolean)
  SECURITY DEFINER SET search_path = invoke, pg_temp;
ALTER FUNCTION invoke.pedir_al_pool(bigint, timestamptz, boolean, boolean)
  SECURITY DEFINER SET search_path = invoke, pg_temp;

GRANT EXECUTE ON FUNCTION invoke.pool_elegible(bigint, timestamptz), invoke.reparto_frescos(bigint, timestamptz),
  invoke.orden_determinista(bigint, bigint), invoke.areas_llenas(bigint), invoke.propiedades_llenas(bigint)
  TO invoke_app;
GRANT EXECUTE ON FUNCTION invoke.reparto_frescos(bigint, timestamptz) TO invoke_lectura;
