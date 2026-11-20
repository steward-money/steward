"""steward-gateway: the gateway's commands on the gateway (spec 3.2, 5.4, 9.5)."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import typer

from steward_boundary.pairing import short_code
from steward_gateway import accelerator, hardware
from steward_gateway.config import ConfigError, GatewayConfig, load_config
from steward_gateway.ledger import Ledger
from steward_gateway.ollama import local_model_line
from steward_gateway.pairing import WINDOW_S, arm
from steward_gateway.prices import cost_nd, dollars, estimate_nd, reserve_price, settle_price
from steward_gateway.prompt import prewarm_body, prewarm_payload
from steward_gateway.secrets import SecretFileError, api_key

app = typer.Typer(no_args_is_help=True, help="Steward's gateway: the only part of Steward that calls Claude")
NO_KEY = ("No API key in {path}. Set one up as docs/guides/gateway.md, \"The Claude API key\", says, "
          "then run steward-gateway check again.")


def _config() -> GatewayConfig:
    try:
        return load_config()
    except ConfigError as e:
        typer.echo(str(e))
        raise typer.Exit(1)


def _ledger(cfg: GatewayConfig) -> Ledger:
    return Ledger(cfg.ledger_path, cfg.timezone, cfg.default_cap_cents, cfg.max_cap_cents, cfg.default_model)


@app.command()
def serve():
    """Run the gateway (the systemd unit runs this)."""
    import os

    from steward_gateway import gpu
    from steward_gateway.app import create_gateway_app
    from steward_gateway.logs import setup_logging
    problem = gpu.license_problem(os.environ)       # before anything else, even the config
    if problem:
        typer.echo(problem)
        raise typer.Exit(1)
    cfg = _config()
    setup_logging()
    if cfg.network == "compose" and gpu.two_gateways():        # every compose gateway, the CPU image included
        typer.echo(gpu.TWO_GATEWAYS)
        raise typer.Exit(1)
    try:
        api_key(cfg)                     # refuse to start with a key file others can read (spec 9.5)
    except SecretFileError as e:
        typer.echo(str(e))
        raise typer.Exit(1)
    from steward_gateway import serving
    typer.echo(local_model_line(cfg))
    line = gpu.no_gpu_line(os.environ, accelerator.available)
    if line:
        typer.echo(line)
    serving.run(cfg, create_gateway_app(cfg))


@app.command("hardware")
def hardware_cmd():
    """What this machine has for the gateway to run on, and the one next step. Read-only: it installs nothing."""
    typer.echo(f"This gateway runs on: {accelerator.available()}")
    for line in hardware.report(hardware.probe()):
        typer.echo(line)


def check_key(cfg: GatewayConfig, client, now: datetime | None = None) -> str:
    """One max_tokens 0 request with the fixed instructions (spec 9.5). Listed in the sent list as channel "check"."""
    import json

    from steward_boundary.canonical import canonical, sha256_hex
    now = now or datetime.now(timezone.utc)
    ledger = _ledger(cfg)
    model = ledger.model()
    body = prewarm_body(model)
    tokens = client.messages.count_tokens(model=model, tools=body["tools"], system=body["system"],
                                          messages=body["messages"]).input_tokens
    rid = ledger.reserve(now=now, book_id=None, user_id=None, turn_id=None, channel="check", model=model,
                         estimate_nd=estimate_nd(len(json.dumps(body)), 0, reserve_price(cfg, model)))
    try:
        msg = client.messages.create(**body)
    except Exception:
        ledger.release(rid)
        raise
    usage = msg.usage.to_dict(mode="json")
    ledger.settle(rid, cost_nd(usage, settle_price(cfg, msg.model, usage)))
    payload = prewarm_payload()
    ledger.record_sent(now, [(sha256_hex(payload), len(canonical(payload)))], None, "check")
    st = ledger.state(now, cfg)
    return (f"The API key works. The fixed instructions are {tokens:,} tokens. "
            f"This month: {dollars(st['spent_cents'] * 10_000_000)} of {dollars(st['cap_cents'] * 10_000_000)} used.")


@app.command()
def check():
    """Confirm the API key works with one max_tokens 0 request, and show this month's spending against the cap."""
    import anthropic
    from steward_gateway.logs import api_error_reason
    cfg = _config()
    try:
        key = api_key(cfg)
    except SecretFileError as e:
        typer.echo(str(e))
        raise typer.Exit(1)
    if key is None:
        typer.echo(NO_KEY.format(path=cfg.env_path))
        raise typer.Exit(1)
    try:
        typer.echo(check_key(cfg, anthropic.Anthropic(api_key=key, max_retries=2, open_telemetry=False)))
    except anthropic.AuthenticationError:
        typer.echo("Anthropic refused the key. Check the line in gateway.env, or make a new key.")
        raise typer.Exit(1)
    except anthropic.APIError as e:
        message = api_error_reason(e)[1] if isinstance(e, anthropic.APIStatusError) else ""
        typer.echo(f"Anthropic didn't answer: {type(e).__name__}{': ' + message if message else ''}.")
        raise typer.Exit(1)


