"""The GitHub workflows (release spec 2026-11-05, section 6, PR 11). Every job runs only in the public repo,
steward-money/steward, so a private copy skips them. CI is read-only, every action is pinned to a commit, no stored
secret is used, and nothing from an event is pasted into a script."""
import os
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WF = ROOT / ".github" / "workflows"
WORKFLOWS = ("ci.yml", "release.yml")
REPO = "steward-money/steward"          # the public repo; release.yml names the images after its owner
GUARD = f"github.repository == '{REPO}'"
PINNED = re.compile(r"^\s*(?:- )?uses: [\w.-]+/[\w./-]+@[0-9a-f]{40} # v\d+(?:\.\d+)*$")


def load(name: str) -> dict:
    return yaml.safe_load((WF / name).read_text())


def triggers(wf: dict):
    return wf.get("on", wf.get(True))       # PyYAML reads a bare `on:` key as True


def steps(wf: dict):
    for job in wf["jobs"].values():
        yield from job.get("steps", [])


@pytest.mark.parametrize("name", WORKFLOWS)
def test_every_job_runs_only_in_the_public_repo(name):
    for job_id, job in load(name)["jobs"].items():
        assert job.get("if") == GUARD, f"{name}: job {job_id} needs if: {GUARD}"


@pytest.mark.parametrize("name", WORKFLOWS)
def test_every_action_is_pinned_to_a_commit_with_its_version(name):
    lines = [line for line in (WF / name).read_text().splitlines() if "uses:" in line]
    assert lines and [line for line in lines if not PINNED.match(line)] == []


@pytest.mark.parametrize("name", WORKFLOWS)
def test_no_stored_secret_and_nothing_untrusted_in_a_script(name):
    text = (WF / name).read_text()
    assert set(re.findall(r"secrets\.(\w+)", text)) <= {"GITHUB_TOKEN"}
    assert "pull_request_target" not in text and "workflow_run" not in text
    for step in steps(load(name)):
        assert "${{" not in step.get("run", ""), step.get("name") or step.get("run")


@pytest.mark.parametrize("name", WORKFLOWS)
def test_checkout_never_keeps_the_token(name):
    checkouts = [s for s in steps(load(name)) if s.get("uses", "").startswith("actions/checkout@")]
    assert checkouts and all(s["with"]["persist-credentials"] is False for s in checkouts)


def test_ci_runs_on_main_and_pull_requests_read_only():
    wf = load("ci.yml")
    assert triggers(wf) == {"push": {"branches": ["main"]}, "pull_request": None}
    assert wf["permissions"] == {"contents": "read"}
    assert all("permissions" not in job for job in wf["jobs"].values())


def test_ci_runs_every_suite_the_claims_and_the_secret_scan():
    runs = "\n".join(s.get("run", "") for s in steps(load("ci.yml")))
    for cmd in ("uv sync --frozen", "uv run pytest tests/test_claims.py -q", "uv run pytest -n auto -q", "npm ci",
                "npx vitest run", "npx tsc --noEmit", "npm run build",
                "go install github.com/zricethezav/gitleaks/v8@v8.30.1", "gitleaks git --config .gitleaks.toml",
                "--ignore-gitleaks-allow", "--gitleaks-ignore-path", "--redact"):
        assert cmd in runs, cmd


def test_ci_builds_every_image_without_pushing():
    job = load("ci.yml")["jobs"]["images"]
    assert job["strategy"]["matrix"]["include"] == [{"context": ".", "file": "Dockerfile"},
                                                    {"context": "deploy/proxy", "file": "deploy/proxy/Dockerfile"},
                                                    {"context": ".", "file": "deploy/gateway/Dockerfile"}]
    build = next(s for s in job["steps"] if s.get("uses", "").startswith("docker/build-push-action@"))
    assert build["with"]["push"] is False and build["with"]["platforms"] == "linux/amd64"


def test_ci_checks_the_gpu_dockerfiles_without_building_them():
    job = load("ci.yml")["jobs"]["gpu-dockerfiles"]
    runs = [s.get("run", "").strip() for s in job["steps"]]
    assert runs[-2:] == ["docker buildx build --check -f deploy/gateway/Dockerfile.cuda .",
                         "docker buildx build --check -f deploy/gateway/Dockerfile.rocm ."]
    assert any(s.get("uses", "").startswith("docker/setup-buildx-action@") for s in job["steps"])


def test_the_secret_scan_reads_the_whole_history_with_the_root_config():
    job = load("ci.yml")["jobs"]["gitleaks"]
    checkout = next(s for s in job["steps"] if s.get("uses", "").startswith("actions/checkout@"))
    assert checkout["with"]["fetch-depth"] == 0
    assert (ROOT / ".gitleaks.toml").is_file()


def test_release_runs_on_version_tags_only_with_no_default_permissions():
    wf = load("release.yml")
    assert triggers(wf) == {"push": {"tags": ["v*"]}}
    assert wf["permissions"] == {}


