"""deploy/compose.yml and compose.demo.yml (release spec 2026-11-05, section 7): Steward pulled from GHCR on
127.0.0.1:8420 with ./data; the tailscale, domain and lan profiles; third-party images pinned by digest; nothing
published beyond this box unless .env says so; no /volume1. PR 13 adds the gateway and ollama profiles."""
import os
import re
import shutil
import subprocess
import tomllib
from pathlib import Path

import json
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy"
VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
OURS = re.compile(r"^ghcr\.io/steward-money/steward(-proxy|-gateway)?:(\S+)$")   # the images release.yml publishes
TAILSCALE = "tailscale/tailscale:v1.104.1@sha256:87c55d6be56ad85b3d8bceca5f87f73de288c609131460dd1be1874c250cc41d"
HEALTH = ["CMD", "python", "-c", "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8420/api/health')"]
OLLAMA = "ollama/ollama:0.40.16@sha256:910d0d8107b538fb0894e63163eb195de0cc7aefcbc666dc62d3b18f71124592"
GATEWAY_HEALTH = ["CMD", "python", "-c", "import socket;socket.create_connection(('127.0.0.1', 8430), 5).close()"]
GATEWAY_NETS = ["gateway-private", "gateway-egress"]
PROXY_PORT = "${STEWARD_PROXY_BIND:-127.0.0.1}:"
LOCKED = {"cap_drop": ["ALL"], "security_opt": ["no-new-privileges:true"]}
GENERIC = ("compose.yml", "compose.demo.yml", ".env.example", "tailscale/serve.json", "tailscale/tailscale.env.example",
           "proxy/proxy.env.example", "proxy/start.sh", "proxy/Dockerfile", "proxy/Caddyfile.dns-cloudflare",
           "proxy/Caddyfile.http", "proxy/Caddyfile.internal", "gateway/Dockerfile", "gateway/gateway.toml",
           "gateway/torch-cpu.txt")


def compose(name="compose.yml") -> dict:
    return yaml.safe_load((DEPLOY / name).read_text())


def services(name="compose.yml") -> dict:
    return compose(name)["services"]


def test_steward_is_the_release_image_on_localhost_with_the_data_folder():
    s = services()["steward"]
    assert s["image"] == f"ghcr.io/steward-money/steward:{VERSION}"
    assert s["ports"] == ["127.0.0.1:8420:8420"] and s["volumes"] == ["./data:/data"]
    assert s["env_file"] == ".env" and s["restart"] == "unless-stopped" and "profiles" not in s
    assert s["healthcheck"]["test"] == HEALTH
    assert {k: s[k] for k in LOCKED} == LOCKED


def test_the_profiles_are_tailscale_domain_lan_gateway_and_ollama():
    assert {n: s.get("profiles") for n, s in services().items()} == {
        "steward": None, "tailscale": ["tailscale"], "proxy": ["domain"], "proxy-lan": ["lan"],
        "gateway": ["gateway"], "ollama": ["ollama"], "gateway-nvidia": ["gateway-nvidia"],
        "ollama-nvidia": ["gateway-nvidia"], "gateway-amd": ["gateway-amd"], "ollama-amd": ["gateway-amd"],
        "ollama-pull": ["ollama-pull"]}   # its own profile: `up` never starts it, `docker compose run` does


def test_nothing_is_published_beyond_this_box_unless_env_says_so():
    for name in ("compose.yml", "compose.demo.yml"):
        for svc, s in services(name).items():
            for port in s.get("ports", []):
                if svc.startswith("proxy"):
                    assert port.startswith(PROXY_PORT), (name, svc, port)
                else:
                    assert port.startswith("127.0.0.1:"), (name, svc, port)
    assert "ports" not in services()["tailscale"]
    assert services()["proxy"]["ports"] == [f"{PROXY_PORT}443:443", f"{PROXY_PORT}80:80"]
    assert services()["proxy-lan"]["ports"] == [f"{PROXY_PORT}443:443"]


