"""Reading an upload takes time in step with its size (SEC-3).

A 5 MB OFX file of opening `<STMTTRN>` tags with no closing one made `<STMTTRN>(.*?)</STMTTRN>` scan to the end of
the file from every tag: time grew with the square of the size, about 2 hours at the cap, inside one `re` call that
holds the interpreter lock, so every request on the install waited. The class is a regular expression whose time
can grow faster than its input, in code that reads what a person uploads. The scan below fails on one, unless it's
listed here with the reason it's bounded; tests/regex_shape.py says which shapes it looks for.
"""

import ast
import dataclasses
import importlib
import importlib.util
import random
import re
import sys
from datetime import date
from pathlib import Path

import pytest

from steward.services import transactions
from steward.services.importers import ofx
from steward.services.importers.base import DESCRIPTION_MAX, ParsedRow, ParseError
from steward.services.importers.ofx import parse_ofx, statements
from steward.services.transactions import normalize, tidy
from steward.watch import payees
from tests.regex_shape import _held_patterns, scan, shapes

ROOT = Path(__file__).resolve().parent.parent
CAP = 5 * 1024 * 1024  # api/imports.MAX_BYTES, the largest upload

# Code that reads an upload: CSV and OFX, PDF statements (here, in the PDF child and on the gateway), positions,
# Social Security and tax-form files, restores, the import plan, and tidy() and normalize(), which run on every row
# an upload holds. Watching, which runs on every row's raw text after an import and again in its views and the
# advisor's tools (payee()), with the patterns it loads from watch/data/watch.json read on their own below. And the
# export tool, which reads every file in the tree and in the images. Code outside the scope that reads the raw text
# of a transaction stored since the cut gets at most DESCRIPTION_MAX characters of it (test_a_row_keeps_at_most_...).
# That caps its input's length; a pattern there that is quadratic still costs that length squared.
SCOPE = (
    "src/steward/services/importers",
    "src/steward/services/statements",
    "src/steward/services/imports.py",
    "src/steward/services/import_plan.py",
    "src/steward/services/import_staging.py",
    "src/steward/services/transactions.py",
    "src/steward/services/positions.py",
    "src/steward/services/ss_import.py",
    "src/steward/services/book_restore.py",
    "src/steward/api/imports.py",
    "src/steward/api/statements.py",
    "src/steward/api/investments.py",
    "src/steward/api/ss.py",
    "src/steward/api/tax_forms.py",
    "src/steward/api/inventory_files.py",
    "src/steward_pdf",
    "src/steward_gateway/statements.py",
    "src/steward_gateway/pdf.py",
    "src/steward_boundary/statements_wire.py",
    "src/steward/watch",
)
# The export tool stays in the private tree (export/allowlist.txt), so the public one runs without it.
PRIVATE = ("export",) if (ROOT / "export").is_dir() else ()

