"""The Look into list, the Home tab count and the flag page (spec 4.2; README screens 1-3 and 7; decisions 3, 5, 9)."""
import json
import logging
import re
import unicodedata
from datetime import timedelta

import pytest
from sqlalchemy import select

from steward.db.models import Flag, Institution, InventoryItem, SimplefinLink, Transaction, WatchKnown
from steward.services.accounts import record_balance
from steward.watch.run import run_daily, run_watch
from steward.watch.store import add_flag
from tests.factories import login
from tests.watch_helpers import NOW, TODAY, account, history, txn

BOOK = "/api/b/margaret-ellis"


@pytest.fixture
def at_now(monkeypatch):
    """Pin steward.deps.now to Mon Oct 19, 2026, 6:12 AM Pacific. List it before logging in."""
    monkeypatch.setattr("steward.clock.now", lambda: NOW)
    return NOW


def _cvs(mdb):
    """The mockup's gift card, with three earlier CVS purchases, on a joint account."""
    chk = account(mdb, "Joint checking")
    card = account(mdb, "Citi card", "credit_card", owner="margaret", institution="Citi")
    history(mdb, chk)
    txn(mdb, chk, 17, -2347, "CVS/PHARMACY #04411 BREA CA", watched=True)
    txn(mdb, chk, 35, -6135, "CVS/PHARMACY #04411 BREA CA", watched=True)
    txn(mdb, card, 51, -820, "CVS/PHARMACY #04411 BREA CA", watched=True)
    gift = txn(mdb, chk, 2, -50_000, "CVS/PHARMACY #09812 GIFT CARD FULLERTON CA", category="shopping")
    run_watch(mdb, TODAY, NOW, after="sync")
    mdb.commit()
    return chk, gift, mdb.scalar(select(Flag).where(Flag.transaction_id == gift.id))


def _normal(mdb, n=1, status="open", **kw):
    out = []
    for i in range(n):
        f = add_flag(mdb, check="monthly", severity="normal", summary=f"StreamBox, $15.99 on Sep 18 and Oct 18 ({i})",
                     title="StreamBox, $15.99 a month", facts={"amount_cents": 1599, "account": "Citi card",
                                                               "posted_at": "2026-10-18"},
                     now=NOW - timedelta(minutes=n - i), after="sync", **kw)
        if status != "open":
            f.status, f.resolved_at = status, NOW - timedelta(minutes=n - i)
        out.append(f)
    mdb.commit()
    return out


def test_the_list_puts_urgent_first_and_counts_both_filters(at_now, margaret, mdb, client):
    chk, gift, scam = _cvs(mdb)
    [streambox] = _normal(mdb)
    [done] = _normal(mdb, status="handled")
    login(client, "jr")
    r = client.get(f"{BOOK}/flags")
    assert r.status_code == 200
    body = r.json()
    assert [f["id"] for f in body["flags"]] == [scam.id, streambox.id]
    assert body["counts"] == {"open": 2, "done": 1}
    assert body["checks"] == [{"check": "scam", "label": "Scam pattern", "count": 1},
                              {"check": "monthly", "label": "New monthly charge", "count": 1}]
    assert body["accounts"] == [{"id": chk.id, "name": "Joint checking"}]
    assert body["ran"]["short"] == "Checked today, 6:12 AM"
    assert body["ran"]["line"] == "The checks ran today at 6:12 AM, after the bank feed sync."
    assert body["mail_ready"] is True and body["can_edit"] is True
    row = body["flags"][0]
    assert {k: row[k] for k in ("check_label", "icon", "urgent", "status_label", "summary", "place", "when",
                                "flagged", "amount_cents")} == {
        "check_label": "Scam pattern", "icon": "octagon-alert", "urgent": True, "status_label": "Open",
        "summary": "$500.00 gift card at CVS Pharmacy", "place": "Joint checking", "when": "Sat, Oct 17",
        "flagged": "Flagged Oct 19", "amount_cents": 50_000}
    assert [f["id"] for f in client.get(f"{BOOK}/flags", params={"check": "monthly"}).json()["flags"]] == [streambox.id]
    assert [f["id"] for f in client.get(f"{BOOK}/flags", params={"show": "done"}).json()["flags"]] == [done.id]
    assert client.get(f"{BOOK}/flags", params={"show": "everything"}).status_code == 422
    assert client.get(f"{BOOK}/flags", params={"check": "nope"}).status_code == 422


