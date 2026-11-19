-- 0004 — Log de eventos inmutable (ADR-002) y proyecciones operativas.
--
-- Todo hecho se escribe como evento. Ningún reporte lee contadores: todos son
-- consultas sobre invoke.evento. Las dos proyecciones de acá abajo
-- (caso_estado y participante_estado) existen solo para que el asignador y la
-- consola respondan rápido; las actualiza exclusivamente el trigger de eventos,
-- en la misma transacción, y se pueden reconstruir desde cero con
-- invoke.reconstruir_caso_estado(). Los reportes no las leen.

CREATE TABLE invoke.evento (
  id               bigserial PRIMARY KEY,
  tipo             text NOT NULL CHECK (tipo IN (
                     'muestra_cargada', 'caso_creado', 'telefono_agregado',
                     'caso_asignado', 'caso_liberado',
                     'llamada_originada', 'llamada_timbrando', 'llamada_atendida', 'amd_resultado',
                     'llamada_conectada', 'llamada_abandonada', 'llamada_descartada', 'llamada_finalizada',
                     'grabacion', 'disposicion_telefono', 'reclasificacion_amd',
                     'encuesta_iniciada', 'pantalla', 'disposicion_encuesta', 'reagendada',
                     'sesion_iniciada', 'estado_participante', 'sesion_finalizada',
                     'audio_conectado', 'audio_desconectado', 'prueba_audio',
                     'escucha_iniciada', 'escucha_finalizada',
                     'caso_cerrado', 'pacer_ajuste', 'estudio_modificado', 'cuota_excedida')),
  ocurrido_en      timestamptz NOT NULL DEFAULT clock_timestamp(),
  registrado_en    timestamptz NOT NULL DEFAULT clock_timestamp(),
  estudio_id       bigint REFERENCES invoke.estudio,
  caso_id          bigint REFERENCES invoke.caso,
  llamada_id       uuid,
  participante_id  bigint REFERENCES invoke.participante,
  autor            text,     -- uid de quien produjo el evento (supervisor, administrador, servicio)
  datos            jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(datos) = 'object'),
  CHECK (ocurrido_en <= registrado_en + interval '1 minute')
);

CREATE INDEX evento_caso_idx         ON invoke.evento (caso_id, id) WHERE caso_id IS NOT NULL;
CREATE INDEX evento_llamada_idx      ON invoke.evento (llamada_id, id) WHERE llamada_id IS NOT NULL;
CREATE INDEX evento_estudio_idx      ON invoke.evento (estudio_id, ocurrido_en);
CREATE INDEX evento_participante_idx ON invoke.evento (participante_id, ocurrido_en) WHERE participante_id IS NOT NULL;
CREATE INDEX evento_tipo_idx         ON invoke.evento (tipo, ocurrido_en);
-- Una sola disposición de teléfono por llamada: el intento tiene un único resultado.
CREATE UNIQUE INDEX evento_disp_tel_unica ON invoke.evento (llamada_id) WHERE tipo = 'disposicion_telefono';
-- Una sola reclasificación por llamada.
CREATE UNIQUE INDEX evento_reclasif_unica ON invoke.evento (llamada_id) WHERE tipo = 'reclasificacion_amd';

-- Inmutabilidad: ni UPDATE ni DELETE ni TRUNCATE, aunque alguien tenga el privilegio.
CREATE FUNCTION invoke.tg_evento_inmutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'invoke.evento es inmutable (ADR-002): % rechazado', TG_OP;
END $$;
CREATE TRIGGER evento_inmutable BEFORE UPDATE OR DELETE ON invoke.evento
  FOR EACH ROW EXECUTE FUNCTION invoke.tg_evento_inmutable();
CREATE TRIGGER evento_inmutable_truncate BEFORE TRUNCATE ON invoke.evento
  FOR EACH STATEMENT EXECUTE FUNCTION invoke.tg_evento_inmutable();

-- ───────────────────────── Proyección del caso ─────────────────────────

