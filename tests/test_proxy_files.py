"""deploy/proxy/: the steward-proxy image's pinned versions, its locked-down Caddyfile, and its entrypoint's checks."""
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PROXY = ROOT / "deploy/proxy"
# Pinned versions, kept in step with deploy/proxy/Dockerfile.
CADDY = "2.11.9"
CLOUDFLARE_MODULE = "v0.2.4"
BUILDER = "caddy:2.11.9-builder@sha256:345264d8c6b50f44f4a4d507d4e6b6bcae40eef2f210d70f76e717fd8cc31baf"
RUNTIME = "caddy:2.11.9-alpine@sha256:003a97808e781aa9f7e75cc2f43d34d461099031faf5e20faefb9f61126836f0"
TOKEN = "0" * 40
CURL = "8.23.0-r0"      # Alpine's curl in the runtime base's branch; the build fails once Alpine drops it


def dockerfile() -> str:
    return (PROXY / "Dockerfile").read_text()


def test_proxy_installs_only_a_pinned_curl():
    assert re.findall(r"apk add (.*?) &&", dockerfile()) == [f"--no-cache curl={CURL}"]


def test_proxy_bases_are_pinned_by_digest():
    assert re.findall(r"^FROM (\S+)", dockerfile(), re.M) == [BUILDER, RUNTIME]


def test_proxy_builds_the_pinned_caddy_with_the_pinned_cloudflare_module():
    assert f"RUN xcaddy build v{CADDY} --with github.com/caddy-dns/cloudflare@{CLOUDFLARE_MODULE}\n" in dockerfile()
    assert BUILDER.startswith(f"caddy:{CADDY}-") and RUNTIME.startswith(f"caddy:{CADDY}-")


def test_proxy_binary_is_built_without_file_capabilities():
    # The builder image sets XCADDY_SETCAP=1; compose drops all capabilities, so a binary with file caps can't exec.
    assert "ENV XCADDY_SETCAP=0\n" in dockerfile()


def test_proxy_runs_as_its_own_user_through_the_checking_entrypoint():
    d = dockerfile()
    assert re.findall(r"^USER (\S+)", d, re.M) == ["1001:1001"]
    assert d.rstrip().endswith('ENTRYPOINT ["/usr/local/bin/steward-proxy"]')
    assert "grep -qx dns.providers.cloudflare" in d and "caddy validate" in d
    assert "COPY --chmod=0755 start.sh /usr/local/bin/steward-proxy" in d


def test_the_cloudflare_caddyfile_listens_only_on_443_and_never_holds_a_secret():
    c = (PROXY / "Caddyfile.dns-cloudflare").read_text()
    for s in ("admin off", "persist_config off", "auto_https disable_redirects", "protocols h1 h2",
              "acme_ca https://acme-v02.api.letsencrypt.org/directory",
              "dns cloudflare {env.CLOUDFLARE_API_TOKEN}", "resolvers 1.1.1.1 1.0.0.1",
              "reverse_proxy steward:8420", "{$STEWARD_DOMAIN} {"):
        assert s in c, s
    assert not re.search(r":80\b|:2019|h3", c)


def start(tmp_path, **env):
    fake = tmp_path / "caddy"
    fake.write_text('#!/bin/sh\necho "caddy $*"\n')
    fake.chmod(0o755)
    e = {"PATH": f"{tmp_path}:/usr/bin:/bin", **env}
    return subprocess.run(["sh", str(PROXY / "start.sh")], env=e, capture_output=True, text=True)


def test_start_runs_caddy_with_the_baked_in_config(tmp_path):
    p = start(tmp_path, STEWARD_DOMAIN="steward.example.com", CLOUDFLARE_API_TOKEN=TOKEN)
    assert p.returncode == 0, p.stderr
    assert p.stdout == "caddy run --config /etc/caddy/Caddyfile.dns-cloudflare --adapter caddyfile\n"


@pytest.mark.parametrize("domain", [
    "", "https://steward.example.com", "steward.example.com:443", "Steward.Example.com",
    "steward.example.com {\n}", "steward.example.com\nadmin :2019", "localhost", "steward..example.com",
    ".example.com", "example.com.", "-steward.example.com", "steward-.example.com", "example.com-",
    "steward example.com", "a" * 250 + ".com",
])
def test_start_refuses_a_domain_that_isnt_a_plain_host_name(tmp_path, domain):
    p = start(tmp_path, STEWARD_DOMAIN=domain, CLOUDFLARE_API_TOKEN=TOKEN)
    assert p.returncode == 1
    assert "STEWARD_DOMAIN must be a plain lowercase host name" in p.stderr and "proxy.env" in p.stderr
    assert p.stdout == ""