def test_done_flags_are_kept_with_show_older(at_now, margaret, mdb, client):
    _normal(mdb, n=52, status="expected")
    login(client, "jr")
    first = client.get(f"{BOOK}/flags", params={"show": "done"}).json()
    assert len(first["flags"]) == 50 and first["next_offset"] == 50
    older = client.get(f"{BOOK}/flags", params={"show": "done", "offset": 50}).json()
    assert len(older["flags"]) == 2 and older["next_offset"] is None


def test_the_home_tab_count_includes_still_checking_and_shows_urgent(at_now, margaret, mdb, client):
    _, _, scam = _cvs(mdb)
    [streambox] = _normal(mdb)
    streambox.status = "checking"
    mdb.commit()
    login(client, "jr")
    assert client.get(f"{BOOK}/flags/count").json() == {"count": 2, "urgent": True}
    scam.status, scam.resolved_at = "handled", NOW
    mdb.commit()
    assert client.get(f"{BOOK}/flags/count").json() == {"count": 1, "urgent": False}
    login(client, "vee")
    assert client.get(f"{BOOK}/flags/count").json() == {"count": 1, "urgent": False}   # viewers see it too


def test_the_flag_page_reads_like_the_mockup(at_now, margaret, mdb, client):
    chk, gift, scam = _cvs(mdb)
    login(client, "jr")
    page = client.get(f"{BOOK}/flags/{scam.id}").json()
    assert page["title"] == "$500.00 gift card at CVS Pharmacy"
    assert page["fact"] == "$500.00 gift card at CVS Pharmacy, from Joint checking."
    assert page["why"].startswith("This check flags every gift card, wire transfer, crypto exchange")
    assert page["also"] == [{"check": "large", "label": "Large amount", "icon": "banknote", "detail": "$500.00 or more"}]
    assert page["transaction"] == {
        "removed": False, "id": gift.id, "payee": "CVS Pharmacy", "amount_cents": 50_000,
        "date_label": "Sat, Oct 17, 2026", "account": "Joint checking", "joint_names": "Margaret and Robert (husband)",
        "raw": "CVS/PHARMACY #•9812 GIFT CARD FULLERTON CA", "category_label": "Shopping"}
    assert page["earlier"] == {
        "heading": "Earlier at CVS Pharmacy",
        "line": "3 purchases in the last 12 months, from $8.20 to $61.35. None were gift cards.",
        "rows": [{"name": "CVS Pharmacy", "amount_cents": 2347, "meta": "Oct 2 · Joint checking"},
                 {"name": "CVS Pharmacy", "amount_cents": 6135, "meta": "Sep 14 · Joint checking"},
                 {"name": "CVS Pharmacy", "amount_cents": 820, "meta": "Aug 29 · Citi card"}]}
    item_id = mdb.scalar(select(InventoryItem.id).where(InventoryItem.account_id == chk.id))
    assert page["related"]["id"] == item_id and page["related"]["name"] == "Joint checking"
    assert page["expected"]["scopes"] == [] and page["expected"]["fixed"].startswith("This marks only this flag.")
    assert [(h["icon"], h["text"]) for h in page["history"]] == [("flag", "Flagged by the checks after the bank feed sync")]
    assert page["can_edit"] is True and page["viewer_line"] is None and page["known"] is None