CREATE TABLE invoke.caso_estado (
  caso_id                  bigint PRIMARY KEY REFERENCES invoke.caso ON DELETE CASCADE,
  estudio_id               bigint NOT NULL,
  guia_id                  bigint NOT NULL,
  estado                   text   NOT NULL DEFAULT 'pendiente'
                           CHECK (estado IN ('pendiente', 'asignado', 'en_llamada', 'agendado', 'cerrado')),
  activo                   boolean NOT NULL DEFAULT true,     -- sustitutos: solo los activos se discan
  disponible_desde         timestamptz NOT NULL DEFAULT '2000-01-01 00:00+00',
  asignado_a               bigint REFERENCES invoke.participante,
  agenda_desde             timestamptz,
  agenda_hasta             timestamptz,
  agenda_participante      bigint REFERENCES invoke.participante,
  intentos                 integer NOT NULL DEFAULT 0,
  intentos_por_franja      jsonb   NOT NULL DEFAULT '{}',
  intentos_por_disposicion jsonb   NOT NULL DEFAULT '{}',
  ultima_franja            text,
  ultima_disp_tel          smallint,
  ultima_disp_enc          smallint,
  abandonada_previa        boolean NOT NULL DEFAULT false,
  cerrado_en               timestamptz,
  motivo_cierre            text,
  ultimo_evento            bigint,
  CHECK ((estado = 'cerrado') = (cerrado_en IS NOT NULL)),
  -- 'en_llamada' sin participante es válido: power/predictivo timbrando antes de que atiendan.
  CHECK (estado <> 'asignado' OR asignado_a IS NOT NULL),
  CHECK (estado <> 'agendado' OR agenda_desde IS NOT NULL)
);
-- Exclusividad de asignación: un participante tiene a lo sumo un caso.
CREATE UNIQUE INDEX caso_estado_un_caso_por_participante
  ON invoke.caso_estado (asignado_a) WHERE asignado_a IS NOT NULL;
CREATE INDEX caso_estado_cola_idx ON invoke.caso_estado (estudio_id, estado, disponible_desde)
  WHERE estado IN ('pendiente', 'agendado') AND activo;

-- Al crear un caso nace su proyección.
CREATE FUNCTION invoke.tg_caso_nace() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  v_activo boolean := true;
BEGIN
  IF NEW.sustituto IS NOT NULL THEN
    -- Activo solo el primer grupo de su ranura (legacy: grupo mínimo).
    v_activo := NOT EXISTS (
      SELECT 1 FROM invoke.caso c
      WHERE c.guia_id = NEW.guia_id AND c.sustituto = NEW.sustituto
        AND c.grupo_sustituto < NEW.grupo_sustituto AND c.id <> NEW.id);
    IF v_activo THEN
      -- Si llega un grupo menor que los ya cargados, los otros se desactivan.
      UPDATE invoke.caso_estado ce SET activo = false
      FROM invoke.caso c
      WHERE ce.caso_id = c.id AND c.guia_id = NEW.guia_id AND c.sustituto = NEW.sustituto
        AND c.grupo_sustituto > NEW.grupo_sustituto AND ce.intentos = 0;
    END IF;
  END IF;
  INSERT INTO invoke.caso_estado (caso_id, estudio_id, guia_id, activo)
  VALUES (NEW.id, NEW.estudio_id, NEW.guia_id, v_activo);
  RETURN NEW;
END $$;
CREATE TRIGGER caso_nace AFTER INSERT ON invoke.caso
  FOR EACH ROW EXECUTE FUNCTION invoke.tg_caso_nace();

-- Franja a la que pertenece un instante, en la zona horaria del estudio.
CREATE FUNCTION invoke.franja_de(p_estudio bigint, p_t timestamptz) RETURNS text
LANGUAGE sql STABLE AS $$
  SELECT f.nombre
  FROM invoke.franja f JOIN invoke.estudio e ON e.id = f.estudio_id
  WHERE f.estudio_id = p_estudio
    AND (p_t AT TIME ZONE e.zona_horaria)::time >= f.desde
    AND (p_t AT TIME ZONE e.zona_horaria)::time <  f.hasta
  ORDER BY f.desde LIMIT 1
$$;

-- Próximo instante en que el caso puede volver a la cola, o NULL si ya no queda
-- ninguna franja con intentos disponibles (no-contacto definitivo).
--   ventana 'misma'      prefiere la misma franja del intento anterior
--   ventana 'rotar'      exige una franja distinta de la del intento anterior
--   ventana 'cualquiera' la primera franja habilitada
-- Si la preferida no está disponible se usa cualquier franja habilitada.
CREATE FUNCTION invoke.calcular_proximo_intento(
  p_estudio bigint, p_desde timestamptz, p_ventana text, p_franja_previa text, p_por_franja jsonb
) RETURNS timestamptz LANGUAGE plpgsql STABLE AS $$
DECLARE
  v_tz       text;
  v_tope     integer;
  v_dia      date;
  v_inicio   timestamptz;
  v_fin      timestamptz;
  v_mejor    timestamptz;
  v_cualq    timestamptz;
  f          record;
