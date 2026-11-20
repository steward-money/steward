"""P2 (docs/CLAIMS.md): Steward's own code and pages send no telemetry. No dependency is an analytics or crash-report
package, no source or page names such a service's host, the page loads nothing from another origin, and the gateway
gateway turns off Hugging Face's telemetry on every install path (release spec 2026-11-05, sections 3 and 10)."""
import ast
import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

from steward.api.headers import content_security_policy

ROOT = Path(__file__).resolve().parents[1]

TELEMETRY = ["sentry", "posthog", "mixpanel", "segment", "amplitude", "datadog", "ddtrace", "newrelic", "bugsnag",
             "rollbar", "logrocket", "honeybadger", "opentelemetry-exporter", "@vercel/analytics", "plausible",
             "fullstory", "hotjar", "heap", "gtag", "google-analytics"]
TELEMETRY_HOSTS = ["google-analytics.com", "googletagmanager.com", "sentry.io", "ingest.sentry", "posthog.com",
                   "mixpanel.com", "segment.io", "amplitude.com", "datadoghq", "nr-data.net", "bugsnag.com",
                   "rollbar.com", "hotjar.com", "plausible.io"]
SCANNED = ("src/steward", "src/steward_boundary", "src/steward_gateway", "src/steward_pdf", "web/src", "web/public")
DEPENDENCY_FILES = ("pyproject.toml", "uv.lock", "web/package.json", "web/package-lock.json")
REQUIREMENT_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")
ANOTHER_ORIGIN = re.compile(r"^(?:[A-Za-z][A-Za-z0-9+.-]*:|//)")


def _requirement_names(requirements) -> set[str]:
    return {m.group(1) for r in requirements if isinstance(r, str) and (m := REQUIREMENT_NAME.match(r))}


def dependency_names(root: Path) -> set[str]:
    """Every package named in the dependency files under root that exist: Python's declared and locked packages, and
    the web's declared and installed ones."""
    names: set[str] = set()
    if (path := root / "pyproject.toml").exists():
        project = tomllib.loads(path.read_text(encoding="utf-8"))
        names |= _requirement_names(project.get("project", {}).get("dependencies", []))
        for group in (*project.get("project", {}).get("optional-dependencies", {}).values(),
                      *project.get("dependency-groups", {}).values(),
                      project.get("tool", {}).get("uv", {}).get("dev-dependencies", [])):
            names |= _requirement_names(group)
    if (path := root / "uv.lock").exists():
        names |= {p["name"] for p in tomllib.loads(path.read_text(encoding="utf-8")).get("package", [])}
    if (path := root / "web/package.json").exists():
        package = json.loads(path.read_text(encoding="utf-8"))
        for field in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
            names |= set(package.get(field, {}))
    if (path := root / "web/package-lock.json").exists():
        installed = json.loads(path.read_text(encoding="utf-8")).get("packages", {})
        names |= {key.rsplit("node_modules/", 1)[-1] for key in installed if key}
    return names


def telemetry_packages(root: Path) -> list[str]:
    return sorted(n for n in dependency_names(root) if any(t in n.lower() for t in TELEMETRY))


def scanned_files(root: Path) -> list[Path]:
    files = [p for d in SCANNED for p in (root / d).rglob("*") if p.is_file() and "__pycache__" not in p.parts]
    return files + [root / "web/index.html"]


def telemetry_hosts_named(root: Path) -> list[str]:
    hits = []
    for path in scanned_files(root):
        text = path.read_bytes().decode("utf-8", errors="ignore").lower()
        hits += [f"{path.relative_to(root)}: {host}" for host in TELEMETRY_HOSTS if host in text]
    return hits


def test_no_dependency_is_a_telemetry_or_analytics_package():
    assert all((ROOT / f).exists() for f in DEPENDENCY_FILES)
    assert len(dependency_names(ROOT)) > 200, "the dependency walk found too little to mean anything"
    assert telemetry_packages(ROOT) == []


def test_the_scan_covers_every_package_under_src():
    """PRIV-7: the gateway's own code was left out of the scan. Every package Steward ships is in it."""
    packages = {f"src/{p.name}" for p in (ROOT / "src").iterdir() if (p / "__init__.py").is_file()}
    assert packages and packages <= set(SCANNED), sorted(packages - set(SCANNED))


def test_no_code_or_page_names_a_telemetry_host():
    assert len(scanned_files(ROOT)) > 300, "the scan found too few files to mean anything"
    assert telemetry_hosts_named(ROOT) == []


def test_the_page_loads_nothing_from_another_origin():
    """index.html's src and href, every import in web/src and every @font-face url() are this origin's; the fonts and
    stylesheets main.tsx imports are bundled packages or relative files; and the CSP lets the page load scripts,
    styles, fonts and requests from 'self' alone."""
    page = (ROOT / "web/index.html").read_text(encoding="utf-8")
    links = re.findall(r"""\b(?:src|href)\s*=\s*["']([^"']*)["']""", page)
    assert links, "index.html has no src or href to check"
    assert [u for u in links if ANOTHER_ORIGIN.match(u)] == []

    sources = [p for p in (ROOT / "web/src").rglob("*") if p.suffix in (".ts", ".tsx", ".css") and p.is_file()]
    imports, font_urls = [], []
    for path in sources:
        text = path.read_text(encoding="utf-8")
        imports += [(path.name, s) for s in re.findall(
            r"""(?:\bfrom\s+|\bimport\s*\(?\s*|@import\s+(?:url\()?)["']([^"']+)["']""", text)]
        for block in re.findall(r"@font-face\s*{[^}]*}", text):
            font_urls += re.findall(r"""url\(\s*["']?([^"')]+)""", block)
    assert len(imports) > 100, "the import scan found too little to mean anything"
    # node: is Node's own modules (node:fs in tests), never a load from another origin
    assert [i for i in imports if ANOTHER_ORIGIN.match(i[1]) and not i[1].startswith("node:")] == []
    assert [u for u in font_urls if ANOTHER_ORIGIN.match(u)] == []

    main = (ROOT / "web/src/main.tsx").read_text(encoding="utf-8")
    styles = [s for s in re.findall(r"""^import\s+["']([^"']+)["']""", main, re.MULTILINE)
              if s.endswith(".css") or s.startswith("@fontsource")]
    assert styles, "main.tsx imports no fonts or stylesheets to check"
    assert all(s.startswith(("@fontsource", "./")) for s in styles), styles

    directives = dict(d.split(" ", 1) for d in content_security_policy(()).split("; "))
    for name in ("default-src", "script-src", "style-src", "font-src", "connect-src"):
        assert directives[name].split() == ["'self'"], name


