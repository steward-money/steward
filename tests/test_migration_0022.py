"""0022 (roadmap 12): an investment account's trades, fees and reinvestments that nobody sorted become Investment
activity and its dividends Interest, so they leave Review; a person's choices, transfers and other accounts' rows stay."""
from alembic import command
from sqlalchemy import text

from steward.db.engine import make_engine
from steward.db.keys import ensure_key
from steward.db.migrate import _config, run_migrations


def test_0022_sorts_investment_rows_nobody_sorted(settings):
    eng = make_engine(settings.db_path, ensure_key(settings.key_path))
    command.upgrade(_config(eng), "0021")
    with eng.begin() as c:
        c.execute(text("INSERT INTO institution (id, name, source) VALUES (1, 'Robo Invest', 'simplefin')"))
        for aid, name, type_ in ((1, "Taxable", "brokerage_taxable"), (2, "Roth IRA", "roth_ira"), (3, "Checking", "checking")):
            c.execute(text("INSERT INTO account (id, institution_id, name, type, owner, source, currency, is_active, created_at) "
                           "VALUES (:id, 1, :n, :t, 'joint', 'simplefin', 'USD', 1, '2026-01-01 00:00:00')"),
                      {"id": aid, "n": name, "t": type_})
        rows = [
            (1, 1, "BUY VTI 10 SHS", "uncategorized", "none", 0),
            (2, 1, "SELL VXUS 5 SHS", "shopping", "model", 0),
            (3, 1, "DIVIDEND RECEIVED VTI", "uncategorized", "none", 0),
            (4, 1, "REINVEST DIVIDEND VTI", "uncategorized", "none", 0),
            (5, 2, "ADVISORY FEE", "fees", "model", 0),
            (6, 1, "BUY AMZN 2 SHS", "shopping", "rule", 0),                 # the Amazon store rule fired on a trade
            (7, 1, "MY OWN CHOICE", "gifts", "correction", 0),             # a person sorted it
            (8, 2, "TRANSFER FROM CHECKING", "transfer", "rule", 1),       # money put in: stays a transfer
            (9, 3, "COSTBOX WHSE", "uncategorized", "none", 0),            # not an investment account
        ]
        # A dividend is money in; 0023 turns Interest the rule sorted on money out into a Fee, so keep it positive here.
        for tid, aid, raw, cat, src, transfer in rows:
            c.execute(text("INSERT INTO \"transaction\" (id, account_id, posted_at, amount_cents, raw_description, category, "
                           "category_source, is_business, is_transfer, imported_at, business_pct, business_source) VALUES "
                           "(:id, :a, '2026-10-01', :amt, :raw, :cat, :src, 0, :tr, '2026-10-01 00:00:00', 0, 'none')"),
                      {"id": tid, "a": aid, "amt": 100 if cat == "uncategorized" and raw.startswith("DIVIDEND") else -100, "raw": raw, "cat": cat, "src": src, "tr": transfer})
    run_migrations(eng)
    with eng.connect() as c:
        assert c.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0038"
        got = dict(c.execute(text("SELECT id, category FROM \"transaction\"")).all())
    assert got == {1: "investment", 2: "investment", 3: "interest", 4: "investment", 5: "investment", 6: "investment",
                   7: "gifts", 8: "transfer", 9: "uncategorized"}
    command.downgrade(_config(eng), "0021")
    with eng.connect() as c:
        back = dict(c.execute(text("SELECT id, category FROM \"transaction\"")).all())
    assert back[1] == back[2] == back[4] == "uncategorized" and back[7] == "gifts" and back[8] == "transfer"
    eng.dispose()