@pytest.mark.parametrize("tls", ["internal", "http", "dns-cloudflare"])
def test_start_refuses_an_ip_address_and_says_to_use_a_name(tmp_path, tls):
    """Install gate INS-1: an IP address started, logged a certificate, and failed every handshake with no word why."""
    p = start(tmp_path, STEWARD_DOMAIN="192.168.50.38", STEWARD_TLS=tls, CLOUDFLARE_API_TOKEN=TOKEN)
    assert p.returncode == 1 and p.stdout == ""
    assert "STEWARD_DOMAIN is an IP address: '192.168.50.38'. Use a name" in p.stderr
    assert "192.168.50.38.nip.io" in p.stderr and "LAN only" in p.stderr
    assert start(tmp_path, STEWARD_DOMAIN="192.168.50.38.nip.io", STEWARD_TLS="internal").returncode == 0


def test_start_refuses_without_a_token(tmp_path):
    p = start(tmp_path, STEWARD_DOMAIN="steward.example.com")
    assert p.returncode == 1 and "CLOUDFLARE_API_TOKEN is empty" in p.stderr and p.stdout == ""


def test_the_http_and_internal_caddyfiles_are_locked_down_and_hold_no_secret():
    for name, wanted, unwanted in (
            ("Caddyfile.http", ("acme_ca https://acme-v02.api.letsencrypt.org/directory",), ("dns cloudflare", "tls internal")),
            ("Caddyfile.internal", ("tls internal", "skip_install_trust", "auto_https disable_redirects"),
             ("acme_ca", "dns cloudflare"))):
        c = (PROXY / name).read_text()
        for s in ("admin off", "persist_config off", "protocols h1 h2", "reverse_proxy steward:8420",
                  "{$STEWARD_DOMAIN} {", *wanted):
            assert s in c, (name, s)
        assert not any(s in c for s in unwanted), name
        assert not re.search(r":2019|h3|\{env\.", c), name


def test_the_image_copies_all_three_caddyfiles():
    assert "COPY Caddyfile.dns-cloudflare Caddyfile.http Caddyfile.internal /etc/caddy/\n" in dockerfile()


def test_the_build_validates_every_caddyfile_and_leaves_no_ca_in_the_image():
    run = re.search(r"^RUN caddy list-modules(?:.*\\\n)*.*$", dockerfile(), re.M)[0]     # the RUN and its continued lines
    assert "for mode in dns-cloudflare http internal" in run
    assert "XDG_DATA_HOME=/tmp/caddy-validate" in run and "XDG_CONFIG_HOME=/tmp/caddy-validate" in run
    assert run.rstrip().endswith("rm -rf /tmp/caddy-validate")      # same layer: no local CA key is ever kept


@pytest.mark.parametrize("tls, env, file", [
    ("dns-cloudflare", {"CLOUDFLARE_API_TOKEN": TOKEN}, "Caddyfile.dns-cloudflare"),
    ("http", {}, "Caddyfile.http"),
    ("internal", {}, "Caddyfile.internal")])
def test_start_picks_the_caddyfile_by_steward_tls(tmp_path, tls, env, file):
    p = start(tmp_path, STEWARD_DOMAIN="steward.example.com", STEWARD_TLS=tls, **env)
    assert p.returncode == 0, p.stderr
    assert p.stdout == f"caddy run --config /etc/caddy/{file} --adapter caddyfile\n"


@pytest.mark.parametrize("tls", ["acme", "HTTP", "dns", "internal;", "../Caddyfile", "http internal"])
def test_start_refuses_an_unknown_tls_mode(tmp_path, tls):
    p = start(tmp_path, STEWARD_DOMAIN="steward.example.com", STEWARD_TLS=tls, CLOUDFLARE_API_TOKEN=TOKEN)
    assert p.returncode == 1 and "STEWARD_TLS must be dns-cloudflare, http or internal" in p.stderr
    assert p.stdout == ""


def test_only_internal_takes_a_one_word_box_name(tmp_path):
    assert start(tmp_path, STEWARD_DOMAIN="steward", STEWARD_TLS="internal").returncode == 0
    for tls in ("http", "dns-cloudflare"):
        p = start(tmp_path, STEWARD_DOMAIN="steward", STEWARD_TLS=tls, CLOUDFLARE_API_TOKEN=TOKEN)
        assert p.returncode == 1 and "STEWARD_DOMAIN must be" in p.stderr


def test_only_dns_cloudflare_needs_the_token(tmp_path):
    assert start(tmp_path, STEWARD_DOMAIN="steward.example.com", STEWARD_TLS="http").returncode == 0
    p = start(tmp_path, STEWARD_DOMAIN="steward.example.com", STEWARD_TLS="dns-cloudflare")
    assert p.returncode == 1 and "CLOUDFLARE_API_TOKEN is empty" in p.stderr


