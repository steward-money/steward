"""Which files are Steward's public docs, and small readers for them (links, heading anchors, sections). The doc guards
(test_claims, test_doc_links, test_doc_words, test_export) share this list, so a new page is covered by all of them as
soon as it exists."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FOLDERS = ("docs/quickstart", "docs/guides", "docs/reference", "docs/explanations", "docs/releases")
FIXED = ("README.md", "SECURITY.md", "CONTRIBUTING.md", "docs/README.md", "docs/CLAIMS.md")

# Pages a later task writes. A link to one may dangle until then; one that exists must come off this list
# (tests/test_doc_links.py fails otherwise). guides/gpu.md is never listed: its own PR writes it.
NOT_YET: set[str] = set()

LINK = re.compile(r"\]\(([^)\s]+)\)|<img\b[^>]*?\bsrc=\"([^\"]+)\"")
EXTERNAL = re.compile(r"^(?:[a-z][a-z0-9+.-]*://|mailto:)", re.IGNORECASE)
FENCE = re.compile(r"^ *```")
HEADING = re.compile(r"^(#{1,6}) +(.*?) *#* *$")
ANCHOR = re.compile(r"<a id=\"([^\"]+)\"")


def public_docs(root: Path = ROOT) -> list[Path]:
    """README.md, SECURITY.md, CONTRIBUTING.md, docs/README.md and docs/CLAIMS.md where they exist, every .md in the
    five folders (the release notes among them), and the public review summaries (docs/reviews/0.1.0-*.md, not the -full reports)."""
    found = [root / name for name in FIXED if (root / name).is_file()]
    for folder in FOLDERS:
        found += (root / folder).glob("*.md")
    found += (p for p in (root / "docs/reviews").glob("0.1.0-*.md") if not p.stem.endswith("-full"))
    return sorted(set(found))


def flat(text: str) -> str:
    """Text in lower case with every run of whitespace one space, so a phrase is found across a line break."""
    return " ".join(text.split()).lower()


def ledger_cells(rid: str, root: Path = ROOT) -> list[str]:
    """The cells of a docs/CLAIMS.md row (ID, claim, pillar, proof, kind, status), or [] when it has no row."""
    for line in (root / "docs" / "CLAIMS.md").read_text(encoding="utf-8").splitlines():
        if line.startswith(f'| <a id="{rid.lower()}"></a>{rid} |'):
            return [c.strip() for c in line.strip().strip("|").split("|")]
    return []


def lines_outside_fences(doc: Path) -> list[tuple[int, str]]:
    """(line number, text) of every line of the doc that isn't inside a ``` block (the fence lines too)."""
    kept, inside = [], False
    for n, line in enumerate(doc.read_text(encoding="utf-8").splitlines(), 1):
        if FENCE.match(line):
            inside = not inside
        elif not inside:
            kept.append((n, line))
    return kept


def headings(doc: Path) -> list[tuple[int, int, str]]:
    """(line, level, text) of each # heading, skipping code blocks."""
    return [(n, len(m[1]), m[2]) for n, line in lines_outside_fences(doc) if (m := HEADING.match(line))]


def links(doc: Path) -> list[tuple[int, str]]:
    """(line, target) of each ](target) and <img src="target"> that isn't scheme://... or mailto:."""
    found = []
    for n, line in enumerate(doc.read_text(encoding="utf-8").splitlines(), 1):
        for m in LINK.finditer(line):
            target = m[1] or m[2]
            if not EXTERNAL.match(target):
                found.append((n, target))
    return found


def slug(text: str) -> str:
    """GitHub's anchor for a heading."""
    return re.sub(r"[^\w\- ]", "", text.lower()).replace(" ", "-")


def heading_slugs(doc: Path) -> set[str]:
    """GitHub's slug of each # heading, plus each <a id="x">."""
    found = {slug(text) for _n, _level, text in headings(doc)}
    return found | set(ANCHOR.findall(doc.read_text(encoding="utf-8")))


def section(doc: Path, heading: str) -> str:
    """The text under a heading line such as '## Passkeys and the address', up to the next heading of the same or a
    higher level. Code blocks stay in the text; a # inside one is not a heading."""
    level = len(heading) - len(heading.lstrip("#"))
    lines, inside, taking, out = doc.read_text(encoding="utf-8").splitlines(), False, False, []
    for line in lines:
        if FENCE.match(line):
            inside = not inside
        elif not inside and (m := HEADING.match(line)):
            if taking and len(m[1]) <= level:
                break
            if line.strip() == heading.strip():
                taking = True
                continue
        if taking:
            out.append(line)
    return "\n".join(out)