BEGIN
  SELECT zona_horaria, tope_intentos_franja INTO v_tz, v_tope FROM invoke.estudio WHERE id = p_estudio;
  IF NOT EXISTS (SELECT 1 FROM invoke.franja WHERE estudio_id = p_estudio) THEN
    RETURN p_desde;   -- estudio sin franjas: solo cuenta la espera
  END IF;
  FOR i IN 0..14 LOOP
    v_dia := (p_desde AT TIME ZONE v_tz)::date + i;
    FOR f IN SELECT * FROM invoke.franja WHERE estudio_id = p_estudio ORDER BY desde LOOP
      CONTINUE WHEN COALESCE((p_por_franja ->> f.nombre)::int, 0) >= v_tope;
      v_inicio := greatest(p_desde, (v_dia + f.desde) AT TIME ZONE v_tz);
      v_fin    := (v_dia + f.hasta) AT TIME ZONE v_tz;
      CONTINUE WHEN v_inicio >= v_fin;
      IF v_cualq IS NULL THEN v_cualq := v_inicio; END IF;
      IF p_ventana = 'cualquiera' OR p_franja_previa IS NULL
         OR (p_ventana = 'misma' AND f.nombre = p_franja_previa)
         OR (p_ventana = 'rotar' AND f.nombre <> p_franja_previa) THEN
        v_mejor := v_inicio;
        EXIT;
      END IF;
    END LOOP;
    EXIT WHEN v_mejor IS NOT NULL;
  END LOOP;
  RETURN COALESCE(v_mejor, v_cualq);
END $$;

-- Activa el siguiente sustituto de la ranura (legacy activateNextSubstitute).
CREATE FUNCTION invoke.activar_siguiente_sustituto(p_caso bigint) RETURNS void
LANGUAGE plpgsql AS $$
DECLARE
  c invoke.caso;
BEGIN
  SELECT * INTO c FROM invoke.caso WHERE id = p_caso;
  IF c.sustituto IS NULL OR NOT (SELECT sustitutos_habilitados FROM invoke.estudio WHERE id = c.estudio_id) THEN
    RETURN;
  END IF;
  UPDATE invoke.caso_estado SET activo = true
  WHERE caso_id = (
    SELECT s.id FROM invoke.caso s JOIN invoke.caso_estado se ON se.caso_id = s.id
    WHERE s.guia_id = c.guia_id AND s.sustituto = c.sustituto
      AND s.grupo_sustituto > c.grupo_sustituto AND NOT se.activo AND se.estado <> 'cerrado'
    ORDER BY s.grupo_sustituto LIMIT 1);
END $$;

-- Cierra el caso en la proyección. Si el cierre no viene de una disposición
-- (tope de intentos, de disposición o de franjas) se registra además como hecho
-- 'caso_cerrado', para que los reportes lo vean en el log y no en la proyección.
CREATE FUNCTION invoke.cerrar_caso(p_caso bigint, p_motivo text, p_t timestamptz) RETURNS void
LANGUAGE plpgsql AS $$
BEGIN
  UPDATE invoke.caso_estado
  SET estado = 'cerrado', cerrado_en = p_t, motivo_cierre = p_motivo,
      asignado_a = NULL, agenda_desde = NULL, agenda_hasta = NULL, agenda_participante = NULL
  WHERE caso_id = p_caso;
  IF p_motivo <> 'disposicion_final' THEN
    INSERT INTO invoke.evento (tipo, ocurrido_en, caso_id, autor, datos)
    VALUES ('caso_cerrado', p_t, p_caso, 'sistema', jsonb_build_object('motivo', p_motivo));
  END IF;
END $$;

-- Validaciones previas a aceptar un evento. Son las invariantes del dominio:
-- viven acá, no repetidas en Python.
CREATE FUNCTION invoke.tg_evento_validar() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  ce        invoke.caso_estado;
  v_cod     smallint;
  v_version integer;
  v_origen  text;
  v_disp    record;
