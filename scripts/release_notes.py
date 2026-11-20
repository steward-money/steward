"""Release notes for a version tag, used by .github/workflows/release.yml. Standard library only, so the workflow runs
it with the runner's own python3.

  python3 scripts/release_notes.py check v0.1.2
      Prints docs/releases/0.1.2.md, or exits 1 and says why: the tag isn't vX.Y.Z (with an optional -suffix), the file
      is missing, or its ## headings aren't exactly HEADINGS, in order.

  python3 scripts/release_notes.py body v0.1.2 < digests
      Prints the notes file with an "## Image digests" section after it. Each stdin line is "<image>:<version>
      <sha256 digest>", such as "ghcr.io/steward-money/steward:0.1.2 sha256:...", and every image must carry the
      tag's version.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NOTES = "docs/releases"
HEADINGS = ("## What's new", "## Before you upgrade", "## Verify the images", "## Issues fixed")
TAG = re.compile(r"v(\d+\.\d+\.\d+(?:-[0-9A-Za-z.]+)?)")
IMAGE = re.compile(r"ghcr\.io/[a-z0-9-]+/[a-z0-9-]+")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
FENCE = re.compile(r"^ *```")


class NotesError(Exception):
    pass


def version_of(tag: str) -> str:
    m = TAG.fullmatch(tag)
    if not m:
        raise NotesError(f"{tag!r} isn't a version tag like v0.1.2")
    return m[1]


def headings(text: str) -> list[str]:
    """The ## lines outside ``` blocks."""
    found, inside = [], False
    for line in text.splitlines():
        if FENCE.match(line):
            inside = not inside
        elif not inside and line.startswith("## "):
            found.append(line.rstrip())
    return found


def check(tag: str, root: Path = ROOT) -> Path:
    """The notes file for the tag, after checking it exists and has exactly HEADINGS, in order."""
    rel = f"{NOTES}/{version_of(tag)}.md"
    path = root / rel
    if not path.is_file():
        raise NotesError(f"{rel} is missing: write the release notes before tagging")
    found = headings(path.read_text(encoding="utf-8"))
    if tuple(found) != HEADINGS:
        raise NotesError(f"{rel} has the sections {found}, not {list(HEADINGS)}")
    return path


def body(tag: str, digests: str, root: Path = ROOT) -> str:
    """The notes file and an "## Image digests" section listing each image, its tag and its digest."""
    version, notes = version_of(tag), check(tag, root).read_text(encoding="utf-8")
    rows = []
    for line in digests.splitlines():
        if not line.strip():
            continue
        parts = line.split()
        if len(parts) != 2:
            raise NotesError(f"{line!r} isn't '<image>:<version> <digest>'")
        ref, digest = parts
        image, _, ref_version = ref.rpartition(":")
        if not IMAGE.fullmatch(image) or ref_version != version:
            raise NotesError(f"{ref!r} isn't a ghcr.io image tagged {version}")
        if not DIGEST.fullmatch(digest):
            raise NotesError(f"{digest!r} isn't a sha256 digest")
        rows.append(f"- `{ref}` `{digest}`")
    if not rows:
        raise NotesError("no image digests")
    return notes.rstrip("\n") + "\n\n## Image digests\n\n" + "\n".join(rows) + "\n"


def main(argv: list[str]) -> int:
    try:
        if len(argv) == 2 and argv[0] == "check":
            print(check(argv[1]).relative_to(ROOT).as_posix())
        elif len(argv) == 2 and argv[0] == "body":
            sys.stdout.write(body(argv[1], sys.stdin.read()))
        else:
            print("usage: release_notes.py check <tag> | body <tag> < digests", file=sys.stderr)
            return 2
    except NotesError as e:
        print(f"release notes: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
