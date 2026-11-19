-- Roles con LOGIN para producción. NO es una migración: lo corre una vez quien
-- administra Cloud SQL, con las claves de Secret Manager (psql -v ...).
--   psql "$ADMIN_DSN" -v clave_api="'...'" -v clave_ast="'...'" -v clave_tab="'...'" -f db/roles_produccion.sql
CREATE ROLE invoke_api LOGIN PASSWORD :clave_api IN ROLE invoke_app;
CREATE ROLE asterisk_rt_login LOGIN PASSWORD :clave_ast IN ROLE asterisk_rt;
CREATE ROLE invoke_tablero LOGIN PASSWORD :clave_tab IN ROLE invoke_lectura;
ALTER ROLE invoke_api SET role = 'invoke_app';
ALTER ROLE asterisk_rt_login SET role = 'asterisk_rt';
ALTER ROLE invoke_tablero SET role = 'invoke_lectura';
