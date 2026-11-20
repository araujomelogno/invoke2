-- 0012 — Un caso pendiente de clasificar no cruza de estudio (hallado en el PBX de
-- laboratorio, scripts/probar_pbx.py). Si el encuestador cambia de estudio con una
-- llamada sin clasificar, el asignador lo dice en lugar de devolver ese caso en la
-- sesión nueva. Igual que 0010 salvo el bloque de idempotencia.

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

  -- Idempotente dentro del estudio. Un caso pendiente de otro estudio no se
  -- devuelve ni se mezcla: hay que clasificarlo antes (verificado en el PBX de
  -- laboratorio: un caso de power sin clasificar aparecía en la sesión siguiente).
  SELECT ce.caso_id, ce.estudio_id INTO v_caso, v_paso FROM invoke.caso_estado ce WHERE ce.asignado_a = p_participante;
  IF FOUND THEN
    IF v_paso <> p_estudio THEN
      RAISE EXCEPTION 'tenés el caso % sin clasificar en otro estudio: clasificalo antes de pedir uno nuevo', v_caso;
    END IF;
    RETURN v_caso;
  END IF;

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

-- CREATE OR REPLACE restablece SECURITY INVOKER: se vuelve a declarar.
ALTER FUNCTION invoke.asignar_caso(bigint, bigint) SECURITY DEFINER SET search_path = invoke, pg_temp;