def test_release_signs_each_image_keyless_with_an_sbom_and_provenance():
    job = load("release.yml")["jobs"]["image"]
    assert job["permissions"] == {"contents": "read", "packages": "write", "id-token": "write"}
    assert job["strategy"]["matrix"]["include"] == [
        {"image": "steward", "context": ".", "file": "Dockerfile"},
        {"image": "steward-proxy", "context": "deploy/proxy", "file": "deploy/proxy/Dockerfile"},
        {"image": "steward-gateway", "context": ".", "file": "deploy/gateway/Dockerfile"}]
    name = next(s for s in job["steps"] if s.get("name") == "Name the image")     # ghcr.io/steward-money/<image>
    assert name["run"] == 'echo "IMAGE=ghcr.io/${GITHUB_REPOSITORY_OWNER,,}/${NAME}" >> "$GITHUB_ENV"'
    by = {s["uses"].split("@")[0]: s for s in job["steps"] if "uses" in s}
    assert {"sigstore/cosign-installer", "docker/setup-buildx-action", "docker/metadata-action"} <= set(by)
    assert by["docker/login-action"]["with"] == {"registry": "ghcr.io", "username": "${{ github.actor }}",
                                                 "password": "${{ secrets.GITHUB_TOKEN }}"}
    assert by["docker/metadata-action"]["with"]["flavor"] == "latest=false"
    build = by["docker/build-push-action"]
    assert build["id"] == "build"
    assert {k: build["with"][k] for k in ("platforms", "push", "sbom", "provenance")} == {
        "platforms": "linux/amd64", "push": True, "sbom": True, "provenance": "mode=max"}
    sign = next(s for s in job["steps"] if "cosign sign" in s.get("run", ""))
    assert sign["run"].strip() == 'cosign sign --yes "${IMAGE}@${DIGEST}"'       # by digest, never by tag
    assert sign["env"] == {"DIGEST": "${{ steps.build.outputs.digest }}"}
    text = (WF / "release.yml").read_text()
    assert "cuda" not in text and "rocm" not in text                               # Steward never publishes vendor software


def test_no_image_is_built_for_a_version_without_release_notes():
    jobs = load("release.yml")["jobs"]
    assert list(jobs) == ["notes", "image", "release"]
    notes = jobs["notes"]
    assert notes["permissions"] == {"contents": "read"}
    assert "needs" not in notes
    assert [s["run"] for s in notes["steps"] if "run" in s] == ['python3 scripts/release_notes.py check "$TAG"']
    assert notes["steps"][-1]["env"] == {"TAG": "${{ github.ref_name }}"}
    assert jobs["image"]["needs"] == "notes"


def test_the_release_job_publishes_the_notes_and_digests_with_contents_write_only():
    jobs = load("release.yml")["jobs"]
    job = jobs["release"]
    assert job["needs"] == "image"
    assert job["permissions"] == {"contents": "write"}
    by_name = {s.get("name"): s for s in job["steps"]}
    write = by_name["Write the notes with the image digests"]
    matrix = [row["image"] for row in jobs["image"]["strategy"]["matrix"]["include"]]
    assert write["env"]["IMAGES"].split() == matrix                   # every image the release pushes
    assert "docker buildx imagetools inspect \"$ref\" --format '{{json .Manifest}}'" in write["run"]
    assert 'python3 scripts/release_notes.py body "$TAG"' in write["run"]
    assert "set -euo pipefail" in write["run"]
    publish = by_name["Publish the GitHub Release"]
    assert publish["env"]["GH_TOKEN"] == "${{ secrets.GITHUB_TOKEN }}"    # by env, never in the script
    run = publish["run"]
    create = next(line.strip() for line in run.splitlines() if "gh release create" in line)
    assert create == 'gh release create "$TAG" --title "$title" --notes-file "$RUNNER_TEMP/notes.md" --verify-tag'
    assert 'title="Steward ${TAG#v}"' in run
    assert 'gh release edit "$TAG" --title "$title" --notes-file "$RUNNER_TEMP/notes.md"' in run   # a re-run updates


PLACEHOLDER = "ORG-" + "PLACEHOLDER"    # the org's stand-in before the repo had a name; split so this file passes
SKIP = {"node_modules", "dist", "__pycache__", "test-results", "playwright-report"}


def test_the_org_placeholder_is_gone_everywhere():
    """The repo's name is real now, so nothing may still say the placeholder: no step replaces it any more. Historical
    plans and specs (docs/superpowers, never exported) keep it."""
    hits = []
    for folder, dirs, files in os.walk(ROOT):
        here = Path(folder)
        dirs[:] = [d for d in dirs if d not in SKIP and (d == ".github" or not d.startswith("."))
                   and (here / d) != ROOT / "docs" / "superpowers"]
        for name in files:
            try:
                text = (here / name).read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            if PLACEHOLDER in text:
                hits.append((here / name).relative_to(ROOT).as_posix())
    assert hits == []
