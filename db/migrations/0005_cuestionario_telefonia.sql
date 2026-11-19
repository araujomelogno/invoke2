-- 0005 — Cuestionarios (ADR-006), respuestas, calidad de red, cola de comandos
-- de telefonía y tablas de Asterisk realtime.

-- Inmutabilidad genérica para tablas de hechos.
CREATE FUNCTION invoke.tg_inmutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION '% es inmutable: % rechazado', TG_TABLE_NAME, TG_OP;
END $$;

CREATE TABLE invoke.cuestionario (
  id            bigserial PRIMARY KEY,
  estudio_id    bigint  NOT NULL REFERENCES invoke.estudio ON DELETE CASCADE,
  version       integer NOT NULL CHECK (version > 0),
  definicion    jsonb   NOT NULL CHECK (jsonb_typeof(definicion) = 'object'),
  creado_por    text    REFERENCES invoke.usuario,
  creado_en     timestamptz NOT NULL DEFAULT now(),
  publicado_en  timestamptz,
  UNIQUE (estudio_id, version)
);

-- Una versión publicada no se modifica: hay respuestas que dependen de ella.
CREATE FUNCTION invoke.tg_cuestionario_publicado() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.publicado_en IS NOT NULL THEN
    RAISE EXCEPTION 'el cuestionario % versión % ya está publicado: creá una versión nueva', OLD.estudio_id, OLD.version;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER cuestionario_publicado BEFORE UPDATE OR DELETE ON invoke.cuestionario
  FOR EACH ROW EXECUTE FUNCTION invoke.tg_cuestionario_publicado();

CREATE VIEW invoke.cuestionario_vigente AS
  SELECT DISTINCT ON (estudio_id) *
  FROM invoke.cuestionario
  WHERE publicado_en IS NOT NULL
  ORDER BY estudio_id, version DESC;