def test_a_flag_whose_transaction_was_removed_still_opens(at_now, margaret, mdb, client):
    _, gift, scam = _cvs(mdb)
    mdb.delete(mdb.get(Transaction, gift.id))
    mdb.commit()
    login(client, "jr")
    r = client.get(f"{BOOK}/flags/{scam.id}")
    assert r.status_code == 200
    t = r.json()["transaction"]
    assert (t["removed"], t["payee"], t["amount_cents"], t["raw"]) == (
        True, "CVS Pharmacy", 50_000, "CVS/PHARMACY #•9812 GIFT CARD FULLERTON CA")


def test_marking_through_the_api(at_now, margaret, mdb, client):
    _, _, scam = _cvs(mdb)
    login(client, "jr")
    r = client.post(f"{BOOK}/flags/{scam.id}/handled", json={})
    assert (r.status_code, r.json()) == (422, {"error": "Add a note saying what was done."})
    r = client.post(f"{BOOK}/flags/{scam.id}/checking", json={"note": "Calling Margaret this morning."})
    assert r.status_code == 200 and r.json()["status"] == "checking" and r.json()["by"] == "Jr, Oct 19"
    assert r.json()["note"] == "Calling Margaret this morning." and r.json()["note_by"] == "Jr"
    assert client.post(f"{BOOK}/flags/{scam.id}/note", json={"note": "Left a message."}).json()["note"] == "Left a message."
    r = client.post(f"{BOOK}/flags/{scam.id}/expected", json={"note": "A birthday present."})
    assert r.status_code == 200 and r.json()["done_line"] == "Marked Expected." and r.json()["done"] is True
    assert r.json()["expected"] is None
    assert client.post(f"{BOOK}/flags/{scam.id}/checking", json={}).status_code == 409
    r = client.post(f"{BOOK}/flags/{scam.id}/reopen", json={})
    assert (r.status_code, r.json()["status"], r.json()["done_line"]) == (200, "open", "Reopened.")
    texts = [h["text"] for h in client.get(f"{BOOK}/flags/{scam.id}").json()["history"]]
    # people's lines, newest first (the "flagged" line's time is the pinned NOW, so it may sort either side of them)
    assert [t for t in texts if t.startswith("Jr")] == ["Jr reopened it", "Jr marked Expected", "Jr changed the note",
                                                        "Jr marked Still checking"]
    assert client.get(f"{BOOK}/flags/999999").status_code == 404


def test_a_flag_raised_under_an_old_payee_key_is_marked_by_the_store_its_row_names_now(at_now, margaret, mdb, client):
    """Public issue #49: a flag raised before the parser read Wells Fargo's card lines by their store keeps the payee
    "Authorized On" in its facts. Its page, its Mark Expected sheet and the Known payee Expected adds name the store
    the row gives now, so the store's next payment is covered and its earlier payments are found."""
    chk = account(mdb, "Joint checking")
    history(mdb, chk)
    wf = "PURCHASE AUTHORIZED ON {} STARBUCKS STORE 05812 SEATTLE WA S382074123456789 CARD 1234"
    txn(mdb, chk, 40, -2_350, wf.format("09/09"), watched=True)
    txn(mdb, chk, 30, -1_200, "PURCHASE AUTHORIZED ON 09/19 SHELL OIL 57444212400 HOUSTON TX P000000381234567 CARD 1234",
        watched=True)
    big = txn(mdb, chk, 2, -60_000, wf.format("10/17"), watched=True)
    old = {"payee": "Authorized On", "payee_key": "AUTHORIZED ON", "method": "card", "amount_cents": 60_000,
           "account": "Joint checking", "posted_at": big.posted_at.isoformat(), "large_cents": 50_000}
    f = add_flag(mdb, check="large", severity="normal", summary="$600.00 to Authorized On",
                 title="$600.00 to Authorized On", facts=old, now=NOW, after="sync", transaction_id=big.id,
                 account_id=chk.id)
    mdb.commit()
    login(client, "jr")
    page = client.get(f"{BOOK}/flags/{f.id}").json()
    assert page["transaction"]["payee"] == "Starbucks Store"
    assert page["earlier"]["heading"] == "Earlier at Starbucks Store"
    assert page["earlier"]["line"] == "1 purchase in the last 12 months, $23.50."      # not the Shell line
    assert page["expected"]["scopes"][0]["line"].startswith("Starbucks Store, $450 to $750.")
    r = client.post(f"{BOOK}/flags/{f.id}/expected", json={"scope": "about"})
    assert r.json()["done_line"] == "Marked Expected. Starbucks Store, $450 to $750, added to Known payees."
    mdb.commit()      # end this session's read so it sees the API's write
    [k] = mdb.scalars(select(WatchKnown)).all()
    assert (k.payee_key, k.payee_name) == ("STARBUCKS STORE", "Starbucks Store")
    nxt = txn(mdb, chk, 0, -58_000, wf.format("10/19"))
    run_watch(mdb, TODAY, NOW, after="sync")
    assert mdb.scalar(select(Flag).where(Flag.transaction_id == nxt.id)) is None     # the Known payee covers it