def test_every_image_is_ours_at_this_version_or_pinned_by_digest():
    for name in ("compose.yml", "compose.demo.yml"):
        for svc, s in services(name).items():
            m = OURS.match(s["image"])
            built_here = "build" in s and s["image"].endswith(":local")      # a GPU gateway, built on the person's machine
            assert (m and m[2] == VERSION) or re.search(r"@sha256:[0-9a-f]{64}$", s["image"]) or built_here, (name, svc)
    assert services()["tailscale"]["image"] == TAILSCALE


def test_every_steward_money_image_tag_is_the_package_version():
    """A release bumps pyproject.toml, steward.__version__ and every tag of ours in deploy/*.yml together."""
    from steward import __version__
    assert VERSION == __version__
    tags = [(p.name, m[1]) for p in sorted(DEPLOY.glob("*.yml"))
            for m in re.finditer(r"ghcr\.io/steward-money/steward(?:-proxy|-gateway)?:([^\s@\"']+)", p.read_text())]
    assert len(tags) >= 4 and all(t == __version__ for _, t in tags), tags


def test_the_demo_runs_the_same_release_image():
    assert services("compose.demo.yml")["steward"]["image"] == services()["steward"]["image"]


def test_the_tailscale_sidecar_serves_steward_over_https_and_never_funnels():
    s = services()["tailscale"]
    assert s["environment"]["TS_SERVE_CONFIG"] == "/config/serve.json" and s["environment"]["TS_USERSPACE"] == "true"
    assert s["volumes"] == ["tailscale-state:/var/lib/tailscale", "./tailscale/serve.json:/config/serve.json:ro"]
    assert s["env_file"] == [{"path": "tailscale.env", "required": False}]
    assert s["security_opt"] == ["no-new-privileges:true"]
    serve = json.loads((DEPLOY / "tailscale" / "serve.json").read_text())
    assert serve["TCP"] == {"443": {"HTTPS": True}}
    assert serve["Web"] == {"${TS_CERT_DOMAIN}:443": {"Handlers": {"/": {"Proxy": "http://steward:8420"}}}}
    assert not any(serve.get("AllowFunnel", {}).values())          # never on the public internet


def test_the_tailscale_sidecar_keeps_its_logs_on_this_box():
    """PRIV-6, J11: tailscaled uploads its own logs to Tailscale by default. compose's environment wins over
    tailscale.env, so the flag holds whatever that file says. deploy/nas/compose.yml is JR's and stays as it is."""
    env = services()["tailscale"]["environment"]
    assert env["TS_TAILSCALED_EXTRA_ARGS"].split() == ["--no-logs-no-support"]
    assert "TS_TAILSCALED_EXTRA_ARGS" not in (DEPLOY / "tailscale" / "tailscale.env.example").read_text()


def test_the_proxies_are_locked_down_and_keep_their_secrets_to_themselves():
    for name in ("proxy", "proxy-lan"):
        s = services()[name]
        assert s["image"] == f"ghcr.io/steward-money/steward-proxy:{VERSION}"
        assert s["read_only"] is True and s["tmpfs"] == ["/tmp"] and s["user"] == "0:0"
        assert s["cap_drop"] == ["ALL"] and s["cap_add"] == ["NET_BIND_SERVICE"]
        assert s["security_opt"] == ["no-new-privileges:true"] and s["volumes"] == ["caddy-data:/data"]
        assert s["env_file"] == [{"path": "proxy.env", "required": False}]
    assert "environment" not in services()["proxy"]                     # its mode comes from proxy.env
    assert services()["proxy-lan"]["environment"] == {"STEWARD_TLS": "internal"}
    assert set(compose()["volumes"]) == {"tailscale-state", "caddy-data", "gateway-config", "gateway-state", "gateway-models",
                                       "ollama-models"}


