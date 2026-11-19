"""Las migraciones no abren accesos de más (CLAUDE.md, regla dura)."""
from verificar_accesos import verificar


def test_accesos(dsn_base):
    assert verificar(dsn_base) == []
