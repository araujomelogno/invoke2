-- 0009 — Códec por encuestador (encargo de cierre D1 + H4) y grabación de todas
-- las llamadas con subida al bucket (H2).

-- ── D1: G.711 extremo a extremo por defecto ──
-- 0005 dejó 'alaw,ulaw,opus' como valor por defecto: todos los puestos negociaban
-- Opus (Chrome lo ofrece primero) y Asterisk transcodificaba en todas las llamadas.
ALTER TABLE asterisk.ps_endpoints ALTER COLUMN allow SET DEFAULT 'alaw,ulaw';
UPDATE asterisk.ps_endpoints SET allow = 'alaw,ulaw' WHERE allow = 'alaw,ulaw,opus';

-- ── H4: Opus habilitable por encuestador ──
-- Vive en el participante (ADR-007): es una propiedad del puesto, no del usuario.
-- credenciales.rotar_sip escribe ps_endpoints.allow según este campo.
ALTER TABLE invoke.participante ADD COLUMN opus_habilitado boolean NOT NULL DEFAULT false;
COMMENT ON COLUMN invoke.participante.opus_habilitado IS
  'Si el puesto acepta Opus además de G.711. Cada puesto con Opus es transcodificación permanente en Asterisk.';

-- ── H2: se graban todas las llamadas de encuesta ──
COMMENT ON COLUMN invoke.estudio.muestreo_grabacion IS
  'SIN USO desde 0009: se graban todas las llamadas de encuesta conectadas (encargo de cierre, H2). Se conserva por compatibilidad.';

-- Nuevo hecho: la grabación quedó en el bucket (y se borró del disco del PBX).
ALTER TABLE invoke.evento DROP CONSTRAINT evento_tipo_check;
ALTER TABLE invoke.evento ADD CONSTRAINT evento_tipo_check CHECK (tipo IN (
  'muestra_cargada', 'caso_creado', 'telefono_agregado',
  'caso_asignado', 'caso_liberado',
  'llamada_originada', 'llamada_timbrando', 'llamada_atendida', 'amd_resultado',
  'llamada_conectada', 'llamada_abandonada', 'llamada_descartada', 'llamada_finalizada',
  'grabacion', 'grabacion_subida', 'disposicion_telefono', 'reclasificacion_amd',
  'encuesta_iniciada', 'pantalla', 'disposicion_encuesta', 'reagendada',
  'sesion_iniciada', 'estado_participante', 'sesion_finalizada',
  'audio_conectado', 'audio_desconectado', 'prueba_audio',
  'escucha_iniciada', 'escucha_finalizada',
  'caso_cerrado', 'pacer_ajuste', 'estudio_modificado', 'cuota_excedida'));
CREATE UNIQUE INDEX evento_grabacion_subida_unica ON invoke.evento (llamada_id) WHERE tipo = 'grabacion_subida';

-- Grabaciones pendientes de subir: terminadas en Asterisk y sin 'grabacion_subida'.
-- La consume services/ari, que es el único que habla con Asterisk (ADR-003).
CREATE VIEW invoke.grabacion_pendiente AS
  SELECT g.llamada_id, g.estudio_id, g.caso_id, g.datos ->> 'archivo' AS archivo, g.ocurrido_en
  FROM invoke.evento g
  WHERE g.tipo = 'grabacion'
    AND NOT EXISTS (SELECT 1 FROM invoke.evento s WHERE s.tipo = 'grabacion_subida' AND s.llamada_id = g.llamada_id);

-- Dónde quedó cada grabación (la última subida por llamada).
CREATE VIEW invoke.grabacion_en_bucket AS
  SELECT llamada_id, estudio_id, caso_id, datos ->> 'uri' AS uri, (datos ->> 'bytes')::bigint AS bytes,
         datos ->> 'md5' AS md5, ocurrido_en AS subida_en
  FROM invoke.evento WHERE tipo = 'grabacion_subida';

GRANT SELECT ON invoke.grabacion_pendiente, invoke.grabacion_en_bucket TO invoke_app, invoke_lectura;