def test_the_gateway_is_our_image_with_its_own_volumes_and_a_health_check():
    s = services()["gateway"]
    assert s["image"] == f"ghcr.io/steward-money/steward-gateway:{VERSION}"
    assert s["restart"] == "unless-stopped" and "env_file" not in s and "environment" not in s
    assert s["volumes"] == ["gateway-config:/etc/steward-gateway", "gateway-state:/var/lib/steward-gateway",
                            "gateway-models:/var/cache/steward-gateway"]
    assert {k: s[k] for k in LOCKED} == LOCKED
    assert s["healthcheck"]["test"] == GATEWAY_HEALTH and s["healthcheck"]["start_period"] == "15m"


def test_the_gateway_and_ollama_publish_nothing_and_sit_on_their_own_networks():
    """Release spec 2026-11-05, section 7: a private network with Steward, a second one with egress. Claim S12."""
    c, svc = compose(), services()
    assert c["networks"] == {"gateway-private": {"internal": True}, "gateway-egress": {}}
    assert "ports" not in svc["gateway"] and svc["gateway"]["networks"] == GATEWAY_NETS
    assert "ports" not in svc["ollama"] and svc["ollama"]["networks"] == ["gateway-private"]
    assert svc["steward"]["networks"] == ["default", "gateway-private"]
    for name in ("tailscale", "proxy", "proxy-lan"):
        assert "networks" not in svc[name], name             # default only: the tailnet and the LAN never reach them


COMPOSE_NAMES_ONLY = {"dns_search": ["."], "dns_opt": ["ndots:1"]}


def test_the_bare_names_gateway_and_ollama_are_answered_only_by_compose():
    """With Docker's default DNS options a container asks the box's resolver for a bare name no container holds, so
    with the ollama profile left out (or the gateway stopped) http://ollama:11434 and http://gateway:8430 would reach
    whatever the LAN calls ollama or gateway. An empty search list and ndots:1 keep those names inside Compose."""
    svc = services()
    on_private = {n for n, s in svc.items() if "gateway-private" in s.get("networks", [])}
    assert on_private == {"steward", "gateway", "ollama", "gateway-nvidia", "ollama-nvidia", "gateway-amd",
                          "ollama-amd"}   # a new service here must decide whether it needs the keys
    for name in ("steward", "gateway", "gateway-nvidia", "gateway-amd"):                       # the two that call another by bare name; Ollama calls none
        assert {k: svc[name].get(k) for k in COMPOSE_NAMES_ONLY} == COMPOSE_NAMES_ONLY, name


def test_ollama_is_pinned_and_keeps_its_models():
    o = services()["ollama"]
    assert o["image"] == OLLAMA and o["restart"] == "unless-stopped"
    assert o["volumes"] == ["ollama-models:/root/.ollama"] and {k: o[k] for k in LOCKED} == LOCKED


OLLAMAS = ("ollama", "ollama-nvidia", "ollama-amd", "ollama-pull")
# Ollama 0.40.15's envconfig reads OLLAMA_NO_CLOUD (docs/faq.mdx, "How do I disable Ollama's cloud features?"). Off,
# a model name ending in :cloud can't send a description to Ollama's hosted models, and the server's timer stops
# fetching model recommendations from ollama.com (server/model_recommendations.go), which it does every four hours
# from its start otherwise. Pulling a local model isn't a cloud feature and still works.
NO_CLOUD = {"OLLAMA_NO_CLOUD": "1"}
# An Ollama starting up deletes blobs no model names, half-done downloads included. With every Ollama on the shared
# volume set not to, a restart of the serving one, or a second pull, never deletes a pull's partial download.
NO_PRUNE = {"OLLAMA_NOPRUNE": "1"}