-- Respuestas: solo se agregan. Volver atrás y cambiar una respuesta agrega una
-- fila nueva; la vigente es la última por (caso, variable).
CREATE TABLE invoke.respuesta (
  id               bigserial PRIMARY KEY,
  caso_id          bigint NOT NULL REFERENCES invoke.caso,
  cuestionario_id  bigint NOT NULL REFERENCES invoke.cuestionario,
  participante_id  bigint REFERENCES invoke.participante,
  llamada_id       uuid,
  variable         text   NOT NULL,
  valor            jsonb,
  registrada_en    timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX respuesta_caso_idx ON invoke.respuesta (caso_id, variable, id DESC);
CREATE TRIGGER respuesta_inmutable BEFORE UPDATE OR DELETE ON invoke.respuesta
  FOR EACH ROW EXECUTE FUNCTION invoke.tg_inmutable();

CREATE VIEW invoke.respuesta_vigente AS
  SELECT DISTINCT ON (caso_id, variable) caso_id, variable, valor, cuestionario_id, participante_id, registrada_en
  FROM invoke.respuesta
  ORDER BY caso_id, variable, id DESC;

-- Calidad de red del puesto remoto (getStats de WebRTC cada pocos segundos).
CREATE TABLE invoke.calidad_red (
  id               bigserial PRIMARY KEY,
  participante_id  bigint NOT NULL REFERENCES invoke.participante,
  llamada_id       uuid,
  tomada_en        timestamptz NOT NULL DEFAULT clock_timestamp(),
  perdida_pct      numeric(5,2) CHECK (perdida_pct BETWEEN 0 AND 100),
  jitter_ms        numeric(8,2) CHECK (jitter_ms >= 0),
  rtt_ms           numeric(8,2) CHECK (rtt_ms >= 0),
  relay            boolean,                -- la llamada va por TURN
  codec            text,
  mos              numeric(3,2)
);
CREATE INDEX calidad_red_participante_idx ON invoke.calidad_red (participante_id, tomada_en DESC);
CREATE TRIGGER calidad_red_inmutable BEFORE UPDATE OR DELETE ON invoke.calidad_red
  FOR EACH ROW EXECUTE FUNCTION invoke.tg_inmutable();

-- Cola de comandos hacia Asterisk. El escucha de ARI es el único consumidor
-- (ADR-010): toma comandos con SKIP LOCKED y los ejecuta contra ARI.
CREATE TABLE invoke.comando_telefonia (
  id               bigserial PRIMARY KEY,
  creado_en        timestamptz NOT NULL DEFAULT clock_timestamp(),
  tipo             text NOT NULL CHECK (tipo IN ('originar', 'colgar', 'escuchar', 'dejar_escuchar',
                                                  'mudo', 'transferir', 'grabar', 'dtmf')),
  participante_id  bigint REFERENCES invoke.participante,
  llamada_id       uuid,
  autor            text,
  datos            jsonb NOT NULL DEFAULT '{}',
  estado           text NOT NULL DEFAULT 'pendiente' CHECK (estado IN ('pendiente', 'tomado', 'hecho', 'error')),
  tomado_en        timestamptz,
  resultado        text
);
CREATE INDEX comando_pendiente_idx ON invoke.comando_telefonia (id) WHERE estado = 'pendiente';

CREATE FUNCTION invoke.tg_comando_notificar() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  PERFORM pg_notify('invoke_comandos', NEW.id::text);
  RETURN NULL;
END $$;
CREATE TRIGGER comando_notificar AFTER INSERT ON invoke.comando_telefonia
  FOR EACH ROW EXECUTE FUNCTION invoke.tg_comando_notificar();

-- Asterisk realtime (res_config_pgsql). Solo las columnas que INVOKE usa; las
-- demás toman el valor por defecto del template en pjsip.conf.
CREATE SCHEMA IF NOT EXISTS asterisk;
REVOKE ALL ON SCHEMA asterisk FROM PUBLIC;
GRANT USAGE ON SCHEMA asterisk TO asterisk_rt, invoke_app;

CREATE TABLE asterisk.ps_aors (
  id              text PRIMARY KEY,
  max_contacts    integer DEFAULT 1,
  remove_existing text DEFAULT 'yes',
  qualify_frequency integer DEFAULT 30
);
-- Sin contraseñas en texto plano (patología del legacy): se guarda solo el hash
-- md5(usuario:realm:clave). La clave es efímera: se rota en cada inicio de sesión
-- y viaja una sola vez al navegador del encuestador autenticado.
CREATE TABLE asterisk.ps_auths (
  id         text PRIMARY KEY,
  auth_type  text NOT NULL DEFAULT 'md5' CHECK (auth_type = 'md5'),
  username   text NOT NULL,
  realm      text NOT NULL,
  md5_cred   text NOT NULL CHECK (md5_cred ~ '^[0-9a-f]{32}$')
);
CREATE TABLE asterisk.ps_endpoints (
  id              text PRIMARY KEY,
  transport       text DEFAULT 'transport-wss',
  aors            text,
  auth            text,
  context         text DEFAULT 'invoke-puesto',
  disallow        text DEFAULT 'all',
  allow           text DEFAULT 'alaw,ulaw,opus',
  webrtc          text DEFAULT 'yes',
  dtls_auto_generate_cert text DEFAULT 'yes',
  direct_media    text DEFAULT 'no',
  ice_support     text DEFAULT 'yes',
  callerid        text
);

GRANT SELECT, INSERT, UPDATE ON invoke.cuestionario TO invoke_app;
GRANT USAGE ON SEQUENCE invoke.cuestionario_id_seq, invoke.respuesta_id_seq, invoke.calidad_red_id_seq,
  invoke.comando_telefonia_id_seq TO invoke_app;
GRANT SELECT, INSERT ON invoke.respuesta, invoke.calidad_red TO invoke_app;
GRANT SELECT, INSERT, UPDATE ON invoke.comando_telefonia TO invoke_app;
GRANT SELECT ON invoke.cuestionario_vigente, invoke.respuesta_vigente TO invoke_app, invoke_lectura;
GRANT SELECT, INSERT, UPDATE ON asterisk.ps_aors, asterisk.ps_auths, asterisk.ps_endpoints TO invoke_app;
GRANT SELECT ON asterisk.ps_aors, asterisk.ps_auths, asterisk.ps_endpoints TO asterisk_rt;
