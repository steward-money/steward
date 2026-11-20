"""Spec 3.3 and 3.4: the NAS has no Anthropic client and no path to one; the boundary code imports neither side; heavy
model packages never reach the NAS image."""
import ast
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _pyproject() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text())


def _name(req: str) -> str:
    for sep in ("==", ">=", "<=", "~=", ">", "<", "[", ";", " "):
        req = req.split(sep)[0]
    return req.strip().lower()


def test_the_nas_app_has_no_anthropic_dependency():
    p = _pyproject()
    assert "anthropic" not in {_name(d) for d in p["project"]["dependencies"]}
    groups = p["dependency-groups"]
    assert "anthropic==2.8.0" in groups["gateway"]
    assert {"include-group": "gateway"} in groups["dev"]
    for heavy in ("gliner", "presidio-analyzer", "torch"):
        assert heavy not in {_name(d) for d in p["project"]["dependencies"]}
        assert heavy not in {_name(d) for d in groups["gateway"]}
        assert any(isinstance(d, str) and d.startswith(heavy + "==") for d in groups["models"])


def test_the_nas_image_installs_no_dependency_group():
    docker = (ROOT / "Dockerfile").read_text()
    assert "uv sync --frozen --no-dev" in docker
    assert "--group" not in docker and "--all-groups" not in docker and "--extra" not in docker


def _imports(path: Path):
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            yield from (a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            yield node.module


def test_no_nas_module_imports_anthropic_or_the_gateway():
    for path in sorted((ROOT / "src" / "steward").rglob("*.py")):
        for name in _imports(path):
            assert name.split(".")[0] not in ("anthropic", "steward_gateway"), f"{path} imports {name}"


def test_the_boundary_imports_nothing_from_either_side():
    for path in sorted((ROOT / "src" / "steward_boundary").rglob("*.py")):
        for name in _imports(path):
            top = name.split(".")[0]
            assert top not in ("steward", "steward_gateway", "anthropic", "fastapi", "sqlalchemy", "httpx2"), \
                f"{path} imports {name}"


def test_both_entry_points_are_declared():
    assert _pyproject()["project"]["scripts"] == {"steward": "steward.cli:app",
                                                  "steward-gateway": "steward_gateway.cli:app"}


def test_four_packages_are_built():
    wheel = _pyproject()["tool"]["hatch"]["build"]["targets"]["wheel"]
    assert wheel["packages"] == ["src/steward", "src/steward_boundary", "src/steward_gateway", "src/steward_pdf"]


def test_the_pdf_package_imports_nothing_from_either_side():
    """Remote-reading P56: both machines run steward_pdf; it needs neither the NAS app nor the gateway. It imports only
    the standard library, steward_boundary and, in the child, the PDF libraries and the dependencies of theirs that it
    imports before the wall (#12)."""
    allowed = set(sys.stdlib_module_names) | {"steward_pdf", "steward_boundary", "pdfplumber", "pypdfium2", "pdfminer",
                                              "cryptography", "PIL"}
    for path in sorted((ROOT / "src" / "steward_pdf").rglob("*.py")):
        for name in _imports(path):
            assert name.split(".")[0] in allowed, f"{path} imports {name}"


def test_the_gateway_never_imports_the_nas_app():
    """Remote-reading P56: the gateway runs without the NAS's code. Only the evals, which measure the NAS's own reading
    on the gateway, import it, lazily."""
    gw = ROOT / "src" / "steward_gateway"
    for path in sorted(gw.rglob("*.py")):
        if "evals" in path.relative_to(gw).parts:
            continue
        for name in _imports(path):
            assert name.split(".")[0] != "steward", f"{path} imports {name}"


def test_the_child_never_loads_the_nas_package():
    code = ("import sys, steward_pdf.sandbox, steward_pdf.lines; "
            "print(any(m == 'steward' or m.startswith('steward.') for m in sys.modules))")
    out = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, check=True).stdout
    assert out.strip() == "False"


def test_the_default_run_skips_perf_models_and_live():
    opts = _pyproject()["tool"]["pytest"]["ini_options"]
    assert opts["addopts"] == "-m 'not perf and not models and not live'"
    assert {m.split(":")[0] for m in opts["markers"]} == {"perf", "models", "live", "real_clock"}


def test_secrets_are_ignored():
    lines = (ROOT / ".gitignore").read_text().splitlines()
    assert {"*.env", "*.token", "history.key"} <= set(lines)