@app.command()
def pair(code: Optional[str] = typer.Argument(
        None, help="A code from steward advisor pair; leave it out to get one to type in Steward")):
    """Accept a pairing code for the next 10 minutes: one from `steward advisor pair`, or a new one to type in Steward."""
    cfg = _config()
    if code is None:
        code = short_code()
        arm(cfg, code, datetime.now(timezone.utc))
        typer.echo(f"Type this code in Steward: {code}\nIt works for {WINDOW_S // 60} minutes.")
        return
    arm(cfg, code, datetime.now(timezone.utc))
    typer.echo(f"Waiting {WINDOW_S // 60} minutes for Steward to finish pairing. Keep the gateway running.")


@app.command("stop-chat")
def stop_chat():
    """Refuse all chat until start-chat (spec 5.4)."""
    cfg = _config()
    cfg.stop_flag_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    cfg.stop_flag_path.write_text("stopped\n")
    typer.echo("Chat is turned off on this machine. Run steward-gateway start-chat to turn it back on.")


@app.command("start-chat")
def start_chat():
    """Allow chat again after stop-chat."""
    cfg = _config()
    cfg.stop_flag_path.unlink(missing_ok=True)
    typer.echo("Chat is allowed again on this machine.")


@app.command("pdf-check")
def pdf_check():
    """Whether this machine opens statement PDFs for Steward (remote-reading P58)."""
    from steward_pdf import sandbox
    c = sandbox.confinement()
    typer.echo(f"Seccomp filter: {'on' if c.get('confined') is True and c.get('seccomp') == 2 else 'off'}")
    if c.get("probe") == "missing":                     # no /etc/passwd to try: the wall can't be shown to hold
        wall = "can't tell (no probe file)"
    else:
        wall = "on" if c.get("walled") is True and c.get("probe") == "refused" else "off"
    typer.echo(f"Reads walled off (Landlock): {wall}")
    typer.echo(f"A test PDF read inside both: {'ok' if c.get('canary') == 'ok' else 'failed'}")
    if sandbox.can_wall(c):
        typer.echo("This machine opens PDFs for Steward.")
        return
    typer.echo("This machine won't open PDFs for Steward, so they wait in Steward. "
               "See docs/guides/gateway.md, \"Reading PDFs\".")
    raise typer.Exit(1)


eval_app = typer.Typer(no_args_is_help=True, help="Measure Steward's models and answers on this machine")
app.add_typer(eval_app, name="eval")


@eval_app.command("categorizer")
def eval_categorizer(model: list[str] = typer.Option(None, "--model", help="An Ollama model; repeat to compare "
                                                     "several (default: categorizer_model)"),
                     set_path: Path = typer.Option(None, "--set", help="The labelled set (default: the repo's)")):
    """Run the categorizer's labelled set (spec 6.1) and time it. Local only: nothing leaves the gateway."""
    import asyncio

    from steward_gateway.categorize import CategorizerAway
    from steward_gateway.evals import categorizer as E
    cfg = _config()
    names = list(model) if model else [cfg.categorizer_model]
    path = set_path or E.DEFAULT_SET
    if not path.exists():
        typer.echo(f"No labelled set at {path}. Run this from the steward checkout.")
        raise typer.Exit(1)
    rows = E.read_set(path)

    async def go(name: str, previous: str | None):
        if previous:
            await E.unload(cfg.ollama_url, previous)
        cat = E.Categorizer(cfg.ollama_url, name, parallel=cfg.categorizer_parallel)
        try:
            if not await cat.ready():
                return None
            return await E.run_eval(cat, rows)
        except CategorizerAway:
            return None
        finally:
            await cat.close()
    results = []
    for n, name in enumerate(names):
        result = asyncio.run(go(name, names[n - 1] if n else None))
        if result is None:
            typer.echo(f"Ollama isn't answering with {name}.")
            if n == 0:
                raise typer.Exit(1)
            continue
        typer.echo(E.format_result(name, result))
        results.append((name, result))
    typer.echo(E.format_comparison(results))
    if not results[0][1].passes:
        raise typer.Exit(1)