# (file, shape, pattern or, for a dynamic one, its source) -> why its time stays in step with the input.
LISTED = {
    ("src/steward/services/transactions.py", "span",
     r"\s(?:(?:WEB|PPD|CCD|TEL|IAT|CTX|ARC|BOC|POP|RCK)\s+ID\b|ID:\s*(?:\d|$)"
     r"|(?:CONF|CONFIRMATION|REF|REFERENCE|TRACE|TRN|TXN)\s*(?:#|:|NO\b|NUMBER\b)).*$"):
        "normalize() runs it only after _WS has made every run of spaces one space and left no line break, so a "
        "start that fails does so within a few characters, and `.*$` always reaches the end.",
    ("src/steward/services/statements/money.py", "span",
     r"\(?[+-]?\s?\$?\s?[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2}\)?(?:\s?(?:CR|DR)\b|-(?!\d))?"):
        "runs on one statement line, at most MAX_CELLS cells of MAX_CELL characters (statements_wire).",
    ("src/steward/services/statements/labels.py", "dynamic",
     "('(?<!\\\\w)' if re.match('\\\\w', s[0]) else '') + re.escape(s) + "
     "('(?!\\\\w)' if re.match('\\\\w', s[-1]) else '')"):
        "an escaped label between two lookarounds: a literal, with no repeat.",
    ("src/steward/services/statements/stage.py", "dynamic", "f'(?<!\\\\d){last4}(?!\\\\d)'"):
        "last4 is up to 4 characters the wire checked; runs on an account's name.",
    ("src/steward_pdf/lines.py", "span",
     r"(?<![0-9])(?<![0-9][-./,])(?:[0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]{2})?|[0-9]{1,7}\.[0-9]{2}"
     r"|[0-9]{1,4}(?:[-/.][0-9]{1,4}){1,2}(?:-[0-9]{1,4}(?:[-/.][0-9]{1,4}){1,2})?)(?![0-9])(?![-./,][0-9])"):
        "the lookbehinds let a match start only where no digit, and no digit and separator, comes before, so no "
        "start falls inside the run `(?:,[0-9]{3})+` steps over.",
    ("export/export.py", "span", r' ([\w-]+)=("(?:[^"\\]|\\.)*"|\S*)'):
        "one line of gitleaks' own log, which the export runs and reads; not a file's content.",
    ("export/export.py", "dynamic", "''.join(out)"):
        "a glob from export/allowlist.txt turned into a regex, applied to one path.",
    ("export/export.py", "dynamic", "rx"):
        "a `paths` rule from the repo's .gitleaks.toml, applied to one folder's path.",
    ("export/export.py", "dynamic", "r['path']"):
        "a rule's path regex from the repo's .gitleaks.toml, applied to one path.",
}
# watch/payees.py builds its patterns from watch.json when first asked. test_the_watch_patterns_take_time_in_step
# reads each one compiled.
LISTED.update({("src/steward/watch/payees.py", "dynamic", source): "built from watch/data/watch.json; "
               "test_the_watch_patterns_take_time_in_step reads it compiled." for source in (
    "f'(?<![A-Z0-9])(?:{_either(words)})(?![A-Z0-9])'",
    "to['to']",
    "to['name']",
    "to['end']",
    "f'(?<![A-Z0-9])({_either((a.upper() for a in apps))})\\\\s*\\\\*\\\\s*(?P<name>[^\\\\s*]+)'",
    "m['pattern']",
    "f'(?:{_either(authorized)})(?: \\\\$ ?\\\\d+(?:\\\\.\\\\d{{2}})?)?(?: RETURN)? AUTHORIZED ON"
    "(?: \\\\d{{1,2}}/\\\\d{{1,2}}(?:/\\\\d{{2,4}})?)?(?= |$)'",
)})
# The Words fields payees.py calls only `.match` on, so each starts at one place.
WATCH_ONE_START = ("name", "end", "authorized")
# watch.json's old recipient pattern, one lazy name per "TO": kept here only to prove the scan names it and that
# payees._recipient finds the same name on short descriptions.
_OLD_RECIPIENT = r"\bTO\s+(?P<name>[A-Z][A-Z .,'&-]*?)(?=\s+(?:ON|CONF|REF|ID|MEMO|FOR)\b|\s*#|\s+\S*\d|$)"


def _files() -> list[Path]:
    out: list[Path] = []
    for entry in SCOPE + PRIVATE:
        p = ROOT / entry
        out += sorted(p.rglob("*.py")) if p.is_dir() else [p]
    return out


def _module(path: Path):
    rel = path.relative_to(ROOT)
    if rel.parts[0] == "export":
        name = "steward_export" if rel.name == "export.py" else f"steward_export_{rel.stem}"
        if name not in sys.modules:
            spec = importlib.util.spec_from_file_location(name, path)
            mod = importlib.util.module_from_spec(spec)
            sys.modules[name] = mod  # dataclasses look their module up here
            spec.loader.exec_module(mod)
        return sys.modules[name]
    parts = rel.with_suffix("").parts[1:]
    return importlib.import_module(".".join(parts[:-1] if parts[-1] == "__init__" else parts))


