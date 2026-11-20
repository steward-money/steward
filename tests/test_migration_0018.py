"""0018 adds institution.dismissed_error (click-through feedback 2026-10-25, section 0), removes the "A bank connection
(MBR-...)" placeholder rows an older sync made for unnamed failing connections and resolves their warnings, keeps every
other row, and downgrades cleanly."""
from alembic import command
from sqlalchemy import inspect, text

from steward.db.engine import make_engine
from steward.db.keys import ensure_key
from steward.db.migrate import _config, run_migrations

PLACEHOLDER = "A bank connection (MBR-1) needs you to sign in again at SimpleFIN"
MARCUS = "Marcus needs you to sign in again at SimpleFIN"


def test_0018_adds_the_dismissed_error_column(settings):
    eng = make_engine(settings.db_path, ensure_key(settings.key_path))
    command.upgrade(_config(eng), "0017")
    before = {c["name"] for c in inspect(eng).get_columns("institution")}
    with eng.begin() as c:
        c.execute(text("INSERT INTO institution (id, name, source, simplefin_org_id, last_error) "
                       f"VALUES (1, 'Marcus', 'simplefin', 'm', '{MARCUS}')"))
    run_migrations(eng)
    assert {c["name"] for c in inspect(eng).get_columns("institution")} == before | {"dismissed_error", "org_id"}
    with eng.connect() as c:
        assert c.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0038"
        assert c.execute(text("SELECT name, dismissed_error FROM institution")).all() == [("Marcus", None)]
    command.downgrade(_config(eng), "0017")
    assert "dismissed_error" not in {c["name"] for c in inspect(eng).get_columns("institution")}
    with eng.connect() as c:
        assert c.execute(text("SELECT last_error FROM institution")).scalar() == MARCUS
    eng.dispose()


def test_0018_removes_the_old_placeholder_connections_and_their_warnings(settings):
    """JR's book: three failing connections SimpleFIN never named, each a Banks row and an open Home warning that
    nothing could clear. A named connection with the same problem is left alone, and so is any row with accounts."""
    eng = make_engine(settings.db_path, ensure_key(settings.key_path))
    command.upgrade(_config(eng), "0017")
    with eng.begin() as c:
        c.execute(text("INSERT INTO institution (id, name, source, simplefin_org_id, last_error) VALUES "
                       f"(1, 'Marcus', 'simplefin', 'm', '{MARCUS}'), "
                       f"(2, 'A bank connection (MBR-1)', 'simplefin', 'MBR-1', '{PLACEHOLDER}'), "
                       "(3, 'A bank connection (MBR-2)', 'simplefin', 'MBR-2', NULL), "
                       "(4, 'A bank connection (with accounts)', 'simplefin', 'MBR-3', NULL)"))
        c.execute(text("INSERT INTO account (id, institution_id, name, type, owner, source, currency, is_active, created_at) "
                       "VALUES (1, 4, 'chk', 'checking', 'joint', 'simplefin', 'USD', 1, '2026-10-20 00:00:00')"))
        c.execute(text("INSERT INTO event_log (id, at, level, kind, message, actionable, resolved_at) VALUES "
                       f"(1, '2026-10-20 00:00:00', 'warn', 'sync', '{PLACEHOLDER}', 1, NULL), "
                       f"(2, '2026-10-20 00:00:00', 'warn', 'sync', '{MARCUS}', 1, NULL), "
                       f"(3, '2026-10-19 00:00:00', 'warn', 'sync', '{PLACEHOLDER}', 1, '2026-10-19 01:00:00')"))
    run_migrations(eng)
    with eng.connect() as c:
        assert c.execute(text("SELECT id, name FROM institution ORDER BY id")).all() == [
            (1, "Marcus"), (4, "A bank connection (with accounts)")]
        resolved = dict(c.execute(text("SELECT id, resolved_at IS NOT NULL FROM event_log")).all())
        assert resolved == {1: 1, 2: 0, 3: 1}
        assert c.execute(text("SELECT resolved_at FROM event_log WHERE id = 3")).scalar() == "2026-10-19 01:00:00"
    eng.dispose()
