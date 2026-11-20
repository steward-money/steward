"""Word rules for the public docs. JR is they/them and the docs never call anyone he or she; the docs don't use the
word the docs rules ban for the reader; no doc, and no in-app text that makes the same promises, repeats an absolute
that a review found false; and every command a doc shows exists."""
import re

import typer.main

from steward.cli import app as steward_app
from steward_gateway.cli import app as gateway_app
from tests.public_docs import ROOT, public_docs

PRONOUN = re.compile(r"\b(he|him|his|himself|she|her|hers|herself)\b", re.IGNORECASE)   # as tests/test_no_he_or_she.py
BANNED_WORD = re.compile(r"\bstr" + r"angers?\b", re.IGNORECASE)
SPAN = re.compile(r"^ *```[^\n]*\n(.*?)^ *```|`([^`\n]+)`", re.S | re.M)
WORD = re.compile(r"(?=\bsteward(-gateway)? ([a-z][a-z-]*))")


def line_hits(pattern):
    hits = []
    for doc in public_docs():
        for n, line in enumerate(doc.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line):
                hits.append(f"{doc.relative_to(ROOT)}:{n}")
    return hits


def test_no_public_doc_says_he_or_she():
    assert line_hits(PRONOUN) == []


def test_no_public_doc_uses_the_banned_word_for_the_reader():
    assert line_hits(BANNED_WORD) == []


# PR 15's claims review found these phrases false: the checks can miss things (privacy.md, What the checks can miss),
# routes are checked by a sweep rather than by a rule about trust, and whoever has the machine and its key can change
# the sent log. The 0.1.0 release gate found the rest (fix PR 16d): a payee in approved store rows can be a person
# (PRIV-2); the gateway's model can miss a name and drops some on purpose (PRIV-3); the exact pass matches less than
# everything Steward holds (PRIV-4); approved rows stay with the conversation (PRIV-9); the log holds every part of
# your data, not every byte of a request (PRIV-10); and what you type into Claude Code goes around Steward (PRIV-12).
# The 16d re-check found two more forms of the same claims: "approved ... for this question" on the card's button, the
# approval line and What Claude saw (PRIV-9), and "names ... account numbers ... stay" said other ways (PRIV-2, TRI-2),
# which ABSOLUTE_SHAPES catches whatever words sit between. Public issue #53's review found the approval card's
# "every row, exactly as it would go" and turn.py's "byte for byte what would be sent": the gateway may still replace a
# person's name in an unticked row. Each says more than its CLAIMS row. Keep the list short and explicit; the release
# gate's pillar reviews check the rest.
ABSOLUTES_FOUND_FALSE = (
    "anything that identifies", "trusts nothing", "checks every word", "nobody can change",
    "never receives names", "never sends names", "never names", "names of people", "notes, people's names",
    "names and account numbers stay", "claude never saw the names", "replaced exactly", "see exactly what was sent",
    "for one question", "this one question", "for this question", "same checks as a question",
    "same checks as chat", "names aren't sent", "those are never sent",
    "exactly as it would go", "byte for byte what would be sent",
)
# "Names, notes and account numbers stay here", "the names and account numbers Steward holds stay here": names held
# back with account numbers, as if no name ever goes. Within one sentence, so "account numbers stay" alone is allowed.
ABSOLUTE_SHAPES = (re.compile(r"\bnames\b[^.;:]{0,40}\baccount numbers\b[^.;:]{0,30}\bstay\b"),)


def phrase_hits(texts: dict[str, str]) -> list[str]:
    """Each text that holds a phrase from ABSOLUTES_FOUND_FALSE or a shape from ABSOLUTE_SHAPES, ignoring case, line
    breaks and extra spaces."""
    hits = []
    for name, text in texts.items():
        flat = " ".join(text.split()).lower()
        hits += [f"{name}: {p!r}" for p in ABSOLUTES_FOUND_FALSE if p in flat]
        hits += [f"{name}: {m.group(0)!r}" for shape in ABSOLUTE_SHAPES if (m := shape.search(flat))]
    return hits


def absolute_hits(docs, root=ROOT) -> list[str]:
    """Each doc that holds a phrase from ABSOLUTES_FOUND_FALSE or a shape from ABSOLUTE_SHAPES."""
    return phrase_hits({str(doc.relative_to(root)): doc.read_text(encoding="utf-8") for doc in docs})


