"""0030 (profile): kids' share, living costs and soft removal; setup_step's five sections and resume point; Later for a
week instead of Hide for good; the home linked to the book's only mortgage. Downgrade maps it all back."""
from datetime import datetime, timedelta, timezone

import pytest
from alembic import command
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from steward.db.engine import make_engine
from steward.db.keys import ensure_key
from steward.db.migrate import _config, run_migrations

AT = "2026-10-01 00:00:00.000000"


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _acct(c, aid, name, typ):
    c.execute(text("INSERT INTO account (id, institution_id, name, type, owner, source, currency, is_active, created_at) "
                   "VALUES (:id, 1, :name, :type, 'joint', 'manual', 'USD', 1, :at)"),
              {"id": aid, "name": name, "type": typ, "at": AT})


def _step(c, step, status, uid, day):
    c.execute(text("INSERT INTO setup_step (step, status, user_id, at) VALUES (:s, :st, :u, :at)"),
              {"s": step, "st": status, "u": uid, "at": f"2026-10-0{day} 12:00:00.000000"})


def _seed(c):
    c.execute(text("INSERT INTO institution (id, name, source) VALUES (1, 'Manual', 'manual')"))
    _acct(c, 1, "Home", "property")
    _acct(c, 2, "Mortgage", "mortgage")
    _acct(c, 3, "Fidelity 529", "plan529")
    c.execute(text("INSERT INTO fact (id, key, value, as_of, note, created_at) "
                   "VALUES (1, 'home.account_id', '1', '2026-10-01', NULL, :at)"), {"at": AT})
    c.execute(text("INSERT INTO child (id, label, birth_year, cost_level, account_id, monthly_contribution_cents) "
                   "VALUES (1, 'Ava', 2015, 'private', 3, 30000), (2, 'Ben', 2018, 'in_state_public', NULL, 0)"))
    for step, status, uid, day in (("about", "done", 1, 2), ("pay", "skipped", 2, 5), ("children", "done", 1, 2),
                                   ("home", "doesnt_apply", 1, 2), ("health", "done", 1, 3), ("earnings", "done", 2, 4),
                                   ("banks", "skipped", 1, 6)):
        _step(c, step, status, uid, day)
    c.execute(text("INSERT INTO user_setup (user_id, seen_at, confirmed_at, home_hidden_at) VALUES "
                   "(1, :at, NULL, NULL), (2, :at, :at, '2026-10-07 12:00:00.000000')"), {"at": AT})


def test_0030_maps_the_steps_keeps_every_kid_whole_and_links_the_home(settings):
    eng = make_engine(settings.db_path, ensure_key(settings.key_path))
    command.upgrade(_config(eng), "0029")
    with eng.begin() as c:
        _seed(c)
    before = _utcnow()
    run_migrations(eng)
    with eng.connect() as c:
        assert c.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0038"
        assert [tuple(r) for r in c.execute(text(
            "SELECT id, label, birth_year, cost_level, account_id, monthly_contribution_cents, share_pct, covers_living, "
            "removed_at FROM child ORDER BY id"))] == [
            (1, "Ava", 2015, "private", 3, 30000, 100, 1, None), (2, "Ben", 2018, "in_state_public", None, 0, 100, 1, None)]
        assert [tuple(r) for r in c.execute(text(
            "SELECT step, status, user_id, substr(at, 1, 10), resume_step FROM setup_step ORDER BY step"))] == [
            ("banks", "skipped", 1, "2026-10-06", None),
            ("benefits", "done", 2, "2026-10-04", None),      # health and earnings both done; the newest answer's who
            ("kids", "done", 1, "2026-10-02", None),
            ("you", "skipped", 2, "2026-10-05", None)]        # pay was skipped, so You and your partner is
        assert c.execute(text("SELECT value, note FROM fact WHERE key = 'home.owns'")).one() == ("no", "From setup's Doesn't apply")
        later = dict(c.execute(text("SELECT user_id, later_until FROM user_setup")).all())
        assert later[1] is None
        week = datetime.fromisoformat(str(later[2]))
        assert before + timedelta(days=7) - timedelta(minutes=1) <= week <= _utcnow() + timedelta(days=7)
        assert c.execute(text("SELECT loan_id FROM account WHERE id = 1")).scalar() == 2   # the only mortgage
    assert "home_hidden_at" not in {x["name"] for x in inspect(eng).get_columns("user_setup")}
    eng.dispose()


