-- 0003 — Usuarios, participantes (ADR-007), estudios, guías, franjas, casos y teléfonos.

CREATE TABLE invoke.usuario (
  uid        text PRIMARY KEY,               -- uid de Firebase Auth
  email      text NOT NULL UNIQUE,
  nombre     text NOT NULL,
  roles      text[] NOT NULL CHECK (roles <@ ARRAY['encuestador', 'supervisor', 'administrador', 'investigador']::text[]
                                    AND cardinality(roles) > 0),
  activo     boolean NOT NULL DEFAULT true,
  creado_en  timestamptz NOT NULL DEFAULT now()
);

-- ADR-007: el núcleo asigna casos a participantes, no a usuarios. El encuestador
-- con WebRTC es la primera implementación; un agente de IA sería otro tipo.
CREATE TABLE invoke.participante (
  id           bigserial PRIMARY KEY,
  tipo         text NOT NULL CHECK (tipo IN ('encuestador_webrtc', 'agente_ia')),
  usuario_uid  text UNIQUE REFERENCES invoke.usuario,
  nombre       text NOT NULL,
  activo       boolean NOT NULL DEFAULT true,
  CHECK (tipo <> 'encuestador_webrtc' OR usuario_uid IS NOT NULL)
);

CREATE TABLE invoke.estudio (
  id                      bigserial PRIMARY KEY,
  codigo                  text NOT NULL UNIQUE,
  nombre                  text NOT NULL,
  cliente                 text,
  -- Marca "no es un estudio" (pruebas, capacitaciones): sale de todo reporte.
  es_estudio              boolean NOT NULL DEFAULT true,
  tipo_marco              text NOT NULL DEFAULT 'probabilistico'
                          CHECK (tipo_marco IN ('probabilistico', 'base_cliente', 'empresas', 'dificil', 'otro')),
  estado                  text NOT NULL DEFAULT 'borrador'
                          CHECK (estado IN ('borrador', 'activo', 'pausado', 'cerrado')),
  objetivo_completas      integer CHECK (objetivo_completas IS NULL OR objetivo_completas > 0),
  -- Discado (ADR-004, Fase 2 y 3).
  modo_discado            text NOT NULL DEFAULT 'preview'
                          CHECK (modo_discado IN ('manual', 'preview', 'power', 'predictivo')),
  n_inicial               numeric(4,2) NOT NULL DEFAULT 1.5 CHECK (n_inicial >= 1),
  n_maximo                numeric(4,2) NOT NULL DEFAULT 3.0,
  n_vigente               numeric(4,2),            -- lo escribe el pacer o el supervisor; NULL = n_inicial
  abandono_objetivo       numeric(5,4) NOT NULL DEFAULT 0.03 CHECK (abandono_objetivo > 0 AND abandono_objetivo < 1),
  ajuste_automatico       boolean NOT NULL DEFAULT true,
  ventana_abandono_min    integer NOT NULL DEFAULT 30 CHECK (ventana_abandono_min BETWEEN 30 AND 60),
  min_agentes_predictivo  integer NOT NULL DEFAULT 8 CHECK (min_agentes_predictivo >= 1),
  -- Casos fuera de marco (ADR-008 y política).
  fuera_marco_habilitado  boolean NOT NULL DEFAULT false,
  umbral_alerta_derivados numeric(5,4) CHECK (umbral_alerta_derivados IS NULL OR umbral_alerta_derivados BETWEEN 0 AND 1),
  -- Sustitutos (legacy enableSubstitutes).
  sustitutos_habilitados  boolean NOT NULL DEFAULT false,
  ignorar_agendados       boolean NOT NULL DEFAULT false,
  preservar_encuestador   boolean NOT NULL DEFAULT true,
  -- Reintentos.
  tope_intentos_caso      integer NOT NULL DEFAULT 8 CHECK (tope_intentos_caso > 0),
  tope_intentos_franja    integer NOT NULL DEFAULT 3 CHECK (tope_intentos_franja > 0),
  -- Grabación con muestreo por estudio.
  muestreo_grabacion      numeric(5,4) NOT NULL DEFAULT 0.10 CHECK (muestreo_grabacion BETWEEN 0 AND 1),
  zona_horaria            text NOT NULL DEFAULT 'America/Montevideo',
  version_diccionario     integer NOT NULL DEFAULT 1 REFERENCES invoke.version_diccionario,
  version_formulas        integer NOT NULL DEFAULT 1,
  creado_en               timestamptz NOT NULL DEFAULT now(),
  cerrado_en              timestamptz,
  CHECK (n_maximo >= n_inicial),
  CHECK (n_vigente IS NULL OR (n_vigente >= 1 AND n_vigente <= n_maximo)),
  CHECK ((estado = 'cerrado') = (cerrado_en IS NOT NULL))
);

