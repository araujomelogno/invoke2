-- 0001 — Esquema, roles y privilegios por defecto.
--
-- Regla dura (CLAUDE.md): ninguna migración otorga privilegios a PUBLIC, y todo
-- GRANT está listado en db/accesos_esperados.txt. scripts/verificar_accesos.py
-- falla si la base tiene un privilegio que no figura ahí.

CREATE SCHEMA IF NOT EXISTS invoke;

DO $$
BEGIN
  -- Rol de la aplicación: Cloud Functions y servicios de Cloud Run.
  CREATE ROLE invoke_app NOLOGIN;
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$
BEGIN
  -- Lectura de reportes y de la vista de compatibilidad CDR (tablero de productividad).
  CREATE ROLE invoke_lectura NOLOGIN;
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$
BEGIN
  -- Asterisk realtime (res_config_pgsql): solo lee endpoints PJSIP.
  CREATE ROLE asterisk_rt NOLOGIN;
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

REVOKE ALL ON SCHEMA public FROM PUBLIC;
REVOKE ALL ON SCHEMA invoke FROM PUBLIC;
GRANT USAGE ON SCHEMA invoke TO invoke_app, invoke_lectura;

-- Las funciones nuevas no son ejecutables por PUBLIC salvo GRANT explícito.
-- (La forma "IN SCHEMA" no alcanza: el EXECUTE a PUBLIC es un default global.)
ALTER DEFAULT PRIVILEGES REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;

-- Normalización de teléfonos al número nacional significativo de Uruguay: solo
-- dígitos, sin prefijo internacional y sin el 0 de larga distancia/móvil.
-- "099 123 456", "+598 99 123 456" y "0059899123456" dan "99123456".
-- Se usa para lista negra, duplicados y equivalencias entre casos.
CREATE FUNCTION invoke.normalizar_telefono(numero text) RETURNS text
LANGUAGE sql IMMUTABLE STRICT AS $$
  SELECT ltrim(CASE
    WHEN d LIKE '00598%' THEN substr(d, 6)
    WHEN d LIKE '598%' AND length(d) > 9 THEN substr(d, 4)
    ELSE d
  END, '0')
  FROM (SELECT regexp_replace(numero, '[^0-9]', '', 'g') AS d) s
$$;
GRANT EXECUTE ON FUNCTION invoke.normalizar_telefono(text) TO invoke_app;
