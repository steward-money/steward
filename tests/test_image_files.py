"""The app image (release spec 2026-11-05, section 6): its three bases pinned by digest, its own non-root user, and
nothing secret baked in. A digest bump changes them here; this checks their shape."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIGEST = r"@sha256:[0-9a-f]{64}"


def dockerfile() -> str:
    return (ROOT / "Dockerfile").read_text()


def test_app_bases_are_pinned_by_digest():
    froms = re.findall(r"^FROM (\S+) AS (\w+)$", dockerfile(), re.M)
    assert [(re.sub(DIGEST + "$", "", ref), stage) for ref, stage in froms] == [
        ("node:26-alpine", "web"), ("python:3.12-slim", "base")]
    assert all(re.search(DIGEST + "$", ref) for ref, _ in froms)


def test_uv_comes_from_a_pinned_image():
    external = [r for r in re.findall(r"^COPY --from=(\S+) ", dockerfile(), re.M) if r != "web"]
    assert len(external) == 1 and re.fullmatch(re.escape("ghcr.io/astral-sh/uv:0.9") + DIGEST, external[0])


def test_app_runs_as_its_own_non_root_user():
    d = dockerfile()
    assert "RUN useradd -u 1000 -m steward" in d
    assert re.findall(r"^USER (\S+)$", d, re.M) == ["steward"]
    assert d.index("USER steward") < d.index('CMD ["steward", "serve"]')


def test_the_image_bakes_in_no_secret():
    d = dockerfile()
    assert not re.search(r"^ARG ", d, re.M)
    env = {}
    for line in d.splitlines():
        if line.startswith("ENV "):
            env.update(re.findall(r'(\w+)=("[^"]*"|\S+)', line[4:]))
    assert set(env) == {"STEWARD_DATA_DIR", "STEWARD_HOST", "STEWARD_PORT", "PATH"}
    ignored = (ROOT / ".dockerignore").read_text().splitlines()
    for pattern in ("data", "*.key", ".env*", "**/.playwright-mcp"):
        assert pattern in ignored, pattern


DOCKERFILES = sorted(p for p in ROOT.rglob("Dockerfile*")
                     if not {"node_modules", ".venv", ".claude"} & set(p.relative_to(ROOT).parts))
UV_CACHE_MOUNT = re.compile(r"--mount=type=cache,(?:\S*,)?target=/root/\.cache/uv\b")


def runs(text: str) -> list[str]:
    """A Dockerfile's RUN instructions, each with its continuation lines joined and its comments dropped."""
    joined = re.sub(r"\\\n(?:[ \t]*#[^\n]*\n)*", " ", "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#")))
    return [line[4:] for line in joined.splitlines() if line.startswith("RUN ")]


def uv_commands(run: str) -> list[str]:
    """Each uv command in a RUN, from uv to the next &&, ||, ;, | or end."""
    return [c.strip() for c in re.split(r"&&|\|\||;|\|", run) if re.match(r"\s*!?\s*(?:\S*/)?uv\s", c)]


def test_the_dockerfiles_are_found():
    assert {p.relative_to(ROOT).as_posix() for p in DOCKERFILES} == {
        "Dockerfile", "deploy/gateway/Dockerfile", "deploy/gateway/Dockerfile.cuda", "deploy/gateway/Dockerfile.rocm",
        "deploy/proxy/Dockerfile"}


def test_no_image_keeps_uvs_download_cache():
    """uv's cache (/root/.cache/uv) left in a layer is megabytes of wheels nobody runs. Each uv command passes --no-cache,
    or its RUN mounts a BuildKit cache at that folder or removes the folder before the layer ends."""
    using = set()
    for path in DOCKERFILES:
        for run in runs(path.read_text()):
            commands = uv_commands(run)
            if not commands:
                continue
            using.add(path.relative_to(ROOT).as_posix())
            if UV_CACHE_MOUNT.search(run) or re.search(r"&& rm -rf /root/\.cache/uv\s*$", run):
                continue
            for c in commands:
                assert re.search(r"\s--no-cache(\s|$)", c), f"{path.relative_to(ROOT)}: {c[:80]}"
    assert using == {"Dockerfile", "deploy/gateway/Dockerfile", "deploy/gateway/Dockerfile.cuda",
                     "deploy/gateway/Dockerfile.rocm"}


def test_the_uv_cache_check_catches_a_command_without_no_cache():
    run = runs("RUN uv sync --frozen --no-cache \\\n && uv pip list --python x | grep -q y\n")[0]
    assert [bool(re.search(r"\s--no-cache(\s|$)", c)) for c in uv_commands(run)] == [True, False]
    assert UV_CACHE_MOUNT.search("--mount=type=cache,target=/root/.cache/uv uv sync")
    assert uv_commands("python -c 'import uv' && echo uv") == []


def test_playwright_mcp_output_is_never_tracked():
    assert ".playwright-mcp/" in (ROOT / ".gitignore").read_text().splitlines()


def test_ci_builds_the_web_app_on_the_same_node_major_as_the_image():
    """The Dockerfile's web stage and CI's setup-node are two pins of one thing; a bump to one must move the other."""
    image = re.search(r"^FROM node:(\d+)-alpine", (ROOT / "Dockerfile").read_text(), re.M)
    ci = re.findall(r'node-version:\s*"?(\d+)', (ROOT / ".github/workflows/ci.yml").read_text())
    assert image and ci, "the Dockerfile's node stage or CI's setup-node moved"
    assert set(ci) == {image.group(1)}, f"CI uses Node {sorted(set(ci))}, the image Node {image.group(1)}"
