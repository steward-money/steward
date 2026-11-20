"""0014 adds the business tag columns, business_rule and business_state (business plan Task 1), turns every row that
was already business into 100% business chosen by hand, and changes nothing else."""
from alembic import command
from sqlalchemy import inspect, text

from steward.db.engine import make_engine
from steward.db.keys import ensure_key
from steward.db.migrate import _config, run_migrations

NEW_COLUMNS = {"business_pct", "business_source", "business_line", "business_hint", "business_answered_at", "guess_category", "guess_pct", "model_asked_at", "reimburser_id", "trip_id", "reimburse_source", "feed_id", "nickname", "statement_page"}


def test_0014_adds_business_and_downgrades_cleanly(settings):
    eng = make_engine(settings.db_path, ensure_key(settings.key_path))
    command.upgrade(_config(eng), "0013")
    before = set(inspect(eng).get_table_names())
    cols_before = {c["name"] for c in inspect(eng).get_columns("transaction")}
    with eng.begin() as c:
        c.execute(text("INSERT INTO institution (id, name, source) VALUES (1, 'M', 'manual')"))
        c.execute(text("INSERT INTO account (id, institution_id, name, type, owner, source, currency, is_active, "
                       "created_at) VALUES (1, 1, 'A', 'checking', 'joint', 'manual', 'USD', 1, '2026-01-01 00:00:00')"))
        for tid, biz in ((1, 0), (2, 1)):
            c.execute(text("INSERT INTO \"transaction\" (id, account_id, posted_at, amount_cents, raw_description, "
                           "category, is_business, is_transfer, category_source, imported_at) VALUES "
                           f"({tid}, 1, '2026-01-02', -1000, 'COWORK SPACE LLC', 'business_expense', {biz}, 0, "
                           "'rule', '2026-01-02 00:00:00')"))
        # Ruling I1: rows on a business-type account, or one owned by Business, become business by account.
        for aid, typ, owner in ((2, "business_checking", "jr"), (3, "checking", "business")):
            c.execute(text("INSERT INTO account (id, institution_id, name, type, owner, source, currency, is_active, "
                           f"created_at) VALUES ({aid}, 1, 'B{aid}', '{typ}', '{owner}', 'manual', 'USD', 1, "
                           "'2026-01-01 00:00:00')"))
        for tid, aid, xfer in ((3, 2, 0), (4, 2, 1), (5, 3, 0)):
            c.execute(text("INSERT INTO \"transaction\" (id, account_id, posted_at, amount_cents, raw_description, "
                           "category, is_business, is_transfer, category_source, imported_at) VALUES "
                           f"({tid}, {aid}, '2026-01-03', -2000, 'NOTEFLOW PRO', 'subscriptions', 0, {xfer}, "
                           "'rule', '2026-01-02 00:00:00')"))
    run_migrations(eng)
    assert set(inspect(eng).get_table_names()) == before | {"business_rule", "business_state", "care_setup", "care_quote", "care_scenario", "tax_state", "spending_target", "spending_prefs", "set_aside", "set_aside_match", "set_aside_dismissed", "transaction_note", "undo_entry", "reimburser", "trip", "reimburse_rule", "reimburse_settlement", "account_feed", "held_feed", "same_account_answer", "feed_txn_alias", "expense_ratio_override", "positions_mapping", "positions_import", "undo_text", "home_visit", "import_set", "import_layout", "import_hint", "statement_stage", "statement_layout"}
    assert {c["name"] for c in inspect(eng).get_columns("transaction")} == cols_before | NEW_COLUMNS
    with eng.connect() as c:
        assert c.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0038"
        rows = c.execute(text("SELECT id, business_pct, business_source, business_line, business_hint, is_business "
                              "FROM \"transaction\" ORDER BY id")).all()
    assert [tuple(r) for r in rows] == [(1, 0, "none", None, None, 0), (2, 100, "manual", None, None, 1),
                                        (3, 100, "account", None, None, 1), (4, 0, "none", None, None, 0),
                                        (5, 100, "account", None, None, 1)]
    assert {c["name"] for c in inspect(eng).get_columns("business_rule")} == {
        "id", "kind", "pattern", "account_id", "pct", "line", "label", "created_at", "created_by"}
    assert {c["name"] for c in inspect(eng).get_columns("business_state")} == {"key", "value", "updated_at", "updated_by"}
    command.downgrade(_config(eng), "0013")
    with eng.connect() as c:   # the inverse: only the rows a person marked stay business
        assert [tuple(r) for r in c.execute(text('SELECT id, is_business FROM "transaction" ORDER BY id')).all()] == [
            (1, 0), (2, 1), (3, 0), (4, 0), (5, 0)]
    assert set(inspect(eng).get_table_names()) == before
    assert {c["name"] for c in inspect(eng).get_columns("transaction")} == cols_before
    eng.dispose()


def test_0014_leaves_a_parent_books_business_owner_alone(settings):
    eng = make_engine(settings.db_path, ensure_key(settings.key_path))
    command.upgrade(_config(eng), "0013")
    with eng.begin() as c:
        c.execute(text("INSERT INTO institution (id, name, source) VALUES (1, 'M', 'manual')"))
        c.execute(text("INSERT INTO account (id, institution_id, name, type, owner, source, currency, is_active, "
                       "created_at) VALUES (1, 1, 'A', 'checking', 'business', 'manual', 'USD', 1, "
                       "'2026-01-01 00:00:00')"))
        c.execute(text('INSERT INTO "transaction" (id, account_id, posted_at, amount_cents, raw_description, '
                       "category, is_business, is_transfer, category_source, imported_at) VALUES "
                       "(1, 1, '2026-01-02', -1000, 'NOTEFLOW PRO', 'subscriptions', 0, 0, 'rule', "
                       "'2026-01-02 00:00:00')"))
    run_migrations(eng, household=False)
    with eng.connect() as c:
        row = c.execute(text('SELECT business_pct, business_source, is_business FROM "transaction"')).one()
    assert tuple(row) == (0, "none", 0)
    eng.dispose()
