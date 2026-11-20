"""0034 (chat turn about): chat_turn gains a NULL about, every turn is kept, and downgrade drops the column."""
from alembic import command
from sqlalchemy import inspect, text

from steward.db.engine import make_engine
from steward.db.keys import ensure_key
from steward.db.migrate import _config, run_migrations

AT = "2026-10-01 00:00:00.000000"


def _at_0033(settings):
    eng = make_engine(settings.db_path, ensure_key(settings.key_path))
    command.upgrade(_config(eng), "0033")
    with eng.begin() as c:
        c.execute(text("INSERT INTO chat_conversation (id, user_id, title, created_at, updated_at, last_input_tokens) "
                       "VALUES (1, 1, 'Dining', :at, :at, 0)"), {"at": AT})
        c.execute(text("INSERT INTO chat_turn (conversation_id, turn_id, user_id, asked, chips, steps, cost_cents, ms, "
                       "store_rows, started_at) VALUES (1, 't_00000000000000a1', 1, 'Are we spending more on dining?', "
                       "'[]', '[]', 0, 0, 0, :at)"), {"at": AT})
    return eng


def test_0034_adds_a_null_about_and_keeps_every_turn(settings):
    eng = _at_0033(settings)
    run_migrations(eng)
    with eng.connect() as c:
        assert c.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0038"
        assert c.execute(text("SELECT turn_id, asked, about FROM chat_turn")).all() == [
            ("t_00000000000000a1", "Are we spending more on dining?", None)]
    eng.dispose()


def test_0034_downgrade_drops_about(settings):
    eng = _at_0033(settings)
    run_migrations(eng)
    with eng.begin() as c:
        c.execute(text("""UPDATE chat_turn SET about = '{"kind":"plan"}'"""))
    command.downgrade(_config(eng), "0033")
    assert "about" not in {x["name"] for x in inspect(eng).get_columns("chat_turn")}
    with eng.connect() as c:
        assert c.execute(text("SELECT count(*) FROM chat_turn")).scalar() == 1
    eng.dispose()