def test_viewers_read_flags_but_cannot_mark_them(at_now, margaret, mdb, client):
    _, _, scam = _cvs(mdb)
    login(client, "vee")
    page = client.get(f"{BOOK}/flags/{scam.id}").json()
    assert page["can_edit"] is False
    assert page["viewer_line"] == "Only managers can mark flags. Emails for this book are set in Settings → Watching."
    assert client.get(f"{BOOK}/flags").json()["mail_ready"] is None
    r = client.post(f"{BOOK}/flags/{scam.id}/expected", json={})
    assert (r.status_code, r.json()) == (403, {"error": "You can see this book but not change it."})


def test_household_books_have_no_flags(at_now, margaret, client):
    login(client, "jr")
    assert client.get("/api/b/ours/flags").status_code == 404


def test_a_missed_premium_page(at_now, margaret, mdb, client):
    chk = account(mdb, "Joint checking", feed=True)
    acct_item = mdb.scalar(select(InventoryItem).where(InventoryItem.account_id == chk.id))
    ltc = InventoryItem(kind="insurance", name="Long-term care", owner="margaret", institution="Evergreen Mutual",
                        last4="7730", status="needs_checking", data_source="manual", details=json.dumps({
                            "type": "long_term_care", "amount_cents": 21_200, "frequency": "monthly", "usual_day": 10,
                            "paid_from_id": acct_item.id, "match_text": "EVERGREEN MUTUAL"}))
    mdb.add(ltc)
    mdb.flush()
    mdb.add(InventoryItem(kind="contact", name="Evergreen Mutual service", owner="margaret",
                          institution="Evergreen Mutual", status="confirmed",
                          details=json.dumps({"role": "other", "phone": "(800) 555-0142", "looks_after_ids": [ltc.id]})))
    mdb.add(SimplefinLink(access_url="http://demo:demo@testserver/simplefin", claimed_at=NOW, last_sync_at=NOW))
    mdb.get(Institution, chk.institution_id).last_sync_at = NOW
    record_balance(mdb, chk.id, NOW.date(), 100_000)
    mdb.commit()
    for days in (101, 70, 39):                                          # Jul 10, Aug 10, Sep 10
        txn(mdb, chk, days, -21_200, "EVERGREEN MUTUAL LTC PREM", watched=True)
    run_daily(mdb, TODAY - timedelta(days=2), NOW - timedelta(days=2))  # Sat Oct 17
    mdb.commit()
    f = mdb.scalar(select(Flag).where(Flag.check == "premium"))
    login(client, "jr")
    page = client.get(f"{BOOK}/flags/{f.id}").json()
    assert page["urgent"] is True and page["when"] == "Due Mon, Oct 12" and page["place"] == "Long-term care"
    assert page["title"] == "Long-term care premium hasn't left"
    assert page["fact"] == ("the Evergreen Mutual long-term care premium, $212.00, usually leaves Joint checking around "
                            "the 10th. Oct 10 was a Saturday, so it was due Mon, Oct 12. It hasn't left as of Oct 17.")
    assert [(p["name"], p["amount_cents"], p["meta"]) for p in page["payments"]] == [
        ("EVERGREEN MUTUAL LTC PREM", 21_200, "Thu, Sep 10 · Joint checking"),
        ("EVERGREEN MUTUAL LTC PREM", 21_200, "Mon, Aug 10 · Joint checking"),
        ("EVERGREEN MUTUAL LTC PREM", 21_200, "Fri, Jul 10 · Joint checking")]
    assert page["related_line"] == "Policy ••7730 · Evergreen Mutual: (800) 555-0142"
    assert page["related"]["id"] == ltc.id
    assert page["expected_note"].startswith('Expected here means "not due this month".')
    assert page["transaction"] is None