# P10 (docs/CLAIMS.md): Caddy writes an error line for a 502 (Steward stopped or restarting) with the whole request in
# it: request>uri holds the path and query, request>headers the user agent and referer, request>remote_ip the client.
# The path is as personal as the query: a book's slug (a parent book's slug is the parent's name) opens /b/, /api/b/
# and /api/books/, and other segments hold a category's name or a link's token. So the filter deletes request>uri
# whole rather than rewriting the routes it knows about. Caddy 2.11's filter encoder (modules/logging/filterencoder.go)
# filters nested fields by their "parent>child" key, applies a field's filters in the order written, and leaves a
# deleted field out of the line. Every Caddyfile the image can run carries the same global block. request>host and
# request>tls>server_name are the install's own address, which can be a family's name (issue #5); Steward's own log drops
# an origin for the same reason (outside_text in src/steward/logs.py). Certificate lines still name the domain, and
# docs/explanations/privacy.md says so.
LOG_FILTER = """\
\tlog {
\t\tformat filter {
\t\t\twrap json
\t\t\trequest>uri delete
\t\t\trequest>headers delete
\t\t\trequest>remote_ip delete
\t\t\trequest>remote_port delete
\t\t\trequest>client_ip delete
\t\t\trequest>host delete
\t\t\trequest>tls>server_name delete
\t\t}
\t}
"""
PLANT = "zqxwvplant"
ROUTES_TSX = ROOT / "web/src/routes.tsx"


def caddyfiles() -> list[Path]:
    return sorted(PROXY.glob("Caddyfile*"))


def global_options(text: str) -> str:
    """The global options block: the first line that is only "{", through the first line that is only "}"."""
    start = text.index("\n{\n") + 3
    return text[start:text.index("\n}\n", start) + 1]


def filtered_uri(block: str, uri: str) -> str | None:
    """What the block's request>uri filters leave of a request's address, in the order Caddy applies them; None when a
    delete leaves the field out of the line. Go's RE2 and Python's re agree on simple patterns (classes, anchors)."""
    for m in re.finditer(r"^\t+request>uri (?:regexp (\S+) (\S+)|(delete))$", block, re.M):
        if m[3]:
            return None
        uri = re.sub(m[1], m[2].strip('"'), uri)
    return uri


def planted_uris(api_paths) -> list[str]:
    """One address for each route that takes a value in its path: the web app's pages (web/src/routes.tsx; the book's
    screens sit under /b/:slug) and the API's routes, with PLANT in every value and a search in the query."""
    def fill(path: str) -> str:
        path = re.sub(r"\{[^}]+\}", PLANT, path)                            # FastAPI: {slug}, {token}, {p:path}
        return re.sub(r":[A-Za-z_]+|\*", PLANT, path) + "?q=zqxwvsearch"   # react-router: :slug, :id, a splat
    web = re.findall(r"\bpath: '([^']+)'", ROUTES_TSX.read_text())
    pages = ["/b/:slug"] + [p if p.startswith("/") else f"/b/:slug/{p}" for p in web]
    uris = [fill(p) for p in pages + list(api_paths) if re.search(r"[:*{]", p)]
    assert len(uris) > 100, len(uris)   # the parse found the routes
    return uris


def test_every_caddyfile_strips_the_request_from_its_error_lines(app):
    from tests.test_isolation_sweep import api_routes
    uris = planted_uris(sorted({path for path, *_ in api_routes(app)}))
    for must in (f"/b/{PLANT}?q=zqxwvsearch", f"/b/{PLANT}/transactions?q=zqxwvsearch",
                 f"/api/b/{PLANT}/transactions?q=zqxwvsearch", f"/api/books/{PLANT}/members?q=zqxwvsearch"):
        assert must in uris, must
    assert [p.name for p in caddyfiles()] == ["Caddyfile.dns-cloudflare", "Caddyfile.http", "Caddyfile.internal"]
    for path in caddyfiles():
        block = global_options(path.read_text())
        assert LOG_FILTER in block, path.name
        assert re.findall(r"^\t+(?:log|format)\b", path.read_text(), re.M) == ["\tlog", "\t\tformat"], path.name
        assert filtered_uri(block, "/b/smith-family/transactions?q=zqxwvsearch") is None
        assert filtered_uri(block, "/api/books/smith-family/members") is None
        kept = [u for u in uris if "zqxwv" in (filtered_uri(block, u) or "")]
        assert kept == [], (path.name, kept[:5])


def test_the_log_filter_check_notices_a_filter_that_misses_a_route():
    bare = (PROXY / "Caddyfile.http").read_text().replace(LOG_FILTER, "")
    assert LOG_FILTER not in global_options(bare)
    assert filtered_uri(global_options(bare), "/b/smith/transactions?q=zqxwv") == "/b/smith/transactions?q=zqxwv"
    # The rewrite this block replaced caught /api/b/ only, and kept the slug in the page's address and in /api/books/.
    rewrite = '\t\t\trequest>uri regexp [?].* ""\n\t\t\trequest>uri regexp ^/api/b/[^/]+ /api/b/(book)\n'
    assert filtered_uri(rewrite, "/api/b/smith/transactions?q=zqxwv") == "/api/b/(book)/transactions"
    assert filtered_uri(rewrite, "/b/smith/transactions?q=zqxwv") == "/b/smith/transactions"
    assert filtered_uri(rewrite, "/api/books/smith/members") == "/api/books/smith/members"


def test_the_refusals_point_at_the_public_access_guide(tmp_path):
    p = start(tmp_path, STEWARD_DOMAIN="", STEWARD_TLS="http")
    assert "docs/guides/access.md" in p.stderr and "deploy/nas" not in p.stderr
