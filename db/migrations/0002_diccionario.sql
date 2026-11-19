-- 0002 — Diccionario de disposiciones, versión 1 (docs/Diccionario de disposiciones — INVOKE 2.md).
--
-- Los catálogos se versionan: la clave es (version_diccionario, codigo). Cada
-- estudio guarda la versión con la que se levantó y sus reportes se calculan
-- siempre con esa versión, nunca con la última.

CREATE TABLE invoke.version_documento (
  documento   text    NOT NULL CHECK (documento IN ('diccionario', 'formulas')),
  version     integer NOT NULL CHECK (version > 0),
  fecha       date    NOT NULL,
  motivo      text    NOT NULL,
  PRIMARY KEY (documento, version)
);

INSERT INTO invoke.version_documento VALUES
  ('diccionario', 1, '2026-11-18', 'Versión 1: catálogo legacy en producción más códigos 30, 31 y 32'),
  ('formulas',    1, '2026-11-18', 'Versión 1: e = 1, parciales no cuentan, fuera de marco excluido');

-- Comportamientos heredados del legacy.
CREATE TABLE invoke.comportamiento (
  codigo  smallint PRIMARY KEY,
  nombre  text NOT NULL
);
INSERT INTO invoke.comportamiento VALUES
  (10, 'Continúa'), (20, 'Termina'), (21, 'Termina y explica'), (22, 'Termina y agenda');

-- Símbolos de las fórmulas (documento de fórmulas):
--   C  entrevista completa          I  interrumpida no promovida
--   R  rechazo                      NC elegible no contactado o sin cierre
--   AB abandonada por el sistema    NE no elegible
--   UD elegibilidad desconocida
-- grupo_calculo agrupa los símbolos en los cuatro grupos del documento.

-- Versiones del diccionario que tienen catálogo cargado.
CREATE TABLE invoke.version_diccionario (
  version   integer PRIMARY KEY,
  documento text NOT NULL DEFAULT 'diccionario' CHECK (documento = 'diccionario'),
  FOREIGN KEY (documento, version) REFERENCES invoke.version_documento (documento, version)
);
INSERT INTO invoke.version_diccionario (version) VALUES (1);

CREATE TABLE invoke.disposicion_telefono (
  version_diccionario  integer  NOT NULL REFERENCES invoke.version_diccionario,
  codigo               smallint NOT NULL,
  etiqueta             text     NOT NULL,
  comportamiento       smallint NOT NULL REFERENCES invoke.comportamiento,
  estado               text     NOT NULL CHECK (estado IN ('inicial', 'transitorio', 'reintentable', 'final', 'habilita_encuesta', 'revision')),
  cuenta_intento       boolean  NOT NULL,
  es_estado_interno    boolean  NOT NULL DEFAULT false,
  -- Símbolo con el que cierra el caso cuando esta es la última disposición
  -- (por ser final o por tope de intentos). NULL en 4 "Contesta": decide la encuesta.
  simbolo              text     CHECK (simbolo IN ('UD', 'AB')),
  activa_sustituto     boolean  NOT NULL DEFAULT false,
  -- Vista de compatibilidad CDR (ADR-009). NULL = pendiente de completar.
  equivalente_cdr      text,
  sin_equivalente      boolean  NOT NULL DEFAULT false,
  PRIMARY KEY (version_diccionario, codigo),
  CHECK (NOT (sin_equivalente AND equivalente_cdr IS NOT NULL))
);

INSERT INTO invoke.disposicion_telefono
  (version_diccionario, codigo, etiqueta, comportamiento, estado, cuenta_intento, es_estado_interno, simbolo, activa_sustituto, sin_equivalente)
VALUES
  (1,  1, 'Aun no llamado',                                    10, 'inicial',           false, true,  NULL, false, false),
  (1,  2, 'Llamada en curso',                                  10, 'transitorio',       false, true,  NULL, false, false),
  (1,  3, 'Ocupado',                                           20, 'reintentable',      true,  false, 'UD', false, false),
  (1,  4, 'Contesta',                                          10, 'habilita_encuesta', true,  false, NULL, false, false),
  (1,  5, 'No volver a llamar',                                20, 'final',             true,  false, 'UD', false, false),
  (1,  6, 'No contesta',                                       20, 'reintentable',      true,  false, 'UD', false, false),
  (1,  7, 'Fuera de servicio',                                 20, 'reintentable',      true,  false, 'UD', false, false),
  (1,  8, 'Desconectado',                                      20, 'reintentable',      true,  false, 'UD', true,  false),
  (1,  9, 'Contestador telefónico verificado',                 20, 'reintentable',      true,  false, 'UD', false, false),
  (1, 30, 'Abandonada por el sistema',                         20, 'reintentable',      true,  false, 'AB', false, true),
  (1, 31, 'Contestador telefónico detectado por el sistema',   20, 'reintentable',      true,  false, 'UD', false, true),
  -- 32 no la produce una llamada: la produce la revisión posterior de una 31.
  (1, 32, 'Falso positivo de contestador',                     20, 'revision',          true,  false, 'AB', false, true);

