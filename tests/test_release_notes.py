"""The release notes in docs/releases (one per version) and scripts/release_notes.py, which release.yml runs: the notes
job refuses a tag whose notes are missing or lack the four sections, and the release job appends the image digests."""
import importlib.util
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("release_notes", ROOT / "scripts" / "release_notes.py")
rn = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rn)
NOTES = ROOT / "docs" / "releases"
VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
SCRIPT = ROOT / "scripts" / "release_notes.py"
RELEASE = ROOT / ".github" / "workflows" / "release.yml"
REPO_LINK = re.compile(r"https://github\.com/steward-money/steward/(?:blob|tree)/v([^/]+)/([^)#\s]+)")
DIGEST_OF = """| jq -r '.[0].critical.image."docker-manifest-digest"'"""
D1, D2 = "sha256:" + "a" * 64, "sha256:" + "b" * 64
GOOD = "\n\n".join(f"{h}\n\nText." for h in rn.HEADINGS) + "\n"


def numbers(version: str) -> tuple[int, int, int]:
    return tuple(int(n) for n in re.match(r"(\d+)\.(\d+)\.(\d+)", version).groups())


def notes_files() -> list[Path]:
    return sorted(NOTES.glob("*.md"))


def test_the_package_version_has_notes_with_the_four_sections_in_order():
    assert rn.check(f"v{VERSION}") == NOTES / f"{VERSION}.md"


def test_every_notes_file_is_a_released_or_current_version_with_the_four_sections():
    assert notes_files(), "docs/releases holds no notes"
    for path in notes_files():
        version = rn.version_of(f"v{path.stem}")
        assert numbers(version) <= numbers(VERSION), f"{path.name} is newer than the package ({VERSION})"
        assert rn.headings(path.read_text(encoding="utf-8")) == list(rn.HEADINGS), path.name


def test_each_notes_file_verifies_its_own_images_with_the_release_identity():
    regexp = re.search(r"--certificate-identity-regexp '([^']+)'", RELEASE.read_text())[1]
    for path in notes_files():
        text = path.read_text(encoding="utf-8")
        block = re.search(r"^```\n(cosign verify .*?)^```", text, re.S | re.M)
        assert block, f"{path.name} has no cosign verify block"
        verify = block[1]
        assert verify.startswith(f"cosign verify ghcr.io/steward-money/steward:{path.stem} "), path.name
        assert f"--certificate-identity-regexp '{regexp}'" in verify, path.name
        assert "--certificate-oidc-issuer https://token.actions.githubusercontent.com" in verify, path.name
        assert verify.rstrip().endswith(DIGEST_OF), path.name
        assert "cosign 3 or newer" in text and "digests are listed below" in text, path.name


def test_each_notes_file_links_its_own_tag_and_pages_that_exist():
    """Notes are read on the Releases page, where a relative link breaks, so they link the repo at their own tag. In
    the public tree only public files exist; tests/test_export.py checks the allowlist keeps each one."""
    bad = []
    for path in notes_files():
        links = REPO_LINK.findall(path.read_text(encoding="utf-8"))
        assert links, f"{path.name} links nothing"
        bad += [f"{path.name}: {rel} at v{version}" for version, rel in links if version != path.stem]
        bad += [f"{path.name}: {rel} doesn't exist" for _v, rel in links if not (ROOT / rel).exists()]
    assert bad == []


def scratch(tmp_path: Path, files: dict[str, str]) -> Path:
    for name, text in files.items():
        (tmp_path / "docs" / "releases").mkdir(parents=True, exist_ok=True)
        (tmp_path / "docs" / "releases" / name).write_text(text, encoding="utf-8")
    return tmp_path


def test_check_passes_present_notes_and_names_the_file(tmp_path):
    root = scratch(tmp_path, {"0.2.0.md": GOOD, "0.2.0-rc.1.md": GOOD})
    assert rn.check("v0.2.0", root) == root / "docs/releases/0.2.0.md"
    assert rn.check("v0.2.0-rc.1", root) == root / "docs/releases/0.2.0-rc.1.md"


def test_check_refuses_missing_notes(tmp_path):
    root = scratch(tmp_path, {"0.1.0.md": GOOD})
    with pytest.raises(rn.NotesError, match=r"docs/releases/0\.2\.0\.md is missing"):
        rn.check("v0.2.0", root)