BEGIN
  IF NEW.caso_id IS NOT NULL THEN
    SELECT estudio_id INTO v_version FROM invoke.caso WHERE id = NEW.caso_id;
    IF NEW.estudio_id IS NULL THEN
      NEW.estudio_id := v_version;
    ELSIF NEW.estudio_id <> v_version THEN
      RAISE EXCEPTION 'el caso % no pertenece al estudio %', NEW.caso_id, NEW.estudio_id;
    END IF;
    SELECT * INTO ce FROM invoke.caso_estado WHERE caso_id = NEW.caso_id FOR UPDATE;
  END IF;

  IF NEW.estudio_id IS NOT NULL THEN
    SELECT version_diccionario INTO v_version FROM invoke.estudio WHERE id = NEW.estudio_id;
  END IF;

  CASE NEW.tipo
  WHEN 'caso_asignado' THEN
    IF NEW.caso_id IS NULL OR NEW.participante_id IS NULL THEN
      RAISE EXCEPTION 'caso_asignado requiere caso y participante';
    END IF;
    IF ce.estado = 'cerrado' THEN
      RAISE EXCEPTION 'el caso % está cerrado', NEW.caso_id;
    END IF;
    IF ce.asignado_a IS NOT NULL AND ce.asignado_a <> NEW.participante_id THEN
      RAISE EXCEPTION 'el caso % ya está asignado a otro participante', NEW.caso_id;
    END IF;

  WHEN 'disposicion_telefono' THEN
    v_cod := (NEW.datos ->> 'codigo')::smallint;
    IF NEW.llamada_id IS NULL OR v_cod IS NULL THEN
      RAISE EXCEPTION 'disposicion_telefono requiere llamada y código';
    END IF;
    SELECT * INTO v_disp FROM invoke.disposicion_telefono
    WHERE version_diccionario = COALESCE(v_version, 1) AND codigo = v_cod;
    IF NOT FOUND OR v_disp.es_estado_interno OR v_disp.estado = 'revision' THEN
      RAISE EXCEPTION 'código de teléfono % no es una disposición de intento', v_cod;
    END IF;
    IF NEW.caso_id IS NULL AND NOT EXISTS (
         SELECT 1 FROM invoke.evento WHERE llamada_id = NEW.llamada_id AND tipo = 'llamada_originada'
           AND datos ->> 'tipo' = 'administrativa') THEN
      RAISE EXCEPTION 'no hay llamadas huérfanas (ADR-008): la disposición necesita un caso';
    END IF;

  WHEN 'disposicion_encuesta' THEN
    v_cod := (NEW.datos ->> 'codigo')::smallint;
    v_origen := COALESCE(NEW.datos ->> 'origen', 'encuestador');
    IF NEW.caso_id IS NULL OR v_cod IS NULL THEN
      RAISE EXCEPTION 'disposicion_encuesta requiere caso y código';
    END IF;
    SELECT * INTO v_disp FROM invoke.disposicion_encuesta
    WHERE version_diccionario = COALESCE(v_version, 1) AND codigo = v_cod;
    IF NOT FOUND OR v_disp.es_estado_interno THEN
      RAISE EXCEPTION 'código de encuesta % no es una disposición', v_cod;
    END IF;
    IF v_origen NOT IN ('encuestador', 'sistema', 'promocion', 'supervisor') THEN
      RAISE EXCEPTION 'origen de disposición desconocido: %', v_origen;
    END IF;
    IF v_disp.comportamiento = 21 AND v_origen IN ('encuestador', 'sistema')
       AND COALESCE(btrim(NEW.datos ->> 'motivo'), '') = '' THEN
      RAISE EXCEPTION 'la disposición % exige un texto de motivo', v_cod;
    END IF;
    IF v_origen = 'promocion' THEN
      -- Decisión 6 del diccionario: una parcial pasa a completa solo si la promueve
      -- un supervisor, con autor, motivo y marca de tiempo.
      IF v_cod <> 3 OR ce.ultima_disp_enc IS DISTINCT FROM 7 THEN
        RAISE EXCEPTION 'solo se promueve una Interrumpida (7) a Completa (3)';
      END IF;
      IF NEW.autor IS NULL OR COALESCE(btrim(NEW.datos ->> 'motivo'), '') = '' THEN
        RAISE EXCEPTION 'la promoción exige autor y motivo';
      END IF;
    ELSIF v_origen = 'supervisor' THEN
      IF NEW.autor IS NULL OR NOT v_disp.extendido THEN
        RAISE EXCEPTION 'un supervisor solo aplica estados extendidos, con autor';
      END IF;
    ELSE
      IF ce.estado = 'cerrado' THEN
        RAISE EXCEPTION 'el caso % está cerrado', NEW.caso_id;
      END IF;
      -- Solo 4 "Contesta" habilita una disposición de encuesta.
      IF NEW.llamada_id IS NULL OR NOT EXISTS (
           SELECT 1 FROM invoke.evento WHERE llamada_id = NEW.llamada_id
             AND tipo = 'disposicion_telefono' AND (datos ->> 'codigo')::int = 4) THEN
        RAISE EXCEPTION 'la disposición de encuesta exige que la llamada tenga disposición 4 (Contesta)';
      END IF;
      IF v_cod = 5 AND NOT (NEW.datos ? 'agenda_desde') THEN
        RAISE EXCEPTION 'Agendada exige la ventana de agenda';
      END IF;
    END IF;

  WHEN 'reclasificacion_amd' THEN
    IF NEW.autor IS NULL OR NOT EXISTS (
         SELECT 1 FROM invoke.evento WHERE llamada_id = NEW.llamada_id
           AND tipo = 'disposicion_telefono' AND (datos ->> 'codigo')::int = 31) THEN
      RAISE EXCEPTION 'solo se reclasifica como 32 una llamada con disposición 31, con autor';
    END IF;

  WHEN 'caso_creado' THEN
    IF NEW.caso_id IS NULL OR NEW.autor IS NULL THEN
      RAISE EXCEPTION 'caso_creado requiere caso y autor';
    END IF;

  ELSE NULL;
  END CASE;
  RETURN NEW;