def test_ollama_reads_descriptions_with_no_way_out_and_its_cloud_features_off():
    """Public issue #4: Ollama reads raw descriptions, statement pages and redacted questions. It sits only on
    gateway-private, which is internal, so with no route out it contacts nothing, idle or categorizing. Claims P1, P9."""
    svc = services()
    assert compose()["networks"]["gateway-private"] == {"internal": True}
    for name in ("ollama", "ollama-nvidia", "ollama-amd"):
        assert list(svc[name]["networks"]) == ["gateway-private"], name
        assert "ports" not in svc[name], name
    for name in OLLAMAS:
        assert {k: v for k, v in svc[name].get("environment", {}).items() if k in NO_CLOUD} == NO_CLOUD, name
        assert {k: v for k, v in svc[name].get("environment", {}).items() if k in NO_PRUNE} == NO_PRUNE, name
    on_egress = {n for n, s in svc.items() if "gateway-egress" in s.get("networks", [])}
    assert on_egress == {"gateway", "gateway-nvidia", "gateway-amd", "ollama-pull"}


def test_a_model_is_pulled_by_a_one_shot_service_on_egress_that_never_serves_the_gateway():
    """Public issue #4: `docker compose run --rm ollama-pull <model>` starts Ollama's own server inside a one-shot
    container on gateway-egress, listening only on its loopback, pulls into the shared volume and exits. It never joins
    gateway-private, so nothing the gateway sends reaches a container that can reach the internet."""
    p, o = services()["ollama-pull"], services()["ollama"]
    assert p["image"] == o["image"] == OLLAMA and p["volumes"] == o["volumes"]
    assert p["networks"] == ["gateway-egress"] and "ports" not in p
    assert p["restart"] == "no" and p["profiles"] == ["ollama-pull"]
    assert p["environment"] == {**NO_CLOUD, **NO_PRUNE, "OLLAMA_HOST": "127.0.0.1:11434"}
    assert {k: p[k] for k in LOCKED} == LOCKED
    script = p["entrypoint"]
    assert script[:2] == ["/bin/sh", "-c"] and script[3] == "ollama-pull"
    assert re.search(r"/bin/ollama serve .*&", script[2]) and script[2].rstrip().endswith('exec /bin/ollama pull "$$1"')


def test_the_env_example_points_at_the_compose_gateway():
    """Guided setup spec 1.3 and 5: the same address the Ask row offers, commented until the profile is on."""
    url = "STEWARD_GATEWAY_URL=http://gateway:8430"
    env = (DEPLOY / ".env.example").read_text()
    assert f"\n# {url}\n" in env and f"\n{url}" not in env
    assert url in (ROOT / "src" / "steward" / "services" / "ready.py").read_text()
    assert "COMPOSE_PROFILES=tailscale,gateway,ollama" in env


def test_the_demo_runs_on_localhost_only_with_a_throwaway_volume():
    """Release spec 2026-11-05, section 6 (deferred from PR 7): compose.demo.yml publishes only on 127.0.0.1."""
    c = compose("compose.demo.yml")
    s = c["services"]["steward"]
    # Install gate INS-8: beside a running install (which holds 8420), STEWARD_DEMO_PORT moves the port and the address.
    assert list(c["services"]) == ["steward"] and s["ports"] == ["127.0.0.1:${STEWARD_DEMO_PORT:-8420}:8420"]
    assert s["volumes"] == ["demo-data:/data"] and set(c["volumes"]) == {"demo-data"} and "env_file" not in s
    assert s["environment"] == {"STEWARD_BASE_URL": "http://localhost:${STEWARD_DEMO_PORT:-8420}", "STEWARD_ENV": "demo",
                                "STEWARD_ALLOW_DEMO": "1", "STEWARD_BACKUP_AT": "", "STEWARD_SYNC_HOURS": "0"}
    assert "steward seed-demo" in s["command"][-1] and "/data/.demo-seeded" in s["command"][-1]
    assert {k: s[k] for k in LOCKED} == LOCKED


