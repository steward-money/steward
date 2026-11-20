"""0021 (roadmap 11, one home value): a book with Profile's home value and no property account gets a manual Home
account holding that value, linked to it; one that already has a property account is linked to it, keeping the newer
value; a book without a home value is untouched; and downgrade removes only what the migration made."""
from alembic import command
from sqlalchemy import text

from steward.db.engine import make_engine
from steward.db.keys import ensure_key
from steward.db.migrate import _config, run_migrations


def _book(settings):
    eng = make_engine(settings.db_path, ensure_key(settings.key_path))
    command.upgrade(_config(eng), "0020")
    return eng


def _fact(c, key, value, as_of):
    c.execute(text("INSERT INTO fact (key, value, as_of, created_at) VALUES (:k, :v, :d, '2026-10-01 00:00:00')"),
              {"k": key, "v": value, "d": as_of})


def test_0021_makes_the_home_account_from_profiles_value(settings):
    """JR's book: the home's value only in Profile, a mortgage from the bank, no property account."""
    eng = _book(settings)
    with eng.begin() as c:
        _fact(c, "home.value_cents", "85000000", "2025-03-01")
        _fact(c, "home.value_cents", "92000000", "2026-09-15")
        c.execute(text("INSERT INTO institution (id, name, source, simplefin_org_id) VALUES (1, 'Big Bank', 'simplefin', 'b')"))
        c.execute(text("INSERT INTO account (id, institution_id, name, type, owner, source, currency, is_active, created_at) "
                       "VALUES (1, 1, 'Mortgage', 'mortgage', 'joint', 'simplefin', 'USD', 1, '2026-10-01 00:00:00')"))
    run_migrations(eng)
    with eng.connect() as c:
        assert c.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0038"
        [(aid, name, type_, owner, source)] = c.execute(text(
            "SELECT id, name, type, owner, source FROM account WHERE type = 'property'")).all()
        assert (name, type_, owner, source) == ("Home", "property", "joint", "manual")
        assert c.execute(text("SELECT as_of, balance_cents FROM balance_snapshot WHERE account_id = :a"), {"a": aid}).all() \
            == [("2026-09-15", 92000000)]
        assert c.execute(text("SELECT value FROM fact WHERE key = 'home.account_id'")).scalar() == str(aid)
    command.downgrade(_config(eng), "0020")
    with eng.connect() as c:
        assert c.execute(text("SELECT count(*) FROM account WHERE type = 'property'")).scalar() == 0
        assert c.execute(text("SELECT count(*) FROM fact WHERE key = 'home.account_id'")).scalar() == 0
        assert c.execute(text("SELECT count(*) FROM fact WHERE key = 'home.value_cents'")).scalar() == 2
    eng.dispose()


def test_0021_links_the_property_account_already_there_and_keeps_the_newer_value(settings):
    """The demo book: a manual Home account as well as the Profile answer. No second Home appears."""
    eng = _book(settings)
    with eng.begin() as c:
        c.execute(text("INSERT INTO institution (id, name, source) VALUES (1, 'Manual', 'manual')"))
        c.execute(text("INSERT INTO account (id, institution_id, name, type, owner, source, currency, is_active, created_at) "
                       "VALUES (7, 1, 'Our house', 'property', 'joint', 'manual', 'USD', 1, '2026-01-01 00:00:00')"))
        c.execute(text("INSERT INTO balance_snapshot (account_id, as_of, balance_cents) VALUES (7, '2026-01-01', 88000000)"))
        _fact(c, "home.value_cents", "92000000", "2026-09-15")
    run_migrations(eng)
    with eng.connect() as c:
        assert c.execute(text("SELECT count(*) FROM account WHERE type = 'property'")).scalar() == 1
        assert c.execute(text("SELECT value FROM fact WHERE key = 'home.account_id'")).scalar() == "7"
        assert c.execute(text("SELECT as_of, balance_cents FROM balance_snapshot WHERE account_id = 7 ORDER BY as_of")).all() \
            == [("2026-01-01", 88000000), ("2026-09-15", 92000000)]
    command.downgrade(_config(eng), "0020")
    with eng.connect() as c:   # the household's own account stays
        assert c.execute(text("SELECT count(*) FROM account WHERE id = 7")).scalar() == 1
        assert c.execute(text("SELECT count(*) FROM fact WHERE key = 'home.account_id'")).scalar() == 0
    eng.dispose()


def test_0021_with_two_properties_links_the_largest_and_makes_no_third(settings):
    eng = _book(settings)
    with eng.begin() as c:
        c.execute(text("INSERT INTO institution (id, name, source) VALUES (1, 'Manual', 'manual')"))
        for aid, name, cents in ((3, "Rental", 40000000), (4, "Our house", 90000000)):
            c.execute(text("INSERT INTO account (id, institution_id, name, type, owner, source, currency, is_active, created_at) "
                           "VALUES (:id, 1, :n, 'property', 'joint', 'manual', 'USD', 1, '2026-01-01 00:00:00')"), {"id": aid, "n": name})
            c.execute(text("INSERT INTO balance_snapshot (account_id, as_of, balance_cents) VALUES (:id, '2026-01-01', :c)"),
                      {"id": aid, "c": cents})
        _fact(c, "home.value_cents", "92000000", "2026-09-15")
    run_migrations(eng)
    with eng.connect() as c:
        assert c.execute(text("SELECT count(*) FROM account WHERE type = 'property'")).scalar() == 2
        assert c.execute(text("SELECT value FROM fact WHERE key = 'home.account_id'")).scalar() == "4"
    eng.dispose()


def test_0021_downgrade_keeps_the_home_account_once_a_balance_was_entered_on_it(settings):
    eng = _book(settings)
    with eng.begin() as c:
        _fact(c, "home.value_cents", "92000000", "2026-09-15")
    run_migrations(eng)
    with eng.begin() as c:
        aid = c.execute(text("SELECT id FROM account WHERE type = 'property'")).scalar()
        c.execute(text("INSERT INTO balance_snapshot (account_id, as_of, balance_cents) VALUES (:a, '2026-10-20', 95000000)"),
                  {"a": aid})
    command.downgrade(_config(eng), "0020")
    with eng.connect() as c:
        assert c.execute(text("SELECT count(*) FROM balance_snapshot WHERE account_id = :a"), {"a": aid}).scalar() == 2
    eng.dispose()


def test_0021_leaves_a_book_without_a_home_value_alone(settings):
    eng = _book(settings)
    run_migrations(eng)
    with eng.connect() as c:
        assert c.execute(text("SELECT count(*) FROM account")).scalar() == 0
        assert c.execute(text("SELECT count(*) FROM fact")).scalar() == 0
    eng.dispose()