MARKS = (("expected", {}), ("handled", {"note": "Called the bank."}), ("checking", {}), ("note", {"note": "x"}),
         ("reopen", {}))


@pytest.mark.parametrize("action,body", MARKS)
def test_only_managers_can_mark_change_notes_or_reopen(at_now, margaret, mdb, client, action, body):
    _, _, scam = _cvs(mdb)
    login(client, "vee")
    r = client.post(f"{BOOK}/flags/{scam.id}/{action}", json=body)
    assert (r.status_code, r.json()) == (403, {"error": "You can see this book but not change it."})
    mdb.expire_all()
    assert mdb.get(Flag, scam.id).status == "open"


def test_a_refusal_answers_with_its_own_status(at_now, margaret, mdb, client):
    _, _, scam = _cvs(mdb)
    login(client, "jr")
    assert client.post(f"{BOOK}/flags/{scam.id}/handled", json={}).status_code == 422          # a note is required
    assert client.post(f"{BOOK}/flags/{scam.id}/note", json={"note": "x"}).status_code == 409  # not Still checking
    assert client.post(f"{BOOK}/flags/{scam.id}/reopen", json={}).status_code == 409           # not Done
    assert client.post(f"{BOOK}/flags/{scam.id}/handled", json={"note": "Called."}).status_code == 200
    r = client.post(f"{BOOK}/flags/{scam.id}/checking", json={})
    assert (r.status_code, r.json()) == (409, {"error": "This flag is done. Reopen it to change it."})


def test_the_floor_change_reads_correctly_in_a_flags_history(at_now, margaret, mdb, client):
    from steward.watch.views import AFTER_WORDS
    assert AFTER_WORDS["floor"] == "when the floor was set"
    f = add_flag(mdb, check="low", severity="normal", summary="Joint checking is under its floor",
                 title="Joint checking is under its floor", now=NOW, after="floor",
                 facts={"account": "Joint checking", "balance_cents": 90_000, "floor_cents": 100_000,
                        "as_of": "2026-10-19"})
    mdb.commit()
    login(client, "jr")
    page = client.get(f"{BOOK}/flags/{f.id}").json()
    assert [h["text"] for h in page["history"]] == ["Flagged by the checks when the floor was set"]


def test_no_flag_shows_an_account_number(at_now, margaret, mdb, client):
    from steward.watch.views import email_facts
    acct = "Everyday Checking ...4417"
    f = add_flag(mdb, check="premium", severity="urgent", now=NOW, after="daily", due_on=TODAY,
                 summary=f"The Evergreen premium, $212.00, hasn't left {acct}", title="Premium hasn't left",
                 facts={"amount_cents": 21_200, "account": acct, "label": "Evergreen Mutual long-term care",
                        "usual_day": 10, "nominal": "2026-10-10", "due": "2026-10-12", "as_of": "2026-10-17",
                        "item": "Long-term care"})
    low = add_flag(mdb, check="low", severity="normal", now=NOW, after="daily", summary=f"{acct} is $900.00",
                   title=f"{acct} is low", facts={"account": acct, "balance_cents": 90_000, "floor_cents": 100_000,
                                                  "as_of": "2026-10-19"})
    new = add_flag(mdb, check="account", severity="normal", now=NOW, after="sync",
                   summary=f"Wells Fargo reports {acct}, which wasn't in the inventory",
                   title=f"New account {acct}", facts={"account": acct, "institution": "Wells Fargo"})
    mdb.commit()
    login(client, "jr")
    seen = [client.get(f"{BOOK}/flags").text]
    seen += [client.get(f"{BOOK}/flags/{x.id}").text for x in (f, low, new)]
    for text in seen:
        assert "...4417" not in text and "Everyday Checking 4417" in text or "Everyday Checking" not in text
    assert "Everyday Checking" in seen[1]
    for x in (f, low, new):
        assert all("...4417" not in value for _, value in email_facts(mdb, x))