CREATE FUNCTION invoke.tg_estudio_version_formulas() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM invoke.version_documento
                 WHERE documento = 'formulas' AND version = NEW.version_formulas) THEN
    RAISE EXCEPTION 'versión de fórmulas % inexistente', NEW.version_formulas;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER estudio_version_formulas BEFORE INSERT OR UPDATE OF version_formulas ON invoke.estudio
  FOR EACH ROW EXECUTE FUNCTION invoke.tg_estudio_version_formulas();

-- Las versiones del diccionario y de fórmulas quedan fijas una vez activo el estudio.
CREATE FUNCTION invoke.tg_estudio_versiones_fijas() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.estado <> 'borrador' AND (NEW.version_diccionario <> OLD.version_diccionario
                                   OR NEW.version_formulas <> OLD.version_formulas) THEN
    RAISE EXCEPTION 'las versiones del diccionario y de fórmulas no cambian después de activar el estudio';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER estudio_versiones_fijas BEFORE UPDATE ON invoke.estudio
  FOR EACH ROW EXECUTE FUNCTION invoke.tg_estudio_versiones_fijas();

CREATE TABLE invoke.estudio_usuario (
  estudio_id  bigint NOT NULL REFERENCES invoke.estudio ON DELETE CASCADE,
  usuario_uid text   NOT NULL REFERENCES invoke.usuario,
  PRIMARY KEY (estudio_id, usuario_uid)
);

-- Franjas horarias del estudio (rotación de franja y tope por franja).
CREATE TABLE invoke.franja (
  estudio_id  bigint NOT NULL REFERENCES invoke.estudio ON DELETE CASCADE,
  nombre      text   NOT NULL,
  desde       time   NOT NULL,
  hasta       time   NOT NULL,
  PRIMARY KEY (estudio_id, nombre),
  CHECK (hasta > desde)
);

CREATE TABLE invoke.regla_reintento (
  estudio_id      bigint   NOT NULL REFERENCES invoke.estudio ON DELETE CASCADE,
  codigo_telefono smallint NOT NULL,
  espera_min      integer  NOT NULL CHECK (espera_min >= 0),
  tope            integer  CHECK (tope IS NULL OR tope > 0),
  ventana         text     NOT NULL CHECK (ventana IN ('misma', 'rotar', 'cualquiera')),
  PRIMARY KEY (estudio_id, codigo_telefono)
);

-- Al crear un estudio se copian reglas y franjas por defecto.
CREATE FUNCTION invoke.tg_estudio_defectos() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  INSERT INTO invoke.regla_reintento (estudio_id, codigo_telefono, espera_min, tope, ventana)
  SELECT NEW.id, codigo_telefono, espera_min, tope, ventana
  FROM invoke.regla_reintento_defecto WHERE version_diccionario = NEW.version_diccionario;
  -- Franjas de la línea base: mañana hasta 16:30, tarde desde 16:30.
  INSERT INTO invoke.franja VALUES (NEW.id, 'manana', '09:00', '16:30'), (NEW.id, 'tarde', '16:30', '21:00');
  RETURN NEW;