CREATE TABLE invoke.disposicion_encuesta (
  version_diccionario  integer  NOT NULL REFERENCES invoke.version_diccionario,
  codigo               smallint NOT NULL,
  etiqueta             text     NOT NULL,
  comportamiento       smallint NOT NULL REFERENCES invoke.comportamiento,
  final                boolean  NOT NULL,
  es_estado_interno    boolean  NOT NULL DEFAULT false,
  extendido            boolean  NOT NULL DEFAULT false,
  -- NULL en estados internos y en los estados extendidos de operativos mixtos,
  -- que las fórmulas v1 no clasifican (se reportan como "sin clasificar").
  simbolo              text     CHECK (simbolo IN ('C', 'I', 'R', 'NC', 'NE')),
  -- Qué hace el caso después: cerrar, volver a la cola o quedar agendado.
  efecto_caso          text     NOT NULL CHECK (efecto_caso IN ('ninguno', 'cerrar', 'reabrir', 'agendar')),
  activa_sustituto     boolean  NOT NULL DEFAULT false,
  visible_encuestador  boolean  NOT NULL DEFAULT true,
  equivalente_cdr      text,
  sin_equivalente      boolean  NOT NULL DEFAULT false,
  PRIMARY KEY (version_diccionario, codigo),
  CHECK (NOT (sin_equivalente AND equivalente_cdr IS NOT NULL))
);

INSERT INTO invoke.disposicion_encuesta
  (version_diccionario, codigo, etiqueta, comportamiento, final, es_estado_interno, extendido, simbolo, efecto_caso, activa_sustituto, visible_encuestador)
VALUES
  (1,  1, 'Disponible',                  10, false, true,  false, NULL, 'ninguno', false, false),
  (1,  2, 'En Progreso',                 10, false, true,  false, NULL, 'ninguno', false, false),
  (1,  3, 'Completa',                    10, true,  false, false, 'C',  'cerrar',  false, true),
  (1,  4, 'Rechazada',                   20, true,  false, false, 'R',  'cerrar',  true,  true),
  (1,  5, 'Agendada',                    22, false, false, false, 'NC', 'agendar', false, true),
  (1,  6, 'Fuera de Cuota',              20, true,  false, false, 'NC', 'cerrar',  false, true),
  (1,  7, 'Interrumpida',                21, true,  false, false, 'I',  'cerrar',  true,  true),
  (1,  8, 'No cumple condición',         21, true,  false, false, 'NE', 'cerrar',  false, true),
  (1, 20, 'Filtrada',                    21, true,  false, false, 'NE', 'cerrar',  false, true),
  (1, 21, 'Fallecido',                   20, true,  false, false, 'NE', 'cerrar',  false, true),
  (1, 22, 'No es un hogar',              20, true,  false, false, 'NE', 'cerrar',  false, true),
  -- 50: las fórmulas v1 no la asignan a un grupo. Provisoriamente NE; ver
  -- docs/decisiones.html, decisión de implementación DI-07.
  (1, 50, 'No aplica',                   21, true,  false, false, 'NE', 'cerrar',  false, true),
  -- Estados extendidos para operativos mixtos (se conservan los seis, decisión 9).
  (1, 13, 'Coordinada',                  20, true,  false, true,  NULL, 'cerrar',  false, false),
  (1, 14, 'Descargada para presencial',  20, true,  false, true,  NULL, 'cerrar',  false, false),
  (1, 15, 'Recall',                      10, false, false, true,  NULL, 'reabrir', false, false),
  (1, 16, 'Retomar',                     10, false, false, true,  NULL, 'reabrir', false, false),
  (1, 17, 'Retomada',                    20, true,  false, true,  NULL, 'cerrar',  false, false),
  (1, 18, 'Corregir',                    10, false, false, true,  NULL, 'reabrir', false, false);

-- Reglas de reintento por defecto (valores legacy). Cada estudio copia estas
-- filas al crearse y las puede cambiar sin tocar código.
CREATE TABLE invoke.regla_reintento_defecto (
  version_diccionario integer  NOT NULL,
  codigo_telefono     smallint NOT NULL,
  espera_min          integer  NOT NULL CHECK (espera_min >= 0),
  tope                integer  CHECK (tope IS NULL OR tope > 0),
  ventana             text     NOT NULL CHECK (ventana IN ('misma', 'rotar', 'cualquiera')),
  PRIMARY KEY (version_diccionario, codigo_telefono),
  FOREIGN KEY (version_diccionario, codigo_telefono) REFERENCES invoke.disposicion_telefono
);

-- Los topes por disposición quedaron "a definir" en el diccionario: se dejan
-- vacíos (sin tope propio) y rige el tope global por caso y por franja del estudio.
INSERT INTO invoke.regla_reintento_defecto VALUES
  (1,  3,   30, NULL, 'misma'),
  (1,  6,  120, NULL, 'rotar'),
  (1,  9,  120, NULL, 'rotar'),
  (1,  7,  180, NULL, 'cualquiera'),
  (1,  8,  180, NULL, 'cualquiera'),
  -- Abandonada: espera de un día y tope 2 (dos abandonos destruyen el caso).
  -- Además nunca vuelve en predictivo: el asignador la ofrece en preview.
  (1, 30, 1440, 2,    'rotar'),
  (1, 31,  120, NULL, 'rotar');
-- 32 no tiene regla: no es resultado de un intento sino de una revisión.

GRANT SELECT ON invoke.version_documento, invoke.version_diccionario, invoke.comportamiento,
  invoke.disposicion_telefono, invoke.disposicion_encuesta, invoke.regla_reintento_defecto
  TO invoke_app, invoke_lectura;