def test_the_gateway_images_turn_off_hugging_face_telemetry():
    for name in ("Dockerfile", "Dockerfile.cuda", "Dockerfile.rocm"):
        text = (ROOT / "deploy/gateway" / name).read_text(encoding="utf-8")
        env = [line for line in text.splitlines() if line.startswith("ENV ")]
        assert any(re.search(r"(?:^|\s)HF_HUB_DISABLE_TELEMETRY=1(?:\s|$)", line) for line in env), name


def test_every_gateway_command_turns_off_hugging_face_telemetry():
    """PRIV-7: a gateway installed from the guide's systemd unit, outside the images, had the setting nowhere. The
    package sets it on import, before huggingface_hub reads it, so no install path can leave it out, even one that
    sets it to 0. The guide's unit says it too."""
    code = "import os, steward_gateway.cli; print(os.environ['HF_HUB_DISABLE_TELEMETRY'])"
    base = {k: v for k, v in os.environ.items() if k != "HF_HUB_DISABLE_TELEMETRY"}
    base["PYTHONPATH"] = os.pathsep.join(p for p in (str(ROOT / "src"), base.get("PYTHONPATH")) if p)
    for env in (base, {**base, "HF_HUB_DISABLE_TELEMETRY": "0"}):
        out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, cwd=ROOT)
        assert (out.returncode, out.stdout) == (0, "1\n"), out.stderr[-2000:]
    unit = (ROOT / "docs/guides/gateway.md").read_text(encoding="utf-8").split("[Service]\n", 1)[1].split("[Install]", 1)[0]
    assert "\n   Environment=HF_HUB_DISABLE_TELEMETRY=1\n" in unit


def test_the_telemetry_check_notices_a_telemetry_package(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\ndependencies = ["httpx2>=1", "sentry-sdk>=2.0"]\n')
    assert telemetry_packages(tmp_path) == ["sentry-sdk"]


def test_the_telemetry_check_notices_a_telemetry_host(tmp_path):
    (tmp_path / "web/src").mkdir(parents=True)
    (tmp_path / "web/src/boot.ts").write_text("load('https://www.googletagmanager.com/gtag/js');\n")
    (tmp_path / "web/index.html").write_text("<html></html>\n")
    assert telemetry_hosts_named(tmp_path) == ["web/src/boot.ts: googletagmanager.com"]


# Anthropic, AsyncAnthropic, and the SDK's other clients (AnthropicBedrock, AsyncAnthropicVertex, ...)
ANTHROPIC_CLIENT = re.compile(r"^(?:Async)?Anthropic[A-Za-z]*$")


def anthropic_clients(root: Path) -> list[tuple[str, bool]]:
    """Each place Steward's Python under src/ and deploy/ constructs an Anthropic SDK client, as ("file:line", whether
    the call passes open_telemetry=False)."""
    found = []
    for path in sorted(p for d in ("src", "deploy") for p in (root / d).rglob("*.py") if "__pycache__" not in p.parts):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            if ANTHROPIC_CLIENT.match(name):
                off = any(k.arg == "open_telemetry" and isinstance(k.value, ast.Constant) and k.value.value is False
                          for k in node.keywords)
                found.append((f"{path.relative_to(root)}:{node.lineno}", off))
    return found


def test_every_anthropic_client_has_its_tracing_off():
    """anthropic 1.14+ has OpenTelemetry built in: with a tracer provider in the process, a client left at the default
    makes spans and sends their trace context (traceparent) to Anthropic. Every client Steward constructs passes
    open_telemetry=False, so none does, whatever else in the process sets up OpenTelemetry."""
    clients = anthropic_clients(ROOT)
    files = {where.rsplit(":", 1)[0] for where, _ in clients}
    assert {"src/steward_gateway/app.py", "src/steward_gateway/cli.py"} <= files, "the scan found too few clients"
    assert [where for where, off in clients if not off] == []


def test_the_tracing_check_notices_a_client_left_at_the_default(tmp_path):
    (tmp_path / "src/pkg").mkdir(parents=True)
    # "sdk", not "anthropic": test_eval_answers counts a file that calls a client on the anthropic module as one that may
    # reach the real API
    (tmp_path / "src/pkg/a.py").write_text("import anthropic as sdk\nc = sdk.AsyncAnthropic(api_key='k', max_retries=0)\n"
                                           "d = sdk.Anthropic(api_key='k', open_telemetry=False)\n"
                                           "e = AnthropicBedrock(open_telemetry=None)\n")
    assert anthropic_clients(tmp_path) == [("src/pkg/a.py:2", False), ("src/pkg/a.py:3", True), ("src/pkg/a.py:4", False)]
