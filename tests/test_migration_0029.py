"""0029 (smarter patterns): every stored rule pattern is recomputed with the new normalize(); rules that now share a
pattern keep the newest and the others go, each merge logged; a rule whose pattern didn't move is left alone."""
import logging

from alembic import command
from sqlalchemy import text

from steward.db.engine import make_engine
from steward.db.keys import ensure_key
from steward.db.migrate import _config, run_migrations
from steward.services.transactions import normalize


def _merchant(c, rid, pattern, category, at):
    c.execute(text("INSERT INTO merchant_rule (id, pattern, merchant, category, is_business, source, created_at) "
                   "VALUES (:id, :p, :m, :c, NULL, 'correction', :at)"),
              {"id": rid, "p": pattern, "m": pattern.title(), "c": category, "at": at})


def test_0029_recomputes_patterns_and_merges_the_rules_that_meet(settings, caplog):
    eng = make_engine(settings.db_path, ensure_key(settings.key_path))
    command.upgrade(_config(eng), "0028")
    # Patterns as the old normalize() stored them: the trailing number gone, the rest kept.
    with eng.begin() as c:
        _merchant(c, 1, "BETTERMENT SEC TRANSFER ST-A1B2C3D4E5 WEB ID:", "transfer", "2026-08-03 10:00:00.000000")
        _merchant(c, 2, "BETTERMENT SEC TRANSFER ST-K9J8H7G6F5 WEB ID:", "investment", "2026-09-03 10:00:00.000000")
        _merchant(c, 3, "BETTERMENT SEC TRANSFER ST-Q1W2E3R4T5 WEB ID:", "transfer", "2026-07-03 10:00:00.000000")
        _merchant(c, 4, "ZELLE PAYMENT TO JOHN SMITH JPM99ABC123", "gifts", "2026-08-01 10:00:00.000000")
        _merchant(c, 5, "ZELLE PAYMENT TO JANE DOE JPM77QRS456", "childcare", "2026-08-01 10:00:00.000000")
        _merchant(c, 6, "HARBOR GRILL", "dining", "2026-08-01 10:00:00.000000")
        _merchant(c, 7, "ZELLE PAYMENT TO JOHN SMITH", "home", "2026-08-01 10:00:00.000000")   # same time: id 7 wins
        c.execute(text("INSERT INTO reimburser (id, name, created_at) VALUES (1, 'Acme', '2026-08-01 00:00:00'),"
                       " (2, 'Globex', '2026-08-01 00:00:00')"))
        c.execute(text("INSERT INTO reimburse_rule (id, pattern, reimburser_id, created_at) VALUES "
                       "(1, 'UNITED AIR 0162345678901 HOUSTON TX', 1, '2026-08-01 00:00:00'), "
                       "(2, 'UNITED AIR 0169876543210 HOUSTON TX', 2, '2026-09-01 00:00:00'), "
                       "(3, 'HARBOR GRILL', 1, '2026-08-01 00:00:00')"))
        c.execute(text("INSERT INTO institution (id, name, source) VALUES (1, 'Bank', 'manual')"))
        for aid in (1, 2):
            c.execute(text("INSERT INTO account (id, institution_id, name, type, owner, source, currency, is_active, "
                           "created_at) VALUES (:id, 1, 'Card', 'credit_card', 'joint', 'manual', 'USD', 1, "
                           "'2026-01-01 00:00:00')"), {"id": aid})
        for rid, kind, pattern, acct, pct, at in (
                (1, "merchant", "CLOUDHOST INC INV12345", 1, 100, "2026-08-01"),
                (2, "merchant", "CLOUDHOST INC INV67890", 1, 50, "2026-09-01"),
                (3, "merchant", "CLOUDHOST INC INV55555", 2, 100, "2026-08-01"),    # another account: not merged
                (4, "client", "ACME CORP ACH PPD ID:", None, 100, "2026-08-01"),
                (5, "account", None, 2, 100, "2026-08-01")):
            c.execute(text("INSERT INTO business_rule (id, kind, pattern, account_id, pct, line, label, created_at) "
                           "VALUES (:id, :k, :p, :a, :pct, NULL, 'x', :at)"),
                      {"id": rid, "k": kind, "p": pattern, "a": acct, "pct": pct, "at": at + " 00:00:00"})
    with caplog.at_level(logging.INFO, logger="alembic.runtime.migration"):
        run_migrations(eng)
    with eng.connect() as c:
        assert c.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0038"
        merchant = c.execute(text("SELECT id, pattern, category FROM merchant_rule ORDER BY id")).all()
        reimburse = c.execute(text("SELECT id, pattern, reimburser_id FROM reimburse_rule ORDER BY id")).all()
        business = c.execute(text("SELECT id, kind, pattern, account_id, pct FROM business_rule ORDER BY id")).all()
    assert [tuple(r) for r in merchant] == [
        (2, "BETTERMENT SEC TRANSFER", "investment"),        # the newest of the three
        (5, "ZELLE PAYMENT TO JANE DOE", "childcare"),
        (6, "HARBOR GRILL", "dining"),
        (7, "ZELLE PAYMENT TO JOHN SMITH", "home"),          # a tie on time goes to the higher id
    ]
    assert [tuple(r) for r in reimburse] == [(2, "UNITED AIR HOUSTON TX", 2), (3, "HARBOR GRILL", 1)]
    assert [tuple(r) for r in business] == [
        (2, "merchant", "CLOUDHOST INC", 1, 50), (3, "merchant", "CLOUDHOST INC", 2, 100),
        (4, "client", "ACME CORP ACH", None, 100), (5, "account", None, 2, 100)]
    said = " ".join(r.getMessage() for r in caplog.records)
    assert "merchant_rule: 3 rules now share one pattern; kept #2, deleted #" in said
    assert "merchant_rule: 2 rules now share one pattern; kept #7, deleted #" in said
    assert not any(w in said for w in ("BETTERMENT", "ZELLE", "SMITH", "CLOUDHOST")), said   # P10: ids, never patterns
    # Every pattern left is what the app's normalize() makes of it, so every rule can still match.
    assert all(normalize(p) == p for _, p, _ in merchant)
    command.downgrade(_config(eng), "0028")              # nothing to put back; the patterns stay
    with eng.connect() as c:
        assert c.execute(text("SELECT count(*) FROM merchant_rule")).scalar() == 4
    eng.dispose()