def test_the_real_compose_never_turns_on_the_demo_or_a_non_prod_env():
    for name, s in services().items():
        e = s.get("environment") or {}
        assert "STEWARD_ALLOW_DEMO" not in e and e.get("STEWARD_ENV", "prod") == "prod", name
    for line in (DEPLOY / ".env.example").read_text().splitlines():
        key, _, value = line.lstrip("# ").partition("=")
        assert key != "STEWARD_ALLOW_DEMO" and (key != "STEWARD_ENV" or value == "prod"), line


APP_STORABLE = ("STEWARD_SMTP_HOST", "STEWARD_SMTP_PORT", "STEWARD_SMTP_USER", "STEWARD_SMTP_PASSWORD",
                "STEWARD_MAIL_FROM", "STEWARD_TIMEZONE", "STEWARD_BACKUP_AT", "STEWARD_BACKUP_PING_URL")


def test_the_env_example_is_compose_safe_and_keeps_secrets_empty():
    lines = [l for l in (DEPLOY / ".env.example").read_text().splitlines() if l.strip() and not l.startswith("#")]
    assert all(re.fullmatch(r"[A-Z_]+=[^$'\"]*", l) for l in lines), lines
    assert dict(l.split("=", 1) for l in lines)["STEWARD_BASE_URL"] == "http://localhost:8420"


def test_the_env_example_leaves_app_settings_to_the_app(monkeypatch, tmp_path):
    """Guided setup spec 4.3: a line here wins, even empty, so the example sets none of them; each is commented."""
    from steward.settings import Settings
    text = (DEPLOY / ".env.example").read_text()
    for key in APP_STORABLE:
        assert f"# {key}=" in text and f"\n{key}=" not in text, key
    for k in [k for k in os.environ if k.startswith("STEWARD_")]:
        monkeypatch.delenv(k)
    s = Settings(_env_file=DEPLOY / ".env.example", data_dir=tmp_path)
    assert not {"smtp_host", "smtp_port", "smtp_user", "smtp_password", "mail_from", "timezone", "backup_at",
                "backup_ping_url"} & s.model_fields_set
    assert s.backup_at == "03:00" and s.backup_ping_url == ""


def test_the_compose_header_says_to_give_steward_its_data_folder():
    assert "sudo chown 1000:1000 data" in (DEPLOY / "compose.yml").read_text()


@pytest.mark.parametrize("rel", GENERIC)
def test_no_generic_file_names_the_nas(rel):
    assert "/volume1" not in (DEPLOY / rel).read_text()