def test_the_scope_is_still_there():
    missing = [e for e in SCOPE if not (ROOT / e).exists()]
    assert not missing, f"moved or renamed: point SCOPE at the new place: {missing}"


def test_every_regex_in_upload_code_takes_time_in_step_with_its_input():
    found = {(f.path, f.shape, f.pattern): f for path in _files() for f in scan(path, ROOT, _module(path))}
    unlisted = [f"{f.path}:{f.line} {f.shape} {f.pattern!r}" for key, f in found.items() if key not in LISTED]
    assert not unlisted, (
        "a regex whose time can grow faster than its input, in code that reads an upload. Search forward with "
        "str.find or a closing-tag search, bound the repeat, or anchor it; or list it in LISTED with the reason "
        "its input is bounded:\n" + "\n".join(unlisted))
    stale = [k for k in LISTED if k not in found and (PRIVATE or not k[0].startswith("export/"))]
    assert not stale, f"listed but no longer found: remove from LISTED: {stale}"


def test_upload_code_calls_re_by_its_module_name():
    """The scan reads `re.compile(...)` and the like. `from re import compile` or another regex module would hide
    a pattern from it."""
    hidden = []
    for path in _files():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.module in ("re", "regex"):
                hidden.append(f"{path.relative_to(ROOT)}:{node.lineno}")
            elif isinstance(node, ast.Import):
                hidden += [f"{path.relative_to(ROOT)}:{node.lineno}" for a in node.names
                           if (a.name == "re" and a.asname) or a.name == "regex"]
    assert not hidden, hidden


def _watch_patterns():
    W = payees.words()
    for field in dataclasses.fields(W):
        for rx in _held_patterns(getattr(W, field.name)):
            yield field.name, rx


def test_the_watch_patterns_take_time_in_step():
    """payee(), scam_kind() and is_cash() run these on a transaction's raw text: on every row after an import, and in
    Watch's views, set-aside matching and the advisor's tools. A 5 MB OFX name of "ZELLE TO A TO A ... !" held the
    server for hours on the old lazy recipient pattern, at commit."""
    risky = [f"{name}: {sorted(found)} {rx.pattern!r}" for name, rx in _watch_patterns()
             if (found := shapes(rx.pattern, rx.flags, one_start=name in WATCH_ONE_START))]
    assert not risky, "a watch.json pattern whose time can grow faster than its input:\n" + "\n".join(risky)
    assert {name for name, _ in _watch_patterns()} >= {"methods", "to", "name", "end", "app", "cash", "drop", "scam",
                                                         "authorized"}


def test_the_one_start_watch_patterns_are_only_matched():
    """WATCH_ONE_START is judged as starting at one place. A `.search` on one of them would start everywhere."""
    path = ROOT / "src/steward/watch/payees.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    parent = {id(child): node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    words_obj = [n for n in ast.walk(tree) if isinstance(n, ast.Attribute) and n.attr in WATCH_ONE_START
                 and ((isinstance(n.value, ast.Name) and n.value.id == "W")
                      or (isinstance(n.value, ast.Call) and getattr(n.value.func, "id", "") == "words"))]
    assert words_obj, "payees.py no longer reads these fields: update WATCH_ONE_START"
    other = [f"payees.py:{n.lineno} .{n.attr}" for n in words_obj
             if not (isinstance(up := parent.get(id(n)), ast.Attribute) and up.attr in ("match", "fullmatch"))]
    assert not other, other