END $$;
CREATE TRIGGER evento_validar BEFORE INSERT ON invoke.evento
  FOR EACH ROW EXECUTE FUNCTION invoke.tg_evento_validar();

-- Aplica un evento a la proyección del caso. Es la única función que escribe
-- invoke.caso_estado (además de su nacimiento y la reconstrucción).
CREATE FUNCTION invoke.aplicar_evento_caso(e invoke.evento) RETURNS void
LANGUAGE plpgsql AS $$
DECLARE
  ce          invoke.caso_estado;
  est         invoke.estudio;
  regla       invoke.regla_reintento;
  dt          invoke.disposicion_telefono;
  de          invoke.disposicion_encuesta;
  v_cod       smallint;
  v_franja    text;
  v_n_disp    integer;
  v_prox      timestamptz;
  v_agenda    boolean;
BEGIN
  SELECT * INTO ce FROM invoke.caso_estado WHERE caso_id = e.caso_id FOR UPDATE;
  SELECT * INTO est FROM invoke.estudio WHERE id = ce.estudio_id;

  CASE e.tipo
  WHEN 'caso_asignado' THEN
    UPDATE invoke.caso_estado SET estado = 'asignado', asignado_a = e.participante_id
    WHERE caso_id = e.caso_id;

  WHEN 'caso_liberado' THEN
    UPDATE invoke.caso_estado
    SET estado = CASE WHEN agenda_desde IS NOT NULL THEN 'agendado' ELSE 'pendiente' END,
        asignado_a = NULL
    WHERE caso_id = e.caso_id AND estado IN ('asignado', 'en_llamada');

  WHEN 'llamada_originada' THEN
    IF e.participante_id IS NOT NULL THEN
      UPDATE invoke.caso_estado SET estado = 'en_llamada', asignado_a = e.participante_id
      WHERE caso_id = e.caso_id AND estado <> 'cerrado';
    ELSE
      -- Power y predictivo: el caso se reserva sin participante hasta que atienden.
      UPDATE invoke.caso_estado SET estado = 'en_llamada'
      WHERE caso_id = e.caso_id AND estado <> 'cerrado' AND asignado_a IS NULL
        AND estado IN ('pendiente', 'agendado');
    END IF;

  WHEN 'llamada_conectada' THEN
    UPDATE invoke.caso_estado SET estado = 'en_llamada', asignado_a = e.participante_id
    WHERE caso_id = e.caso_id AND estado <> 'cerrado';

  WHEN 'llamada_descartada' THEN
    -- El pacer cortó antes de que atiendan: no es un intento, el caso vuelve tal cual.
    UPDATE invoke.caso_estado
    SET estado = CASE WHEN agenda_desde IS NOT NULL THEN 'agendado' ELSE 'pendiente' END,
        asignado_a = NULL
    WHERE caso_id = e.caso_id AND estado = 'en_llamada';

  WHEN 'disposicion_telefono' THEN
    v_cod := (e.datos ->> 'codigo')::smallint;
    SELECT * INTO dt FROM invoke.disposicion_telefono
    WHERE version_diccionario = est.version_diccionario AND codigo = v_cod;
    v_franja := COALESCE(invoke.franja_de(ce.estudio_id, e.ocurrido_en), 'fuera_de_franja');

    IF dt.cuenta_intento THEN
      ce.intentos := ce.intentos + 1;
      ce.intentos_por_franja := jsonb_set(ce.intentos_por_franja, ARRAY[v_franja],
        to_jsonb(COALESCE((ce.intentos_por_franja ->> v_franja)::int, 0) + 1));
      ce.intentos_por_disposicion := jsonb_set(ce.intentos_por_disposicion, ARRAY[v_cod::text],
        to_jsonb(COALESCE((ce.intentos_por_disposicion ->> v_cod::text)::int, 0) + 1));
    END IF;

    UPDATE invoke.caso_estado
    SET intentos = ce.intentos, intentos_por_franja = ce.intentos_por_franja,
        intentos_por_disposicion = ce.intentos_por_disposicion,
        ultima_franja = v_franja, ultima_disp_tel = v_cod,
        abandonada_previa = abandonada_previa OR v_cod = 30
    WHERE caso_id = e.caso_id;

    IF dt.estado = 'habilita_encuesta' THEN
      NULL;  -- 4 Contesta: el caso sigue en llamada hasta la disposición de encuesta
    ELSIF dt.estado = 'final' THEN
      PERFORM invoke.cerrar_caso(e.caso_id, 'disposicion_final', e.ocurrido_en);
      IF dt.activa_sustituto THEN PERFORM invoke.activar_siguiente_sustituto(e.caso_id); END IF;
    ELSIF dt.estado = 'reintentable' THEN
      SELECT * INTO regla FROM invoke.regla_reintento
      WHERE estudio_id = ce.estudio_id AND codigo_telefono = v_cod;
      v_n_disp := COALESCE((ce.intentos_por_disposicion ->> v_cod::text)::int, 0);
      IF ce.intentos >= est.tope_intentos_caso THEN
        PERFORM invoke.cerrar_caso(e.caso_id, 'tope_intentos', e.ocurrido_en);
      ELSIF regla.tope IS NOT NULL AND v_n_disp >= regla.tope THEN
        PERFORM invoke.cerrar_caso(e.caso_id, 'tope_disposicion', e.ocurrido_en);
      ELSE
        v_prox := invoke.calcular_proximo_intento(
          ce.estudio_id, e.ocurrido_en + make_interval(mins => COALESCE(regla.espera_min, 0)),
          COALESCE(regla.ventana, 'cualquiera'), v_franja, ce.intentos_por_franja);
        IF v_prox IS NULL THEN
          PERFORM invoke.cerrar_caso(e.caso_id, 'tope_franjas', e.ocurrido_en);
        ELSE
          -- Un caso agendado conserva la agenda mientras su ventana siga vigente.
          v_agenda := ce.agenda_hasta IS NOT NULL AND ce.agenda_hasta > e.ocurrido_en;
          UPDATE invoke.caso_estado
          SET estado = CASE WHEN v_agenda THEN 'agendado' ELSE 'pendiente' END,
              disponible_desde = v_prox, asignado_a = NULL,
              agenda_desde = CASE WHEN v_agenda THEN agenda_desde END,
              agenda_hasta = CASE WHEN v_agenda THEN agenda_hasta END,
              agenda_participante = CASE WHEN v_agenda THEN agenda_participante END
          WHERE caso_id = e.caso_id;
        END IF;
      END IF;
      IF dt.activa_sustituto THEN PERFORM invoke.activar_siguiente_sustituto(e.caso_id); END IF;
    END IF;

  WHEN 'reclasificacion_amd' THEN
    UPDATE invoke.caso_estado SET abandonada_previa = true WHERE caso_id = e.caso_id;

  WHEN 'disposicion_encuesta' THEN
    v_cod := (e.datos ->> 'codigo')::smallint;
    SELECT * INTO de FROM invoke.disposicion_encuesta
    WHERE version_diccionario = est.version_diccionario AND codigo = v_cod;
    UPDATE invoke.caso_estado SET ultima_disp_enc = v_cod WHERE caso_id = e.caso_id;
    IF de.efecto_caso = 'cerrar' THEN
      IF ce.estado <> 'cerrado' THEN
        PERFORM invoke.cerrar_caso(e.caso_id, 'disposicion_final', e.ocurrido_en);
      END IF;
      IF de.activa_sustituto THEN PERFORM invoke.activar_siguiente_sustituto(e.caso_id); END IF;
    ELSIF de.efecto_caso = 'reabrir' THEN
      UPDATE invoke.caso_estado
      SET estado = 'pendiente', cerrado_en = NULL, motivo_cierre = NULL,
          asignado_a = NULL, disponible_desde = e.ocurrido_en
      WHERE caso_id = e.caso_id;
    ELSIF de.efecto_caso = 'agendar' THEN
      UPDATE invoke.caso_estado
      SET estado = 'agendado', asignado_a = NULL,
          agenda_desde = (e.datos ->> 'agenda_desde')::timestamptz,
          agenda_hasta = COALESCE((e.datos ->> 'agenda_hasta')::timestamptz,
                                  (e.datos ->> 'agenda_desde')::timestamptz + interval '1 hour'),
          agenda_participante = CASE WHEN COALESCE((e.datos ->> 'preservar_encuestador')::boolean,
                                                   est.preservar_encuestador)
                                     THEN e.participante_id END,
          disponible_desde = (e.datos ->> 'agenda_desde')::timestamptz
      WHERE caso_id = e.caso_id;
    END IF;

  ELSE NULL;
  END CASE;

  UPDATE invoke.caso_estado SET ultimo_evento = e.id WHERE caso_id = e.caso_id;