END $$;
CREATE TRIGGER estudio_defectos AFTER INSERT ON invoke.estudio
  FOR EACH ROW EXECUTE FUNCTION invoke.tg_estudio_defectos();

-- Guía (phonebook): una base de muestra cargada en el estudio.
CREATE TABLE invoke.guia (
  id                  bigserial PRIMARY KEY,
  estudio_id          bigint NOT NULL REFERENCES invoke.estudio ON DELETE CASCADE,
  nombre              text   NOT NULL,
  prioridad           integer NOT NULL DEFAULT 0,
  activa              boolean NOT NULL DEFAULT true,
  -- Tope de casos fuera de marco: propiedad de la guía (política, decisión 3). NULL = sin tope.
  tope_fuera_marco    numeric(5,4) CHECK (tope_fuera_marco IS NULL OR tope_fuera_marco BETWEEN 0 AND 1),
  agendar_por_grupo   boolean NOT NULL DEFAULT false,
  cargada_en          timestamptz NOT NULL DEFAULT now(),
  UNIQUE (estudio_id, nombre),
  UNIQUE (id, estudio_id)
);

-- El caso: la unidad muestral. ADR-008: todo caso declara su origen.
CREATE TABLE invoke.caso (
  id                  bigserial PRIMARY KEY,
  estudio_id          bigint NOT NULL,
  guia_id             bigint NOT NULL,
  origen              text   NOT NULL DEFAULT 'marco'
                      CHECK (origen IN ('marco', 'derivado', 'reemplazo_manual', 'lista_externa')),
  caso_origen         bigint,
  usuario_creador     text REFERENCES invoke.usuario,
  motivo              text,
  identificador       text,                       -- id del caso en la base del cliente
  propiedades         jsonb  NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(propiedades) = 'object'),
  estrato             text,
  area                text,                       -- prefijo de área para cuotas por área
  sustituto           text,                       -- ranura muestral (legacy substitute)
  grupo_sustituto     integer,                    -- orden dentro de la ranura
  orden_aleatorio     double precision NOT NULL DEFAULT random(),
  creado_en           timestamptz NOT NULL DEFAULT now(),
  UNIQUE (id, estudio_id),
  FOREIGN KEY (guia_id, estudio_id) REFERENCES invoke.guia (id, estudio_id),
  FOREIGN KEY (caso_origen, estudio_id) REFERENCES invoke.caso (id, estudio_id),
  -- Política de casos fuera de marco, "Campos nuevos en el caso".
  CHECK (origen <> 'marco' OR (caso_origen IS NULL AND usuario_creador IS NULL AND motivo IS NULL)),
  CHECK (origen NOT IN ('derivado', 'reemplazo_manual')
         OR (caso_origen IS NOT NULL AND usuario_creador IS NOT NULL AND motivo IS NOT NULL)),
  CHECK (origen <> 'lista_externa' OR usuario_creador IS NOT NULL),
  CHECK ((sustituto IS NULL) = (grupo_sustituto IS NULL))
);
CREATE INDEX caso_estudio_idx ON invoke.caso (estudio_id, guia_id);
CREATE INDEX caso_origen_idx ON invoke.caso (caso_origen) WHERE caso_origen IS NOT NULL;
CREATE INDEX caso_sustituto_idx ON invoke.caso (guia_id, sustituto, grupo_sustituto) WHERE sustituto IS NOT NULL;

-- Las propiedades de identidad del caso no cambian: el caso es un hecho.
CREATE FUNCTION invoke.tg_caso_inmutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.estudio_id <> OLD.estudio_id OR NEW.origen <> OLD.origen
     OR NEW.caso_origen IS DISTINCT FROM OLD.caso_origen
     OR NEW.guia_id <> OLD.guia_id THEN
    RAISE EXCEPTION 'estudio, guía y origen del caso no se modifican';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER caso_inmutable BEFORE UPDATE ON invoke.caso
  FOR EACH ROW EXECUTE FUNCTION invoke.tg_caso_inmutable();