def test_0030_constraints_and_foreign_keys_hold_after_the_rebuild(settings):
    eng = make_engine(settings.db_path, ensure_key(settings.key_path))
    command.upgrade(_config(eng), "0029")
    with eng.begin() as c:
        _seed(c)
    run_migrations(eng)
    with eng.begin() as c:
        c.execute(text("INSERT INTO child (label, birth_year, cost_level, account_id, monthly_contribution_cents, share_pct, "
                       "covers_living) VALUES ('Cy', 2020, 'two_year', NULL, 0, 50, 0)"))
        c.execute(text("INSERT INTO setup_step (step, status, user_id, at, resume_step) "
                       "VALUES ('home', NULL, 1, :at, 'mortgages')"), {"at": AT})
    for sql in ("INSERT INTO child (label, birth_year, cost_level, monthly_contribution_cents, share_pct) VALUES ('D', 2020, 'private', 0, 15)",
                "INSERT INTO child (label, birth_year, cost_level, monthly_contribution_cents) VALUES ('D', 2020, 'ivy', 0)",
                "INSERT INTO setup_step (step, status, user_id, at) VALUES ('about', 'done', 1, '2026-10-01 00:00:00')",
                "INSERT INTO child (label, birth_year, cost_level, account_id, monthly_contribution_cents) VALUES ('D', 2020, 'private', 999, 0)"):
        with eng.connect() as c:
            assert c.execute(text("PRAGMA foreign_keys")).scalar() == 1     # the 891c4e5 regression: still enforced
            with pytest.raises(IntegrityError):
                c.execute(text(sql))
                c.commit()
    eng.dispose()


def test_0030_downgrade_maps_back_and_is_lossy_only_for_share_and_living(settings):
    eng = make_engine(settings.db_path, ensure_key(settings.key_path))
    command.upgrade(_config(eng), "0029")
    with eng.begin() as c:
        _seed(c)
    run_migrations(eng)
    with eng.begin() as c:
        c.execute(text("INSERT INTO child (id, label, birth_year, cost_level, monthly_contribution_cents, share_pct) "
                       "VALUES (3, 'Cy', 2020, 'two_year', 0, 50), (4, 'Dee', 2021, 'not_sure', 0, 100)"))
        c.execute(text("UPDATE child SET removed_at = :at WHERE id = 2"), {"at": AT})
        c.execute(text("INSERT INTO setup_step (step, status, user_id, at, resume_step) VALUES ('home', NULL, 1, :at, 'cars')"),
                  {"at": AT})
    command.downgrade(_config(eng), "0029")
    with eng.connect() as c:
        assert [tuple(r) for r in c.execute(text("SELECT id, cost_level FROM child ORDER BY id"))] == [
            (1, "private"), (3, "in_state_public"), (4, "in_state_public")]           # the removed kid is gone
        assert [tuple(r) for r in c.execute(text("SELECT step, status FROM setup_step ORDER BY step"))] == [
            ("about", "skipped"), ("banks", "skipped"), ("children", "done"), ("health", "done")]
        assert c.execute(text("SELECT home_hidden_at IS NOT NULL FROM user_setup WHERE user_id = 2")).scalar() == 1
        assert c.execute(text("SELECT count(*) FROM fact WHERE key = 'home.owns'")).scalar() == 0
        assert c.execute(text("SELECT loan_id FROM account WHERE id = 1")).scalar() is None
    assert {x["name"] for x in inspect(eng).get_columns("child")} == {
        "id", "label", "birth_year", "cost_level", "account_id", "monthly_contribution_cents"}
    assert {x["name"] for x in inspect(eng).get_columns("setup_step")} == {"step", "status", "user_id", "at"}
    eng.dispose()
