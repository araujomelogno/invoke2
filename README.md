# INVOKE 2

Plataforma CATI propia de Equipos Consultores: encuestadores remotos en el navegador (audio WebRTC incluido), supervisión en vivo,
modo de discado elegible por estudio (manual, preview, power y predictivo) y reportes auditables derivados de un log de eventos inmutable.

- **PRD**: [`specs/INVOKE 2.0 — PRD por fases y decisiones de arquitectura.md`](specs/)
- **Fase 0** (definiciones que la implementación lee literalmente): [`docs/`](docs/)
- **Manual de uso**: [`docs/manual.md`](docs/manual.md)
- **Despliegue**: [`docs/despliegue.md`](docs/despliegue.md)
- **Decisiones** (ADR del PRD + decisiones de implementación + pendientes): [`docs/decisiones.md`](docs/decisiones.md)
- **Reglas para quien desarrolla** (personas o Claude Code): [`CLAUDE.md`](CLAUDE.md)

## Arranque local

```bash
pip install -r requirements-dev.txt
docker compose up -d postgres
export DATABASE_URL=postgresql://postgres:invoke@127.0.0.1:5432/invoke
python scripts/migrar.py --verificar
python scripts/semilla_demo.py
INVOKE_AUTH_DEV=1 python scripts/servidor_local.py
```

Abrir `http://127.0.0.1:8000/?dev_uid=dev-admin` (administración), `?dev_uid=dev-sup` (sala) o `?dev_uid=dev-enc1` (puesto).
Sin Asterisk el puesto funciona salvo el audio, y lo avisa.

## Tests

```bash
INVOKE_TEST_DSN=postgresql://postgres:invoke@127.0.0.1:5432/postgres pytest -q   # dominio, base, API, exportación, accesos
node --test tests/js/*.test.mjs                                                   # motor y expresiones del navegador
tsc -p .                                                                          # tipos del frontend (sin build)
```