END $$;

-- ─────────────────────── Proyección del participante ───────────────────────
-- ADR-001, excepción registrada: el estado del encuestador alimenta el lazo del
-- discador, así que su fuente de verdad está acá y se espeja a Firebase.

CREATE TABLE invoke.participante_estado (
  participante_id  bigint PRIMARY KEY REFERENCES invoke.participante,
  estado           text NOT NULL DEFAULT 'desconectado'
                   CHECK (estado IN ('desconectado', 'pausa', 'disponible', 'preview', 'en_llamada', 'clasificando')),
  estudio_id       bigint REFERENCES invoke.estudio,
  sesion_id        uuid,
  desde            timestamptz NOT NULL DEFAULT now(),
  caso_id          bigint REFERENCES invoke.caso,
  llamada_id       uuid,
  canal_audio      text,           -- id del canal ARI de la pata permanente del puesto
  ultimo_evento    bigint
);
CREATE INDEX participante_estado_estudio_idx ON invoke.participante_estado (estudio_id, estado);

CREATE FUNCTION invoke.aplicar_evento_participante(e invoke.evento) RETURNS void
LANGUAGE plpgsql AS $$
DECLARE
  v_estado text;
BEGIN
  INSERT INTO invoke.participante_estado (participante_id) VALUES (e.participante_id)
  ON CONFLICT DO NOTHING;

  CASE e.tipo
  WHEN 'sesion_iniciada' THEN
    UPDATE invoke.participante_estado
    SET estado = 'pausa', estudio_id = e.estudio_id, sesion_id = (e.datos ->> 'sesion_id')::uuid,
        desde = e.ocurrido_en, caso_id = NULL, llamada_id = NULL
    WHERE participante_id = e.participante_id;
  WHEN 'sesion_finalizada' THEN
    UPDATE invoke.participante_estado
    SET estado = 'desconectado', estudio_id = NULL, sesion_id = NULL, desde = e.ocurrido_en,
        caso_id = NULL, llamada_id = NULL, canal_audio = NULL
    WHERE participante_id = e.participante_id;
  WHEN 'estado_participante' THEN
    v_estado := e.datos ->> 'estado';
    UPDATE invoke.participante_estado
    SET estado = v_estado, desde = e.ocurrido_en
    WHERE participante_id = e.participante_id AND estado IS DISTINCT FROM v_estado;
  WHEN 'audio_conectado' THEN
    UPDATE invoke.participante_estado SET canal_audio = e.datos ->> 'canal'
    WHERE participante_id = e.participante_id;
  WHEN 'audio_desconectado' THEN
    UPDATE invoke.participante_estado SET canal_audio = NULL
    WHERE participante_id = e.participante_id;
  WHEN 'caso_asignado' THEN
    UPDATE invoke.participante_estado
    SET caso_id = e.caso_id, estado = 'preview', desde = e.ocurrido_en
    WHERE participante_id = e.participante_id;
  WHEN 'llamada_conectada' THEN
    UPDATE invoke.participante_estado
    SET caso_id = COALESCE(e.caso_id, caso_id), llamada_id = e.llamada_id,
        estado = 'en_llamada', desde = e.ocurrido_en
    WHERE participante_id = e.participante_id;
  WHEN 'llamada_originada' THEN
    UPDATE invoke.participante_estado SET llamada_id = e.llamada_id
    WHERE participante_id = e.participante_id;
  WHEN 'llamada_finalizada' THEN
    UPDATE invoke.participante_estado
    SET llamada_id = NULL,
        estado = CASE WHEN estado IN ('en_llamada', 'preview') THEN 'clasificando' ELSE estado END,
        desde = CASE WHEN estado IN ('en_llamada', 'preview') THEN e.ocurrido_en ELSE desde END
    WHERE participante_id = e.participante_id AND llamada_id = e.llamada_id;
  WHEN 'caso_liberado' THEN
    UPDATE invoke.participante_estado SET caso_id = NULL
    WHERE participante_id = e.participante_id AND caso_id = e.caso_id;
  ELSE NULL;
  END CASE;

  UPDATE invoke.participante_estado SET ultimo_evento = e.id WHERE participante_id = e.participante_id;