@pytest.mark.parametrize("text", [
    GOOD.replace("## Verify the images", "## Verify"),                   # a heading renamed
    GOOD.replace("## Issues fixed\n\nText.\n", ""),                      # one missing
    GOOD.replace("## What's new", "### What's new"),                     # at the wrong level
    "## Before you upgrade\n\n## What's new\n\n## Verify the images\n\n## Issues fixed\n",   # out of order
    GOOD + "\n## Image digests\n\n- one\n",                              # the section the workflow adds
    GOOD.replace("## Issues fixed", "```\n## Issues fixed\n```"),        # only inside a code block
])
def test_check_refuses_notes_without_exactly_the_four_sections(tmp_path, text):
    root = scratch(tmp_path, {"0.2.0.md": text})
    with pytest.raises(rn.NotesError, match="has the sections"):
        rn.check("v0.2.0", root)


def test_a_heading_inside_a_code_block_is_not_a_section(tmp_path):
    root = scratch(tmp_path, {"0.2.0.md": GOOD.replace("Text.", "```\n## Not a heading\n```", 1)})
    assert rn.check("v0.2.0", root)


@pytest.mark.parametrize("tag", ["0.2.0", "v0.2", "v0.2.0/../../x", "va.b.c", "v0.2.0+build", "release-0.2.0"])
def test_check_refuses_a_tag_that_isnt_a_version(tmp_path, tag):
    with pytest.raises(rn.NotesError, match="isn't a version tag"):
        rn.check(tag, scratch(tmp_path, {"0.2.0.md": GOOD}))


def test_body_appends_each_image_its_tag_and_its_digest(tmp_path):
    root = scratch(tmp_path, {"0.2.0.md": GOOD})
    out = rn.body("v0.2.0", f"ghcr.io/steward-money/steward:0.2.0 {D1}\n\nghcr.io/steward-money/steward-proxy:0.2.0 {D2}\n",
                  root)
    assert out == GOOD + (f"\n## Image digests\n\n- `ghcr.io/steward-money/steward:0.2.0` `{D1}`\n"
                          f"- `ghcr.io/steward-money/steward-proxy:0.2.0` `{D2}`\n")


@pytest.mark.parametrize("digests, said", [
    ("", "no image digests"),
    (f"ghcr.io/steward-money/steward:0.1.9 {D1}", "tagged 0.2.0"),          # another version's image
    (f"docker.io/steward-money/steward:0.2.0 {D1}", "tagged 0.2.0"),        # another registry
    ("ghcr.io/steward-money/steward:0.2.0 null", "isn't a sha256 digest"),   # jq's answer when inspect has no digest
    ("ghcr.io/steward-money/steward:0.2.0 sha256:abc", "isn't a sha256 digest"),
    (f"ghcr.io/steward-money/steward:0.2.0 {D1} extra", "isn't '<image>:<version> <digest>'"),
])
def test_body_refuses_a_digest_list_it_cant_vouch_for(tmp_path, digests, said):
    with pytest.raises(rn.NotesError, match=re.escape(said)):
        rn.body("v0.2.0", digests, scratch(tmp_path, {"0.2.0.md": GOOD}))


def run(*args: str, stdin: str = "") -> subprocess.CompletedProcess:
    """The script as the workflow runs it: its own interpreter, on this repo's notes."""
    return subprocess.run([sys.executable, "-I", str(SCRIPT), *args], input=stdin, capture_output=True, text=True)


def test_the_script_exits_0_with_the_path_or_1_with_the_reason():
    ok = run("check", f"v{VERSION}")
    assert (ok.returncode, ok.stdout) == (0, f"docs/releases/{VERSION}.md\n")
    missing = run("check", "v999.0.0")
    assert missing.returncode == 1 and "docs/releases/999.0.0.md is missing" in missing.stderr
    assert run("check").returncode == 2
    body = run("body", f"v{VERSION}", stdin=f"ghcr.io/steward-money/steward:{VERSION} {D1}\n")
    assert body.returncode == 0 and body.stdout.endswith(f"## Image digests\n\n- `ghcr.io/steward-money/steward:{VERSION}` `{D1}`\n")
    assert run("body", f"v{VERSION}", stdin="").returncode == 1