def test_the_banks_text_shows_at_most_the_last_four_digits(at_now, margaret, mdb, client):
    chk = account(mdb, "Joint checking")
    history(mdb, chk)
    t1 = txn(mdb, chk, 3, -9_000_00, "ZELLE PAYMENT TO J. SMITH FROM ACCT 987654321012 CONF# QX81234")
    t2 = txn(mdb, chk, 2, -9_000_00, "ZELLE PAYMENT TO J. SMITH FROM ACCT 987654321012 CONF# QX81234")
    f = add_flag(mdb, check="duplicate", severity="normal", summary="Two payments to J. Smith", title="Two payments",
                 facts={"payee": "J. Smith", "payee_key": "J SMITH", "method": "zelle", "amount_cents": 900_000,
                        "account": "Joint checking", "posted_at": t2.posted_at.isoformat(), "other_id": t1.id},
                 now=NOW, after="sync", transaction_id=t2.id)
    mdb.commit()
    login(client, "jr")
    page = client.get(f"{BOOK}/flags/{f.id}").json()
    assert page["transaction"]["raw"] == "ZELLE PAYMENT TO J. SMITH FROM ACCT • CONF# QX•1234"
    assert all(o["raw"].endswith("ACCT • CONF# QX•1234") for o in page["others"])
    assert "987654321012" not in client.get(f"{BOOK}/flags/{f.id}").text
    assert "987654321012" not in client.get(f"{BOOK}/flags").text


def test_a_name_with_the_number_run_into_it_shows_no_number(at_now, margaret, mdb, client):
    from steward.watch.views import email_facts
    acct = "Checking123456789"
    f = add_flag(mdb, check="low", severity="normal", now=NOW, after="daily", summary=f"{acct} is $900.00",
                 title=f"{acct} is low", facts={"account": acct, "balance_cents": 90_000, "floor_cents": 100_000,
                                                "as_of": "2026-10-19"})
    mdb.commit()
    login(client, "jr")
    for text in (client.get(f"{BOOK}/flags").text, client.get(f"{BOOK}/flags/{f.id}").text):
        assert "123456789" not in text
    assert all("123456789" not in value for _, value in email_facts(mdb, f))