END $$;

-- Disparo único después de cada evento: proyecciones, lista negra y aviso a la sala.
CREATE FUNCTION invoke.tg_evento_aplicar() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  v_numero text;
BEGIN
  IF NEW.caso_id IS NOT NULL AND NEW.tipo IN (
       'caso_asignado', 'caso_liberado', 'llamada_originada', 'llamada_conectada', 'llamada_descartada',
       'disposicion_telefono', 'disposicion_encuesta', 'reclasificacion_amd') THEN
    PERFORM invoke.aplicar_evento_caso(NEW);
  END IF;

  IF NEW.participante_id IS NOT NULL AND NEW.tipo IN (
       'sesion_iniciada', 'sesion_finalizada', 'estado_participante', 'audio_conectado',
       'audio_desconectado', 'caso_asignado', 'llamada_conectada', 'llamada_originada',
       'llamada_finalizada', 'caso_liberado') THEN
    PERFORM invoke.aplicar_evento_participante(NEW);
  END IF;

  -- Si la disposición de encuesta cierra el caso, el participante queda libre de él.
  IF NEW.tipo IN ('disposicion_encuesta', 'disposicion_telefono') AND NEW.caso_id IS NOT NULL THEN
    UPDATE invoke.participante_estado pe SET caso_id = NULL
    FROM invoke.caso_estado ce
    WHERE ce.caso_id = NEW.caso_id AND pe.caso_id = NEW.caso_id AND ce.asignado_a IS NULL;
  END IF;

  -- Lista negra transversal: 5 "No volver a llamar".
  IF NEW.tipo = 'disposicion_telefono' AND (NEW.datos ->> 'codigo')::int = 5 THEN
    SELECT datos ->> 'numero' INTO v_numero FROM invoke.evento
    WHERE llamada_id = NEW.llamada_id AND tipo = 'llamada_originada' LIMIT 1;
    IF v_numero IS NOT NULL THEN
      INSERT INTO invoke.lista_negra (numero_normalizado, desde, estudio_id, caso_id, evento_id)
      VALUES (invoke.normalizar_telefono(v_numero), NEW.ocurrido_en, NEW.estudio_id, NEW.caso_id, NEW.id)
      ON CONFLICT DO NOTHING;
    END IF;
  END IF;

  PERFORM pg_notify('invoke_eventos', json_build_object(
    'id', NEW.id, 'tipo', NEW.tipo, 'estudio_id', NEW.estudio_id, 'caso_id', NEW.caso_id,
    'participante_id', NEW.participante_id, 'llamada_id', NEW.llamada_id)::text);
  RETURN NULL;