def test_compose_parses_with_every_profile(tmp_path):
    if shutil.which("docker") is None or subprocess.run(["docker", "compose", "version"], capture_output=True).returncode:
        pytest.skip("no docker compose here")
    for rel in ("compose.yml", "compose.demo.yml", "tailscale/serve.json"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(DEPLOY / rel, tmp_path / rel)
    shutil.copy(DEPLOY / ".env.example", tmp_path / ".env")
    env = {**os.environ, "COMPOSE_PROFILES": ""}
    for args in (["-f", "compose.yml", "--profile", "tailscale", "--profile", "domain", "--profile", "lan",
                  "--profile", "gateway", "--profile", "ollama"],
                 ["-f", "compose.yml", "--profile", "gateway-nvidia", "--profile", "gateway-amd"],   # they replace the two
                 ["-f", "compose.demo.yml"]):
        p = subprocess.run(["docker", "compose", "--project-directory", str(tmp_path), *args, "config", "-q"],
                           cwd=tmp_path, env=env, capture_output=True, text=True)
        assert p.returncode == 0, p.stderr


OLLAMA_ROCM = "ollama/ollama:0.40.16-rocm@sha256:85b3c895222b1ddc9d63c40b20fc74f37a37593dcbbdfbf28910a925e43ead00"
# Both networks answer to "gateway": a GPU gateway must be found by the two-gateways check on either one.
ALIASED = {"gateway-private": {"aliases": ["gateway"]}, "gateway-egress": {"aliases": ["gateway"]}}
NVIDIA_GPU = {"resources": {"reservations": {"devices": [{"driver": "nvidia", "count": "all", "capabilities": ["gpu"]}]}}}
ACCEPT = {"STEWARD_GPU_LICENSE_ACCEPTED": "${STEWARD_GPU_LICENSE_ACCEPTED:-}"}


# Public issue #11: the GPU gateways once built from a tag on GitHub, which can be moved to other code. They build from
# the clone compose.yml sits in (docs/quickstart/install.md clones it), so what is built is the source beside the file.
# Compose hands a context to BuildKit, which fetches a URL, git@..., github.com/... or anything ending .git or holding #.
REMOTE = re.compile(r"^(?:[a-z][a-z0-9+.-]*://|git@|github\.com/)|\.git$|#", re.I)
COMPOSE_FILES = sorted(DEPLOY.rglob("compose*.yml"))


@pytest.mark.parametrize("vendor, image", [("nvidia", "cuda"), ("amd", "rocm")])
def test_a_gpu_gateway_is_built_on_this_machine_from_this_clone_and_never_pulled(vendor, image):
    s = services()[f"gateway-{vendor}"]
    assert s["image"] == f"steward-gateway-{image}:local" and s["pull_policy"] == "build"
    assert s["build"] == {"context": "..", "dockerfile": f"deploy/gateway/Dockerfile.{image}", "args": ACCEPT}
    assert (DEPLOY / s["build"]["context"]).resolve() == ROOT
    assert (ROOT / s["build"]["dockerfile"]).is_file()


def contexts(path: Path) -> list[tuple[str, str]]:
    """Every build context a compose file names, with its service: the main one and any additional_contexts."""
    out = []
    for name, s in (yaml.safe_load(path.read_text()).get("services") or {}).items():
        b = s.get("build")
        if isinstance(b, str):
            out.append((name, b))
        elif b is not None:
            extra = b.get("additional_contexts") or {}
            extra = extra.values() if isinstance(extra, dict) else [e.split("=", 1)[-1] for e in extra]
            out += [(name, c) for c in (b.get("context", "."), *extra)]
    return out


@pytest.mark.parametrize("path", COMPOSE_FILES, ids=lambda p: str(p.relative_to(DEPLOY)))
def test_no_compose_file_builds_from_a_remote_source(path):
    assert [(n, c) for n, c in contexts(path) if REMOTE.search(c)] == []


def test_the_remote_source_check_finds_a_git_or_http_context(tmp_path):
    for c in ("https://github.com/steward-money/steward.git#v0.1.0", "http://example.com/ctx.tar.gz",
              "git://github.com/x/y", "git@github.com:x/y.git", "github.com/x/y", "ssh://git@github.com/x/y",
              "example.com/x/y.git", "../steward#main"):
        assert REMOTE.search(c), c
    for c in ("..", ".", "../deploy", "gateway", "proxy"):
        assert not REMOTE.search(c), c
    compose = {"services": {"a": {"build": {"context": "..", "additional_contexts": {"src": "https://x/y.git"}}},
                            "b": {"build": {"context": ".", "additional_contexts": ["t=github.com/x/y"]}},
                            "c": {"build": "git@github.com:x/y.git"}, "d": {"image": "x"}}}
    path = tmp_path / "compose.yml"
    path.write_text(yaml.safe_dump(compose))
    assert [c for _, c in contexts(path) if REMOTE.search(c)] == ["https://x/y.git", "github.com/x/y",
                                                                  "git@github.com:x/y.git"]


def test_the_gpu_build_context_leaves_out_the_households_data_and_settings():
    """The context is the clone's top folder, and deploy/ beside compose.yml holds data/ and the settings files."""
    ignored = (ROOT / ".dockerignore").read_text().splitlines()
    for pattern in ("deploy/data", "**/.env*", "**/*.env", "**/*.db", "**/*.key", "**/*.token"):
        assert pattern in ignored, pattern


@pytest.mark.parametrize("vendor", ["nvidia", "amd"])
def test_a_gpu_profile_brings_a_gpu_gateway_that_answers_as_gateway(vendor):
    s, cpu = services()[f"gateway-{vendor}"], services()["gateway"]
    assert s["networks"] == ALIASED and s["environment"] == ACCEPT and "ports" not in s
    for key in ("volumes", "healthcheck", "cap_drop", "security_opt", "restart"):
        assert s[key] == cpu[key], key                   # the same volumes: the key, pairing and models carry over


def test_the_gpu_ollamas_answer_as_ollama_and_wait_for_their_gateway():
    for vendor, image in (("nvidia", OLLAMA), ("amd", OLLAMA_ROCM)):
        o = services()[f"ollama-{vendor}"]
        assert o["image"] == image and "ports" not in o and "build" not in o
        assert o["networks"] == {"gateway-private": {"aliases": ["ollama"]}}
        assert o["volumes"] == ["ollama-models:/root/.ollama"]
        assert o["depends_on"] == {f"gateway-{vendor}": {"condition": "service_healthy"}}   # a refused license stops both


def test_nvidia_reserves_the_gpu_and_amd_passes_its_devices():
    svc = services()
    assert svc["gateway-nvidia"]["deploy"] == NVIDIA_GPU and svc["ollama-nvidia"]["deploy"] == NVIDIA_GPU
    for name in ("gateway-amd", "ollama-amd"):
        assert svc[name]["devices"] == ["/dev/kfd", "/dev/dri"], name
    assert svc["gateway-amd"]["group_add"] == ["${STEWARD_RENDER_GID:-video}"]
    for name in ("gateway", "ollama", "steward"):
        assert "deploy" not in svc[name] and "devices" not in svc[name], name


def test_the_env_example_offers_the_gpu_settings_commented():
    env = (DEPLOY / ".env.example").read_text()
    for line in ("# STEWARD_GPU_LICENSE_ACCEPTED=nvidia", "# STEWARD_RENDER_GID="):
        assert f"\n{line}\n" in env, line
    assert "\nSTEWARD_GPU_LICENSE_ACCEPTED=" not in env and "instead of gateway and ollama" in env


GUIDE = ROOT / "docs" / "guides" / "gateway.md"


def test_the_gateway_guide_gives_the_commands_compose_runs():
    doc = GUIDE.read_text()
    for heading in ("## What works without it", "## On the same box, with compose", "## The Claude API key",
                    "## Pairing", "## Reading PDFs"):
        assert f"\n{heading}\n" in doc, heading
    for line in ("COMPOSE_PROFILES=tailscale,gateway,ollama", "STEWARD_GATEWAY_URL=http://gateway:8430",
                 "docker compose run --rm --no-deps gateway bash -c 'umask 077; read -rsp",
                 "docker compose run --rm --no-deps gateway steward-gateway check",
                 "docker compose exec gateway steward-gateway pair",
                 "docker compose exec gateway steward-gateway pdf-check",
                 "docker compose run --rm ollama-pull qwen3.5:9b-q4_K_M",
                 "docker compose cp gateway:/etc/steward-gateway/gateway.toml gateway.toml",
                 "docker compose run --rm --no-deps gateway steward-gateway eval redaction",
                 "[S12](../CLAIMS.md#s12)", "[Speed up the gateway with a GPU](gpu.md)"):
        assert line in doc, line
    assert "Local model: none yet. Ollama isn't answering at http://ollama:11434" in doc


def test_optional_env_files_are_why_compose_2_24_is_the_floor():
    """Compose added `required` to env_file entries in 2.24.0; older Compose refuses compose.yml, so install.md names
    2.24 as the floor."""
    optional = [name for name, svc in services().items()
                if any(isinstance(e, dict) and e.get("required") is False for e in svc.get("env_file", []))]
    assert optional
    assert "Compose 2.24" in (ROOT / "docs/quickstart/install.md").read_text()