def test_no_run_of_more_than_four_digits_of_a_number_leaves_the_server(at_now, margaret, mdb, client, caplog):
    from steward.watch.emails import summary_email, urgent_email
    from steward.watch.views import account_label, email_facts
    number = "4929381372645019"
    spellings = ["4929 3813 7264 5019", f"${number}", "Oct 17 1234567890", "４９２９　３８１３　７２６４　５０１９",
                 "4929/3813/7264/5019", "4929-3813-7264-5019", "4929,3813,7264,5019", "4929|3813|7264|5019",
                 "4929:3813:7264:5019", "4929+3813+7264+5019", "4929 \u2013 3813 \u2013 7264 \u2013 5019",
                 "4929\u200b3813\u200b7264\u200b5019", "4929~3813~7264~5019", "4929  3813 7264 5019",
                 "4929ab3813cd7264ef5019"]
    forbidden = ("4929", "3813", "7264", "12345", "23456", "34567", "45678", "56789", "67890", "93813", "81372")
    chk = account(mdb, f"Checking {spellings[0]}")
    history(mdb, chk)
    flags, nows = [], []
    for i, sp in enumerate(spellings):
        t = txn(mdb, chk, 2 + i, -9_000_00, f"ZELLE PAYMENT TO J. SMITH ACCT {sp} CONF# QX81234")
        flags.append(add_flag(mdb, check="scam", severity="urgent", now=NOW, after="sync", transaction_id=t.id,
                              account_id=chk.id, summary=f"$900.00 by Zelle to J. Smith {sp} from Checking {sp}",
                              title=f"Zelle to J. Smith {sp}",
                              facts={"amount_cents": 90_000, "payee": f"J. Smith {sp}", "account": f"Checking {sp}",
                                     "posted_at": t.posted_at.isoformat(), "kind": "wire", "raw": t.raw_description}))
        nows.append(add_flag(mdb, check="monthly", severity="normal", now=NOW, after="sync", account_id=chk.id,
                             summary=f"Charge on card {sp}", title=f"Card {sp}",
                             facts={"amount_cents": 1599, "account": f"Checking {sp}", "payee": sp}))
    mdb.commit()
    login(client, "jr")
    args = {"book_name": "Margaret Ellis", "slug": "margaret-ellis", "base_url": "https://nas.example", "name": "Margaret"}
    caplog.set_level(logging.DEBUG)
    seen = [client.get(f"{BOOK}/flags").text, client.get(f"{BOOK}/flags?show=done").text]
    seen += [client.get(f"{BOOK}/flags/{x.id}").text for x in (*flags, *nows)]
    for f in flags:
        e = urgent_email(f, email_facts(mdb, f), large_cents=50_000, account=account_label(mdb, f), **args)
        seen += [e.subject, e.text, e.html]
    e = summary_email(nows, today=TODAY, still=None, accounts={x.id: account_label(mdb, x) for x in nows}, **args)
    seen += [e.subject, e.text, e.html, caplog.text]
    for text in seen:
        plain = "".join(c for c in unicodedata.normalize("NFKC", text) if unicodedata.category(c) != "Cf")
        assert not any(g in plain for g in forbidden), plain[:300]
    assert "J. Smith" in seen[2] or "J Smith" in seen[2]


def test_emails_carry_the_account_from_the_inventory_and_no_bank_text(at_now, margaret, mdb, client):
    from steward.watch.emails import urgent_email
    from steward.watch.views import account_label, email_facts
    chk = account(mdb, "Everyday Checking 123456789")
    item = mdb.scalar(select(InventoryItem).where(InventoryItem.account_id == chk.id))
    item.last4 = "6789"
    history(mdb, chk)
    t = txn(mdb, chk, 2, -50_000, "CVS/PHARMACY #09812 GIFT CARD FULLERTON CA", category="shopping")
    f = add_flag(mdb, check="scam", severity="urgent", now=NOW, after="sync", transaction_id=t.id, account_id=chk.id,
                 summary="$500.00 gift card at CVS Pharmacy", title="$500.00 gift card at CVS Pharmacy",
                 facts={"amount_cents": 50_000, "payee": "CVS Pharmacy", "account": chk.name, "raw": t.raw_description,
                        "posted_at": t.posted_at.isoformat(), "kind": "gift_card"})
    mdb.commit()
    assert account_label(mdb, f) == "Everyday Checking ••6789"   # the repeat of the stored last 4 is trimmed
    args = {"book_name": "Margaret Ellis", "slug": "margaret-ellis", "base_url": "https://nas.example", "name": "Margaret"}
    e = urgent_email(f, email_facts(mdb, f), large_cents=50_000, account=account_label(mdb, f), **args)
    assert "From:    Everyday Checking ••6789\n" in e.text
    assert "GIFT CARD FULLERTON" not in e.text + e.html and "09812" not in e.text + e.html