@pytest.mark.parametrize(("pattern", "flags", "expected"), [
    (r"<STMTTRN>(.*?)</STMTTRN>", re.S | re.I, {"span"}),        # SEC-3 itself
    (r"<(CC)?STMTRS>(.*?)</(?:CC)?STMTRS>", re.S | re.I, {"span"}),
    (r"<A>(.*?)</A>", 0, {"span"}),                                # no DOTALL: one long line does the same
    (r"<A>(.*)</A>", re.S, {"span"}),                              # greedy too
    (r"(\s+(#?\d[\d/\-]*|\*\w+))+$", 0, {"span"}),                 # tidy()'s old trailing-code pattern
    (_OLD_RECIPIENT, 0, {"span"}),                                 # watch payees' old lazy recipient
    (r"\s+$", 0, {"span"}),
    (r"(?=.*X)", 0, {"span"}),                                     # inside a lookahead
    (r"^(a+)+$", 0, {"nested"}),
    (r"(\w+\s?)*!", 0, {"nested", "span"}),
    (r"<STMTTRN>", re.I, set()),
    (r"<([A-Z0-9.]+)>([^<\r\n]*)", re.I, set()),                   # ofx._TAG: no start falls inside a run
    (r"\A\s*x", 0, set()),                                         # one place to start
    (r"^\s*x", 0, set()),
    (r"\s+", 0, set()),                                            # nothing after the repeat can fail
    (r"\d+(?:[-/]\d+)*", 0, set()),
    (r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", 0, {"span"}),                # the dot separates turns: not nested
    (r"•(?:[^\w•]*•)*x", 0, {"span"}),                            # each • starts a scan to the end
    (r"(?:ab)+c", 0, {"span"}),                                    # each a starts a scan to the end
    (r"app/[^/]*\.md", 0, set()),                                  # a start needs a / the run never holds
    (r"<X>([^<]*)</X>", 0, set()),
])
def test_the_scan_knows_the_shapes(pattern, flags, expected):
    assert shapes(pattern, flags) == expected


def test_one_start_has_no_span():
    assert shapes(r"\s*x", 0) == {"span"}
    assert shapes(r"\s*x", 0, one_start=True) == set()


def test_the_scan_reads_a_file(tmp_path):
    (tmp_path / "m.py").write_text(
        "import re\n"
        "_A = re.compile(r'<T>(.*?)</T>', re.S)\n"
        "_B = re.compile(r'\\s*x')\n"
        "_C = re.compile(r'\\d+')\n"
        "def f(s, x):\n"
        "    return _B.match(s), re.search(x, s), re.findall(rf'{re.escape(x)}(.*?)y', s, flags=re.S)\n")
    got = {(f.line, f.shape) for f in scan(tmp_path / "m.py", tmp_path)}
    assert got == {(2, "span"), (6, "dynamic"), (6, "span")}  # _B only ever matches, so it starts once


def test_the_scan_reads_a_list_of_patterns_the_module_loops_over(tmp_path):
    """The export tool keeps its image-path globs as a list of compiled patterns and fullmatches each one against
    one path, so each starts in one place. The same list searched would start everywhere."""
    (tmp_path / "m.py").write_text(
        "import re\n"
        "GLOBS = ('(?:[^/]+/)*bin/x[^/]*', '(?:[^/]+/)*lib/y[^/]*')\n"
        "_FULL = [re.compile(g) for g in GLOBS]\n"
        "_LOOP = [re.compile(g) for g in GLOBS]\n"
        "_SOUGHT = [re.compile(g) for g in GLOBS]\n"
        "def full(p):\n"
        "    return any(rx.fullmatch(p) or rx.fullmatch(p + '/') for rx in _FULL)\n"
        "def loop(p):\n"
        "    for rx in _LOOP:\n"
        "        if rx.match(p):\n"
        "            return True\n"
        "def sought(p):\n"
        "    return [rx.fullmatch(p) or rx.search(p) for rx in _SOUGHT]\n")
    spec = importlib.util.spec_from_file_location("linear_scan_sample", tmp_path / "m.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    got = sorted((f.line, f.shape, f.pattern) for f in scan(tmp_path / "m.py", tmp_path, module))
    assert got == [(0, "span", r"(?:[^/]+/)*bin/x[^/]*"), (0, "span", r"(?:[^/]+/)*lib/y[^/]*")]


# The failing scenarios, at the cap. Linear reading takes well under a second; the old code took hours, so the
# test timeout (pytest.ini_options) is the margin. Not a timing test. The commit route's own test, with Watching
# running on such a row, is in tests/test_import_commit_api.py.

def test_a_cap_sized_file_of_unclosed_transactions_is_refused():
    body = "<OFX>" + "<STMTTRN>" * ((CAP - 5) // 9)
    with pytest.raises(ParseError) as e:
        parse_ofx(body)
    assert e.value.row == 0  # no transactions


def test_a_cap_sized_file_of_unclosed_statements_reads_nothing():
    body = "<OFX>" + "<CCSTMTRS><STMTTRN>" * ((CAP - 5) // 19)
    (only,) = statements(body)
    assert only.rows == () and only.errors == ()


@pytest.mark.parametrize(("tail", "expected"), [("", "SHOP"), (" X", ("SHOP" + " 1" * 100)[:200])],
                         ids=["codes to the end", "a word last"])
def test_a_cap_sized_transaction_name_is_tidied(tail, expected):
    """tidy() and normalize() run on every row of an upload; their trailing-code pattern was quadratic too. A new row
    keeps DESCRIPTION_MAX characters, so this is the text as a row stored before that cut holds it."""
    name = "SHOP" + " 1" * ((CAP - 200) // 2) + tail
    assert tidy(name) == expected
    assert normalize(name) == expected


def _ofx(name: str) -> str:
    return f"<OFX><STMTTRN><DTPOSTED>20260915<TRNAMT>-1.00<NAME>{name}</STMTTRN></OFX>"


def test_a_row_keeps_at_most_description_max_characters():
    """Every row a file, a statement or SimpleFIN gives is a ParsedRow, and ingest stores only those
    (test_only_ingest_writes_a_transactions_raw_text), so this one cut bounds the length of the raw text any pattern
    reads from a row stored since, inside the scan's scope or not. It bounds the input, not the pattern's cost on it,
    and rows stored before the cut keep their length. The cut shows."""
    name = "ZELLE" + " TO A" * ((CAP - 100) // 5) + "!"
    (row,) = parse_ofx(_ofx(name))
    assert len(row.raw_description) == DESCRIPTION_MAX
    assert row.raw_description == name[:DESCRIPTION_MAX - 1] + "…"
    assert ParsedRow(date(2026, 9, 15), -100, row.raw_description).raw_description == row.raw_description  # once
    short = "ZELLE TO A" + "B" * (DESCRIPTION_MAX - 10)
    assert ParsedRow(date(2026, 9, 15), -100, short).raw_description == short


# The writers a stored row's raw text may come from: ingest, from a ParsedRow; and the sample books' fixed text.
RAW_WRITERS = {"src/steward/services/ingest.py", "src/steward/demo.py", "src/steward/demo_parent.py",
               "src/steward/demo_watching.py"}


def test_only_ingest_writes_a_transactions_raw_text():
    """A new place that stores raw_description from anything but a ParsedRow would skip DESCRIPTION_MAX."""
    found = set()
    for path in sorted((ROOT / "src/steward").rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if "/alembic/" in rel:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            keyword = isinstance(node, ast.keyword) and node.arg == "raw_description"
            assigned = isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)) and any(
                isinstance(t, ast.Attribute) and t.attr == "raw_description"
                for t in (node.targets if isinstance(node, ast.Assign) else [node.target]))
            if keyword or assigned:
                found.add(rel)
    assert found - RAW_WRITERS == set(), "stores raw_description outside ingest: build a ParsedRow and ingest it"
    assert RAW_WRITERS - found == set(), "no longer writes raw_description: remove from RAW_WRITERS"
    ingest = (ROOT / "src/steward/services/ingest.py").read_text(encoding="utf-8")
    assert "raw_description=r.raw_description" in ingest and "rows: list[ParsedRow]" in ingest


@pytest.mark.parametrize(("name", "key"), [
    ("ZELLE" + " TO A" * ((CAP - 6) // 5) + "!", ""),                 # no name ends anywhere
    ("ZELLE" + " TO A" * ((CAP - 5) // 5), "zelle:A TO A TO A"),      # the name runs to the end
    ("ZELLE TO " + "A " * ((CAP - 11) // 2) + "X1", "zelle:A A A"),  # a word with a digit ends it
], ids=["no end", "to the end", "a digit last"])
def test_payee_reads_a_cap_sized_recipient(name, key):
    """payee() runs on every stored row's raw text, from Watching after an import, its views, set-aside matching and
    the advisor's tools; rows stored before DESCRIPTION_MAX can be this long."""
    p = payees.payee(name)
    assert p.method == "zelle" and p.key.startswith(key) and (p.key == "") == (key == "")


def test_the_recipient_matches_the_old_pattern():
    old = re.compile(_OLD_RECIPIENT)
    rng = random.Random(16)
    words = ["TO", "ZELLE", "A", "J.", "ON", "CONF", "#", "#1", "X1", "12", "!", "TOO", "-", "&", "REF", "MEMO", "ID",
             "FOR", "O'NEIL", "", ",", "ATO", "TO#", "TO1", "B"]
    named = 0
    for _ in range(20000):
        s = " ".join(rng.choice(words) for _ in range(rng.randint(1, 9)))
        s = payees._upper(s.replace(" ", "", rng.randint(0, 3)) if rng.random() < 0.3 else s)
        m = old.search(s)
        assert payees._recipient(s) == (m.group("name") if m else ""), s
        named += bool(m)
    assert named > 1000  # the inputs reach names, not only misses


# The old pattern, kept here only to prove the word loop gives the same text on short descriptions.
_OLD_TRAIL = re.compile(r"(\s+(#?\d[\d/\-]*|\*\w+))+$")


def test_dropping_trailing_codes_matches_the_old_pattern():
    rng = random.Random(16)
    words = ["AMAZON", "MKTP", "12", "#45", "*AB1", "*", "1/2", "3-4/5", "X1", "", "#", "-1", "*_", "Q*R", "07/11"]
    for _ in range(5000):
        s = " ".join(rng.choice(words) for _ in range(rng.randint(1, 7)))
        s = re.sub(r"\s+", " ", s)
        assert transactions._drop_trailing_codes(s) == _OLD_TRAIL.sub("", s), s


# The old OFX patterns, kept here only to prove the forward search finds the same blocks on short files.
_OLD_TRN = re.compile(r"<STMTTRN>(.*?)</STMTTRN>", re.S | re.I)
_OLD_STMT = re.compile(r"<(CC)?STMTRS>(.*?)</(?:CC)?STMTRS>", re.S | re.I)


def test_ofx_blocks_match_the_old_patterns():
    rng = random.Random(16)
    parts = ["<STMTTRN>", "</STMTTRN>", "<stmttrn>", "<STMTRS>", "</STMTRS>", "<CCSTMTRS>", "</CCSTMTRS>",
             "<NAME>A", "\n", "x", "<", ">", "</"]
    for _ in range(5000):
        s = "".join(rng.choice(parts) for _ in range(rng.randint(0, 12)))
        assert [b for _, b in ofx._blocks(s, ofx._TRN)] == [m.group(1) for m in _OLD_TRN.finditer(s)], s
        assert ([(bool(m.group(1)), b) for m, b in ofx._blocks(s, ofx._STMT)]
                == [(bool(m.group(1)), m.group(2)) for m in _OLD_STMT.finditer(s)]), s