END $$;
CREATE TRIGGER evento_aplicar AFTER INSERT ON invoke.evento
  FOR EACH ROW EXECUTE FUNCTION invoke.tg_evento_aplicar();

-- Reconstrucción de la proyección de un caso desde sus eventos. Sirve para
-- auditar que la proyección no se desvió (tests/test_proyeccion.py) y para
-- reparar si algún día se cambia la lógica de aplicación.
CREATE FUNCTION invoke.reconstruir_caso_estado(p_caso bigint) RETURNS void
LANGUAGE plpgsql AS $$
DECLARE
  e invoke.evento;
  c invoke.caso;
  v_activo boolean;
BEGIN
  SELECT * INTO c FROM invoke.caso WHERE id = p_caso;
  SELECT activo INTO v_activo FROM invoke.caso_estado WHERE caso_id = p_caso;
  DELETE FROM invoke.caso_estado WHERE caso_id = p_caso;
  INSERT INTO invoke.caso_estado (caso_id, estudio_id, guia_id, activo)
  VALUES (c.id, c.estudio_id, c.guia_id, COALESCE(v_activo, true));
  FOR e IN SELECT * FROM invoke.evento WHERE caso_id = p_caso AND tipo IN (
      'caso_asignado', 'caso_liberado', 'llamada_originada', 'llamada_conectada', 'llamada_descartada',
      'disposicion_telefono', 'disposicion_encuesta', 'reclasificacion_amd') ORDER BY id LOOP
    PERFORM invoke.aplicar_evento_caso(e);
  END LOOP;
END $$;

GRANT SELECT, INSERT ON invoke.evento TO invoke_app;
GRANT USAGE ON SEQUENCE invoke.evento_id_seq TO invoke_app;
GRANT SELECT ON invoke.caso_estado, invoke.participante_estado TO invoke_app;
GRANT SELECT ON invoke.evento TO invoke_lectura;
GRANT EXECUTE ON FUNCTION invoke.franja_de(bigint, timestamptz),
  invoke.calcular_proximo_intento(bigint, timestamptz, text, text, jsonb) TO invoke_app;

-- Las proyecciones no son escribibles por la aplicación: solo las tocan estos
-- disparadores, que corren con los privilegios del dueño del esquema.
ALTER FUNCTION invoke.tg_evento_validar()    SECURITY DEFINER SET search_path = invoke, pg_temp;
ALTER FUNCTION invoke.tg_evento_aplicar()    SECURITY DEFINER SET search_path = invoke, pg_temp;
ALTER FUNCTION invoke.tg_caso_nace()         SECURITY DEFINER SET search_path = invoke, pg_temp;
ALTER FUNCTION invoke.reconstruir_caso_estado(bigint) SECURITY DEFINER SET search_path = invoke, pg_temp;