@eval_app.command("statements")
def eval_statements(model: list[str] = typer.Option(None, "--model", help="An Ollama text model for new layouts; "
                                                    "repeat to compare (default: statement_layout_model)"),
                    vision_model: list[str] = typer.Option(None, "--vision-model", help="An Ollama vision model for "
                                                           "scanned pages; repeat to compare (default: "
                                                           "statement_vision_model)")):
    """Run the synthetic statements through the NAS's reading with this machine's models (spec P47): the layout model
    on text statements, the vision model on scans. With neither option, both default models. Local only: nothing
    leaves the gateway. Uses the GPU: run it by hand, never from tests."""
    import asyncio

    from steward_gateway.evals import categorizer as C
    from steward_gateway.evals import statements as E
    cfg = _config()
    both = not model and not vision_model
    names = (list(model) if model else [cfg.statement_layout_model]) if (model or both) else []
    vision = (list(vision_model) if vision_model else [cfg.statement_vision_model]) if (vision_model or both) else []
    results = []
    for n, name in enumerate(names):
        loop = asyncio.new_event_loop()
        sm = E.StatementModel(cfg.ollama_url, name, layout_timeout=cfg.statement_layout_timeout_s)
        try:
            if n:
                loop.run_until_complete(C.unload(cfg.ollama_url, names[n - 1]))
            if not loop.run_until_complete(sm.features()):
                typer.echo(f"Ollama isn't answering with {name}.")
                continue
            result = E.run_eval(sm, loop, ids=E.BANK_CARD + E.HOLDINGS + E.LOANS)
        finally:
            loop.run_until_complete(sm.close())
            loop.close()
        typer.echo(E.format_result(name, result))
        results.append((name, result))
    last = names[-1] if names else None
    if names:
        typer.echo(E.format_comparison(results))
    scans = []
    for name in vision:
        loop = asyncio.new_event_loop()
        sm = E.StatementModel(cfg.ollama_url, cfg.statement_layout_model, vision_model=name,
                              page_timeout=cfg.statement_page_timeout_s)
        try:
            if last:
                loop.run_until_complete(C.unload(cfg.ollama_url, last))
            last = name
            if "scan" not in loop.run_until_complete(sm.features()):
                typer.echo(f"Ollama isn't answering with {name}.")
                continue
            scan = E.run_scan_eval(sm, loop, ids=E.ONE_ACCOUNT + E.HOLDINGS)
        finally:
            loop.run_until_complete(sm.close())
            loop.close()
        typer.echo(E.format_scan_result(name, scan))
        scans.append((name, scan))
    if (names and (not results or not results[0][1].passes)) or (vision and (not scans or not scans[0][1].passes)):
        raise typer.Exit(1)


@eval_app.command("redaction")
def eval_redaction():
    """Time redaction with the real models on this machine: one read of each sample question and of 1,000
    characters. Local only: nothing leaves the gateway."""
    from steward_gateway.evals.redaction import format_redaction, run_redaction
    from steward_gateway.redact import load_real_detectors
    detectors, problem = load_real_detectors()
    if not detectors:
        typer.echo(problem or "The redaction models didn't load.")
        raise typer.Exit(1)
    typer.echo(format_redaction(run_redaction(detectors)))


def _real_api(cfg: GatewayConfig, what: str, yes: bool):
    """The real key, the real detectors and the real ledger for an eval JR asked for (ruling 14). Says what it will
    cost and what is left this month, and asks first unless --yes."""
    try:
        key = api_key(cfg)
    except SecretFileError as e:
        typer.echo(str(e))
        raise typer.Exit(1)
    if key is None:
        typer.echo(NO_KEY.format(path=cfg.env_path))
        raise typer.Exit(1)
    ledger = _ledger(cfg)
    st = ledger.state(datetime.now(timezone.utc), cfg)
    left = max(0, st["cap_cents"] - st["spent_cents"] - st["reserved_cents"])
    typer.echo(f"{what} This month: {dollars(left * 10_000_000)} of {dollars(st['cap_cents'] * 10_000_000)} left.")
    if not yes and not typer.confirm("Go ahead?"):
        raise typer.Exit(1)
    import anthropic

    from steward_gateway.redact import load_real_detectors
    detectors, problem = load_real_detectors()
    if not detectors:
        typer.echo(problem or "The redaction models didn't load, so nothing was sent.")
        raise typer.Exit(1)
    return anthropic.AsyncAnthropic(api_key=key, max_retries=0, open_telemetry=False), detectors, ledger


@eval_app.command("answers")
def eval_answers(yes: bool = typer.Option(False, "--yes", help="Don't ask before spending")):
    """Ask 32 fictional questions over the demo books through the real API and check the figures (spec 11.4)."""
    from steward_gateway.evals.answers import format_answers, run_answers
    from steward_gateway.evals.answers_set import CASES
    from steward_gateway.evals.rig import demo_rig
    cfg = _config()
    client, detectors, ledger = _real_api(
        cfg, "This asks 32 fictional questions about Steward's demo books through the real API: about $3.", yes)
    with demo_rig(cfg, client=client, detectors=detectors, ledger=ledger) as rig:
        result = run_answers(rig, CASES)
    typer.echo(format_answers(result))
    if not result.passes:
        raise typer.Exit(1)