# Two string literals on adjacent lines that Python joins into one ("... account numbers, "\n    "addresses ..."), so a
# phrase split across them is still found.
JOINED_LITERALS = re.compile(r"([\"'])\s*\n\s*[rbfu]*\1")


def in_app_texts(root=ROOT) -> dict[str, str]:
    """The in-app words that make the docs' promises to the person using Steward (gate finding TRI-2): every copy.ts
    under web/src, web/src/lib/copy.ts among them; the disclosure shown before chat is turned on; and every module of
    the advisor, where the lines chat shows as sent are written (the approval line in turn.py, What Claude saw in
    saw.py), comments and all."""
    from steward.advisor.state import DISCLOSURE
    texts = {p.relative_to(root).as_posix(): p.read_text(encoding="utf-8")
             for p in sorted((root / "web/src").rglob("copy.ts"))}
    texts["src/steward/advisor/state.py DISCLOSURE"] = DISCLOSURE
    texts |= {p.relative_to(root).as_posix(): JOINED_LITERALS.sub("", p.read_text(encoding="utf-8"))
              for p in sorted((root / "src/steward/advisor").rglob("*.py"))}
    return texts


def test_no_public_doc_repeats_an_absolute_the_claims_review_found_false():
    assert absolute_hits(public_docs()) == []


def test_no_in_app_text_repeats_an_absolute_a_review_found_false():
    assert phrase_hits(in_app_texts()) == []


def test_the_in_app_check_reads_the_shared_copy_the_chat_copy_and_the_disclosure():
    names = set(in_app_texts())
    assert {"web/src/lib/copy.ts", "web/src/screens/chat/copy.ts", "web/src/screens/welcome/copy.ts",
            "src/steward/advisor/state.py DISCLOSURE", "src/steward/advisor/turn.py", "src/steward/advisor/saw.py",
            "src/steward/advisor/tools/merchant.py"} <= names
    assert len(names) > 30, "the copy.ts and advisor walks found almost nothing"


def test_the_absolute_check_sees_the_other_forms_the_16d_recheck_found(tmp_path):
    """Approval said to cover one question, and names held back with account numbers in any wording; a phrase split
    across two joined Python string literals is still one phrase."""
    texts = {"a": "Approve for this question", "b": "Names, notes and account numbers stay here.",
             "c": "The names and account numbers Steward holds stay here.", "d": "Notes and account numbers stay here.",
             "e": "Imported files, their names and where a row came from stay home."}
    assert [h.split(":")[0] for h in phrase_hits(texts)] == ["a", "b", "c"]
    module = tmp_path / "m.py"
    module.write_text('LINE = ("{name} approved {rows} for this "\n        "question.")\n', encoding="utf-8")
    assert phrase_hits({"m": JOINED_LITERALS.sub("", module.read_text(encoding="utf-8"))}) == ["m: 'for this question'"]


def test_the_absolute_check_sees_a_phrase_across_a_line_break_and_in_any_case(tmp_path):
    doc = tmp_path / "privacy.md"
    doc.write_text("The gateway Checks\n  every   word. Nobody can change it.\n", encoding="utf-8")
    assert absolute_hits([doc], tmp_path) == ["privacy.md: 'checks every word'", "privacy.md: 'nobody can change'"]


def commands(app) -> set[str]:
    """The names of the app's top-level commands and groups. Typer 0.27 vendors click, so the apps are read with
    typer.main.get_command, not by isinstance against click.Group."""
    return set(typer.main.get_command(app).commands)


def test_every_command_a_public_doc_shows_exists():
    """steward <word> and steward-gateway <word> in code spans and blocks name a command of steward.cli.app or
    steward_gateway.cli.app. The word "steward" is skipped: it's the container in `docker exec -it steward steward ...`."""
    known = {"": commands(steward_app), "-gateway": commands(gateway_app)}
    assert known[""] and known["-gateway"], "the command walk found nothing"
    bad = []
    for doc in public_docs():
        text = doc.read_text(encoding="utf-8")
        for span in SPAN.finditer(text):
            body = span.group(1) or span.group(2)
            for m in WORD.finditer(body):
                suffix, word = m.group(1) or "", m.group(2)
                if word != "steward" and word not in known[suffix]:
                    line = text.count("\n", 0, span.start()) + 1
                    bad.append(f"{doc.relative_to(ROOT)}:{line}: steward{suffix} {word}")
    assert bad == []
