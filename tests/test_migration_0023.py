"""0023 (phase 12, Data): guess_category, guess_pct and model_asked_at on transaction; the transaction_note table;
rows the model sorted get model_asked_at = imported_at; interest a card charged (money out, by the built-in rule)
becomes a Fee; the catch-up's old cursor key goes; downgrade puts it back as far as it can."""
from alembic import command
from sqlalchemy import inspect, text

from steward.db.engine import make_engine
from steward.db.keys import ensure_key
from steward.db.migrate import _config, run_migrations

AT = "'2026-10-20 00:00:00.000000'"


def _book(settings):
    eng = make_engine(settings.db_path, ensure_key(settings.key_path))
    command.upgrade(_config(eng), "0022")
    with eng.begin() as c:
        c.execute(text("INSERT INTO institution (id, name, source) VALUES (1, 'Manual', 'manual')"))
        c.execute(text("INSERT INTO account (id, institution_id, name, type, owner, source, currency, is_active, created_at) "
                       f"VALUES (1, 1, 'Card', 'credit', 'joint', 'manual', 'USD', 1, {AT})"))
        rows = [
            (1, -1200, "SQ *VENDOR", "dining", "model"),             # the model sorted it: asked when imported
            (2, -1500, "INTEREST CHARGED ON PURCHASES", "interest", "rule"),   # a card's interest: a fee
            (3, 300, "INTEREST PAID", "interest", "rule"),           # interest earned stays
            (4, -900, "INTEREST CHARGE", "interest", "correction"),  # a person's choice stays
            (5, -500, "POS PURCHASE", "uncategorized", "none"),      # never asked
        ]
        for tid, amount, raw, cat, src in rows:
            c.execute(text("INSERT INTO \"transaction\" (id, account_id, posted_at, amount_cents, raw_description, category, "
                           "category_source, is_business, is_transfer, imported_at, business_pct, business_source) VALUES "
                           f"(:id, 1, '2026-10-01', :amt, :raw, :cat, :src, 0, 0, {AT}, 0, 'none')"),
                      {"id": tid, "amt": amount, "raw": raw, "cat": cat, "src": src})
        c.execute(text(f"INSERT INTO watch_state (key, value, updated_at) VALUES ('catchup_cursor', '5', {AT})"))
        c.execute(text(f"INSERT INTO watch_state (key, value, updated_at) VALUES ('large_cents', '50000', {AT})"))
    return eng


def test_0023_adds_the_columns_and_the_notes_table_and_backfills(settings):
    eng = _book(settings)
    before = set(inspect(eng).get_table_names())
    run_migrations(eng)
    with eng.connect() as c:
        assert c.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0038"
        cols = {col["name"] for col in inspect(eng).get_columns("transaction")}
        assert {"guess_category", "guess_pct", "model_asked_at"} <= cols
        assert set(inspect(eng).get_table_names()) == before | {"transaction_note", "undo_entry", "reimburser", "trip", "reimburse_rule", "reimburse_settlement", "account_feed", "held_feed", "same_account_answer", "feed_txn_alias", "expense_ratio_override", "positions_mapping", "positions_import", "undo_text", "home_visit", "import_set", "import_layout", "import_hint", "statement_stage", "statement_layout"}
        assert {col["name"] for col in inspect(eng).get_columns("transaction_note")} == {
            "id", "transaction_id", "author_id", "author_name", "body", "created_at"}
        assert "ix_transaction_note_transaction_id" in {i["name"] for i in inspect(eng).get_indexes("transaction_note")}
        asked = dict(c.execute(text("SELECT id, model_asked_at FROM \"transaction\"")).all())
        assert asked[1] == "2026-10-20 00:00:00.000000" and asked[2] is None and asked[5] is None
        cats = dict(c.execute(text("SELECT id, category FROM \"transaction\"")).all())
        assert cats == {1: "dining", 2: "fees", 3: "interest", 4: "interest", 5: "uncategorized"}
        assert c.execute(text("SELECT key FROM watch_state ORDER BY key")).scalars().all() == ["large_cents"]
    command.downgrade(_config(eng), "0022")
    with eng.connect() as c:
        cols = {col["name"] for col in inspect(eng).get_columns("transaction")}
        assert not {"guess_category", "guess_pct", "model_asked_at"} & cols
        assert set(inspect(eng).get_table_names()) == before
        assert dict(c.execute(text("SELECT id, category FROM \"transaction\"")).all())[2] == "interest"
    eng.dispose()


def test_a_note_goes_with_its_transaction(settings):
    eng = _book(settings)
    run_migrations(eng)
    with eng.begin() as c:
        c.execute(text(f"INSERT INTO transaction_note (transaction_id, author_id, author_name, body, created_at) "
                       f"VALUES (5, 1, 'JR', 'Paid back by Sam', {AT})"))
        c.execute(text("DELETE FROM \"transaction\" WHERE id = 5"))
        assert c.execute(text("SELECT count(*) FROM transaction_note")).scalar() == 0
    eng.dispose()