@app.command()
def bench(yes: bool = typer.Option(False, "--yes", help="Don't ask before spending")):
    """Time the first answer token over 20 fictional questions through the real API (spec 12)."""
    import json

    from steward_gateway.evals.bench import as_json, format_bench, run_bench
    from steward_gateway.evals.rig import demo_rig
    cfg = _config()
    client, detectors, ledger = _real_api(
        cfg, "This times 20 fictional questions about Steward's demo books through the real API: about $2.", yes)
    with demo_rig(cfg, client=client, detectors=detectors, ledger=ledger) as rig:
        result = run_bench(rig)
    typer.echo(format_bench(result))
    out = cfg.state_dir / f"bench-{datetime.now(timezone.utc):%Y-%m-%d}.json"
    out.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    out.write_text(json.dumps(as_json(result), indent=2))
    typer.echo(f"Saved to {out}. Keep the table above with the plan's notes.")
    if not result.passes:
        raise typer.Exit(1)


mcp_app = typer.Typer(no_args_is_help=True, help="Claude Code's grants on this machine")
app.add_typer(mcp_app, name="mcp-grant")


@mcp_app.command("add")
def mcp_grant_add(grant: Optional[str] = typer.Argument(None, hidden=True)):
    """Ask for a grant from Settings → Chat → Claude Code, save it and print the command that adds it to Claude Code."""
    import asyncio

    from steward_gateway.mcp import grants as G
    from steward_gateway.mcp.nas import NasAway, NasClient, NasRefused
    from steward_gateway.secrets import read_private
    if grant is not None:
        # A grant typed here would stay in shell history and in sudo's journal line (threat model A4).
        typer.echo("Paste the grant when asked, not on the command line: it would stay in your shell history.")
        raise typer.Exit(2)
    cfg = _config()
    if not cfg.mcp:
        typer.echo("Claude Code is off on this machine. To turn it on, set mcp = true in gateway.toml (change its "
                   "mcp = false line, or add one above any [prices] table) and restart the gateway (the gateway "
                   "README, section 9, Claude Code).")
        raise typer.Exit(1)
    if cfg.nas_url is None:
        typer.echo("Set nas_url in gateway.toml first: Steward's address, like https://steward.tail1234.ts.net.")
        raise typer.Exit(1)
    grant = typer.prompt("Paste the grant from Settings → Chat → Claude Code", hide_input=True).strip()
    try:
        slug = G.slug_of(grant)
    except G.GrantError as e:
        typer.echo(str(e))
        raise typer.Exit(1)

    async def check() -> dict:
        nas = NasClient(cfg.nas_url, lambda: read_private(cfg.token_path))
        try:
            return await nas.tools(slug, grant)
        finally:
            await nas.close()
    try:
        info = asyncio.run(check())
    except NasAway:
        typer.echo("Steward didn't answer, so the grant wasn't saved. Check nas_url and the tailnet grant "
                   "from this machine to Steward (docs/guides/gateway.md).")
        raise typer.Exit(1)
    except NasRefused as e:
        typer.echo(f"Steward refused the grant, so it wasn't saved: {e.message}")
        raise typer.Exit(1)
    alias = G.add(cfg, grant)
    token = G.mcp_token(cfg, create=True)
    # No book or person names here (ruling I1b): this may run inside Claude Code. Settings → Chat says whose it is.
    typer.echo(f"This grant lets Claude Code look up that book until {info['until']} as {alias}. "
               "Store and payee names are never offered.")
    if not info.get("chat"):
        typer.echo("Chat is off in that book, so Claude Code gets no answers until a manager turns it on.")
    typer.echo("Add it to Claude Code with:")
    typer.echo(G.claude_command(cfg, alias, token))


@mcp_app.command("list")
def mcp_grant_list():
    """The books Claude Code can look up from this machine."""
    from steward_gateway.mcp import grants as G
    cfg = _config()
    found = G.load(cfg)
    if not found:
        typer.echo("No grants on this machine yet.")
    for alias in sorted(found, key=lambda k: int(k.split("-")[1])):
        typer.echo(f"{alias}  http://127.0.0.1:{cfg.mcp_port}/mcp/{alias}")


@mcp_app.command("remove")
def mcp_grant_remove(name: str):
    """Forget a grant on this machine by its name, like steward-1 (revoke it in Settings → Chat too)."""
    from steward_gateway.mcp import grants as G
    cfg = _config()
    if not G.remove(cfg, name):
        typer.echo(f"No grant named {name} on this machine. steward-gateway mcp-grant list shows them.")
        raise typer.Exit(1)
    typer.echo(f"Removed. Also run: claude mcp remove {name}")