CREATE TABLE invoke.telefono (
  id                  bigserial PRIMARY KEY,
  caso_id             bigint NOT NULL,
  estudio_id          bigint NOT NULL,
  numero              text   NOT NULL,
  numero_normalizado  text   NOT NULL GENERATED ALWAYS AS (invoke.normalizar_telefono(numero)) STORED,
  orden               smallint NOT NULL DEFAULT 1,
  agregado_por        text REFERENCES invoke.usuario,
  agregado_en         timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (caso_id, estudio_id) REFERENCES invoke.caso (id, estudio_id) ON DELETE CASCADE,
  UNIQUE (caso_id, numero_normalizado),
  CHECK (length(invoke.normalizar_telefono(numero)) >= 6)
);
CREATE INDEX telefono_estudio_numero_idx ON invoke.telefono (estudio_id, numero_normalizado);
CREATE INDEX telefono_numero_idx ON invoke.telefono (numero_normalizado);

-- Lista negra transversal a toda la casa (diccionario, decisión 5). La escribe
-- el trigger de eventos cuando un intento termina en 5 "No volver a llamar".
CREATE TABLE invoke.lista_negra (
  numero_normalizado  text PRIMARY KEY,
  desde               timestamptz NOT NULL DEFAULT now(),
  estudio_id          bigint REFERENCES invoke.estudio,
  caso_id             bigint REFERENCES invoke.caso,
  evento_id           bigint
);

-- Cuotas. Tres tipos, como el legacy: por pregunta (código de respuesta),
-- por área y por propiedad de la ficha. Sin contador: el avance es una consulta.
CREATE TABLE invoke.cuota (
  id              bigserial PRIMARY KEY,
  estudio_id      bigint NOT NULL REFERENCES invoke.estudio ON DELETE CASCADE,
  nombre          text   NOT NULL,
  tipo            text   NOT NULL CHECK (tipo IN ('pregunta', 'area', 'propiedad')),
  pregunta        text,
  propiedad       text,
  valores         text[] NOT NULL CHECK (cardinality(valores) > 0),
  objetivo        integer,
  porcentaje      numeric(5,2) CHECK (porcentaje IS NULL OR (porcentaje > 0 AND porcentaje <= 100)),
  activa          boolean NOT NULL DEFAULT true,
  UNIQUE (estudio_id, nombre),
  CHECK ((objetivo IS NULL) <> (porcentaje IS NULL)),
  CHECK (objetivo IS NULL OR objetivo >= 0),
  CHECK (tipo <> 'pregunta'  OR (pregunta IS NOT NULL AND propiedad IS NULL)),
  CHECK (tipo <> 'propiedad' OR (propiedad IS NOT NULL AND pregunta IS NULL)),
  CHECK (tipo <> 'area'      OR (pregunta IS NULL AND propiedad IS NULL))
);

GRANT SELECT, INSERT, UPDATE ON invoke.usuario, invoke.participante, invoke.estudio,
  invoke.franja, invoke.regla_reintento, invoke.guia, invoke.caso, invoke.cuota TO invoke_app;
GRANT DELETE ON invoke.estudio_usuario, invoke.franja, invoke.cuota TO invoke_app;
GRANT SELECT, INSERT ON invoke.estudio_usuario, invoke.telefono TO invoke_app;
GRANT SELECT ON invoke.lista_negra TO invoke_app;
GRANT USAGE ON SEQUENCE invoke.participante_id_seq, invoke.estudio_id_seq, invoke.guia_id_seq,
  invoke.caso_id_seq, invoke.telefono_id_seq, invoke.cuota_id_seq TO invoke_app;
GRANT SELECT ON invoke.estudio, invoke.guia, invoke.caso, invoke.cuota, invoke.usuario,
  invoke.participante TO invoke_lectura;
