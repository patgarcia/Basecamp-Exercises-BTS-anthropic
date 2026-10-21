#!/usr/bin/env python3
"""Partner Basecamp — one-time machine setup, and the Claude connection every notebook uses.

Run it once from the repo root:

    python3 basecamp_setup.py          (Windows: py basecamp_setup.py, or python basecamp_setup.py)

It installs uv if you don't have it, creates .venv with everything the exercises need
(uv sync), registers the "Anthropic Basecamp (.venv)" Jupyter kernel, points VS Code at
.venv, asks for your credentials and saves them to .env, then checks they work with a real
call.

The notebooks import connect() from this file, so the connection logic lives in one place.
Only the standard library is imported at the top: the setup half runs before anything is
installed.
"""

import getpass
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent  # the repo root: the folder with day1/ and day2/
# How to run this script, as people type it on this OS (python3 often opens the Store on Windows).
SETUP_COMMAND = "py basecamp_setup.py" if os.name == "nt" else "python3 basecamp_setup.py"
ENV_FILE = ROOT / ".env"
ENV_EXAMPLE = ROOT / ".env.example"
KERNEL_NAME = "basecamp-exercises"
KERNEL_DISPLAY_NAME = "Anthropic Basecamp (.venv)"
DEFAULT_MODEL_PREFIX = "bedrock.anthropic."  # what PwC's Bedrock gateway expects
ENV_KEYS = ("ANTHROPIC_API_KEY", "AWS_BEARER_TOKEN_BEDROCK", "AWS_REGION",
            "ANTHROPIC_BASE_URL", "ANTHROPIC_MODEL_PREFIX")


# =============================================================================
# The connection — imported by every notebook and by Diagnose_Fix_Brief.py
# =============================================================================

class Settings:
    """Credentials and routing, read fresh from your shell and .env (the shell wins).

    Re-reading on every call means editing .env and re-running a cell picks up the change
    with no kernel restart. Nothing is written to os.environ, so a script launched from a
    notebook reads the current .env itself instead of inheriting stale values.
    """

    def __init__(self):
        from dotenv import dotenv_values

        # utf-8-sig: Windows editors can save .env with a BOM.
        file = dotenv_values(ENV_FILE, encoding="utf-8-sig") if ENV_FILE.is_file() else {}

        def setting(name):
            value = (os.environ.get(name) or file.get(name) or "").strip()
            return "" if value.startswith("paste-") else value  # the .env.example placeholder

        self.api_key = setting("ANTHROPIC_API_KEY")
        self.bedrock_token = setting("AWS_BEARER_TOKEN_BEDROCK")
        self.region = setting("AWS_REGION")
        self.base_url = setting("ANTHROPIC_BASE_URL")  # company gateway / proxy, if any
        self.model_prefix = setting("ANTHROPIC_MODEL_PREFIX")
        # If both credentials are set, the Anthropic-protocol key wins.
        self.provider = "anthropic" if self.api_key else "bedrock" if self.bedrock_token else None

    def problem(self):
        """What's missing before a client can be built, as (headline, details) — or None."""
        if self.provider is None:
            return ("📋 Add your credentials to continue",
                    "No key found in your shell or in .env.\n"
                    "\n"
                    "In a terminal, from the repo root (the folder with day1 and day2):\n"
                    f"    cd \"{ROOT}\"\n"
                    f"    {SETUP_COMMAND}\n"
                    "It asks for your key and saves it to .env. (Or edit .env yourself — "
                    ".env.example shows the options.)\n"
                    "\n"
                    "Then run this again.")
        if self.provider == "bedrock" and not self.region and not self.base_url:
            return ("📋 Bedrock needs a region",
                    f"Found AWS_BEARER_TOKEN_BEDROCK but no AWS_REGION.\n"
                    f"\n"
                    f"Add the region your Bedrock models are enabled in to {ENV_FILE}, e.g.:\n"
                    "    AWS_REGION=us-east-1\n"
                    "\n"
                    "Then run this again.")
        return None

    def model(self, name):
        """Model ID for your route: ANTHROPIC_MODEL_PREFIX if set (gateways), else Bedrock's
        `anthropic.` prefix, else the bare ID. Idempotent — an already-resolved ID passes
        through, so a value from --model can't end up double-prefixed."""
        prefix = self.model_prefix or ("anthropic." if self.provider == "bedrock" else "")
        return name if name.startswith(prefix) else prefix + name

    def make_client(self, timeout=600.0, max_retries=2):
        """Client for your route. ANTHROPIC_BASE_URL, when set, sends either kind through it."""
        import anthropic

        if self.provider == "bedrock":
            return anthropic.AnthropicBedrockMantle(
                api_key=self.bedrock_token, aws_region=self.region or None,
                base_url=self.base_url or None, timeout=timeout, max_retries=max_retries)
        return anthropic.Anthropic(api_key=self.api_key, base_url=self.base_url or None,
                                   timeout=timeout, max_retries=max_retries)


class Connection:
    """What connect() hands a notebook: a verified client plus the resolved model IDs."""

    def __init__(self, settings, client, model, fast_model, big_model):
        self.settings = settings
        self.client = client
        self.model = model
        self.fast_model = fast_model
        self.big_model = big_model
        self.provider = settings.provider
        self.base_url = settings.base_url

    def make_client(self, timeout=600.0, max_retries=2):
        """Another client on the same route, e.g. with a different timeout."""
        return self.settings.make_client(timeout=timeout, max_retries=max_retries)

    @property
    def batch_api(self):
        """The Batch API is an Anthropic API endpoint: Bedrock doesn't expose it, and most
        company gateways don't pass it through."""
        return self.provider == "anthropic" and not self.base_url


def connect(model="claude-sonnet-5", fast_model="claude-haiku-4-5", big_model="claude-opus-4-8"):
    """Build a Claude client from your settings and prove it works, or stop with a clear
    banner saying what to fix. Model names are bare IDs; they come back resolved for your route.

        model       the workhorse for the exercise
        fast_model  cheap + quick (connection check, judges)
        big_model   when you want to try a larger model
    """
    try:
        import anthropic
        import dotenv  # noqa: F401 — Settings needs it
    except ImportError as exc:
        _stop("📦 Packages missing",
              f"'{exc.name}' isn't installed in the Python running this:\n"
              f"    {sys.executable}\n"
              "\n"
              f"In VS Code, click the kernel name (top right) and pick \"{KERNEL_DISPLAY_NAME}\".\n"
              "Not in the list? In a terminal, from the repo root (the folder with day1 and "
              "day2):\n"
              f"    cd \"{ROOT}\"\n"
              f"    {SETUP_COMMAND}\n"
              "\n"
              "Then run this again.")

    settings = Settings()
    issue = settings.problem()
    if issue:
        _stop(*issue)

    connection = Connection(settings, settings.make_client(timeout=900.0),
                            settings.model(model), settings.model(fast_model),
                            settings.model(big_model))
    gateway = settings.base_url
    via = f" via {gateway}" if gateway else ""

    # Ping the real model, not just the credential's shape: on Bedrock or a gateway a valid
    # key can still 404 if the model isn't enabled for you, or the gateway wants another ID.
    probe = settings.make_client(timeout=30.0, max_retries=1)
    try:
        probe.messages.create(model=connection.fast_model, max_tokens=1,
                              messages=[{"role": "user", "content": "ping"}])
    except anthropic.NotFoundError:
        if gateway:
            _status(False, f"Reached the gateway at {gateway}, but it doesn't know model "
                           f"'{connection.fast_model}'. Check ANTHROPIC_MODEL_PREFIX in .env "
                           f"against the model IDs your gateway team gave you, then try again.")
        elif settings.provider == "bedrock":
            _status(False, f"Bedrock reached, but model '{connection.fast_model}' isn't "
                           f"available to you in {settings.region}. Enable model access for it "
                           f"in the Bedrock console (or switch AWS_REGION to a region where it "
                           f"is enabled), then try again.")
        else:
            _status(False, f"Model '{connection.fast_model}' not found for this key.")
        raise SystemExit("Model not available — see the message above.")
    except (anthropic.AuthenticationError, anthropic.PermissionDeniedError):
        if gateway:
            _status(False, f"The gateway at {gateway} rejected that key. Check it's the key your "
                           "gateway issued (re-run basecamp_setup.py to replace it).")
        elif settings.provider == "bedrock":
            _status(False, "That Bedrock key was rejected. Check AWS_BEARER_TOKEN_BEDROCK and "
                           "that it has Bedrock invoke permissions.")
        else:
            _status(False, "That key was rejected. Re-run basecamp_setup.py and paste the whole "
                           "key (it starts with sk-ant-).")
        raise SystemExit("Credentials not accepted — see the message above.")
    except Exception as exc:
        _status(False, f"Could not reach the API{via} ({type(exc).__name__}). Check your "
                       "connection (and VPN, for a company gateway), then try again.")
        raise

    if settings.provider == "anthropic" and not gateway:
        _status(True, f"API key verified — you're connected to Claude as {connection.model}.")
    elif settings.provider == "anthropic":
        _status(True, f"Gateway key verified{via} — you're connected to Claude as "
                      f"{connection.model}.")
    else:
        _status(True, f"Bedrock key verified ({settings.region or 'gateway'}){via} — you're "
                      f"connected to Claude as {connection.model}.")
    return connection


def _in_notebook():
    try:
        from IPython import get_ipython
        return get_ipython().__class__.__name__ == "ZMQInteractiveShell"
    except Exception:
        return False


def _status(ok, msg):
    """Green/red banner in notebooks; plain text in a terminal."""
    if _in_notebook():
        from IPython.display import HTML, display
        color = "#1a7f37" if ok else "#b42318"
        bg = "#e6f4ea" if ok else "#fdecea"
        display(HTML(
            f'<div style="padding:12px 16px;border-radius:8px;background:{bg};'
            f'border:1.5px solid {color};color:{color};font-weight:600;'
            f'font-size:15px;font-family:sans-serif;">{"✓" if ok else "✗"} {msg}</div>'
        ))
    else:
        print(("✓ " if ok else "✗ ") + msg)


def _stop(head, body):
    """Warning-yellow banner + stop, so a problem shows up here rather than several cells later."""
    if _in_notebook():
        import html
        from IPython.display import HTML, display
        display(HTML(
            '<div style="padding:12px 16px;border-radius:8px;background:#fff8c5;'
            'border:1.5px solid #9a6700;font-size:15px;font-family:sans-serif;">'
            '<div style="color:#9a6700;font-weight:600;">' + html.escape(head) + '</div>'
            '<pre style="margin:10px 0 0;font-family:inherit;font-size:14px;font-weight:400;'
            'color:#141413;white-space:pre-wrap;">' + html.escape(body) + '</pre></div>'
        ))
        raise SystemExit("Stopped — see the message above.")
    raise SystemExit("\n" + head + "\n   " + body.replace("\n", "\n   ") + "\n")


# =============================================================================
# One-time setup — python3 basecamp_setup.py (Windows: py basecamp_setup.py)
# =============================================================================

def main():
    for stream in (sys.stdout, sys.stderr):
        # Windows consoles: replace characters they can't show instead of crashing. Line
        # buffering keeps these messages in order with uv's own output.
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace", line_buffering=True)
    print("Partner Basecamp setup\n")
    uv = _find_uv() or _install_uv()
    _ask_credentials()
    _step("Installing the exercise packages into .venv (first run takes a minute)")
    _sync(uv)
    _step("Registering the Jupyter kernel")
    _run([uv, "run", "--no-sync", "python", "-m", "ipykernel", "install", "--user",
          "--name", KERNEL_NAME, "--display-name", KERNEL_DISPLAY_NAME], quiet=True)
    print(f"✓ Kernel \"{KERNEL_DISPLAY_NAME}\" is available in VS Code and Jupyter.")
    _point_vscode_at_venv()
    _step("Checking your connection to Claude")
    check = subprocess.run([uv, "run", "--no-sync", "python", "-c",
                            "import basecamp_setup; basecamp_setup.connect()"], cwd=ROOT)
    if check.returncode != 0:
        raise SystemExit("\nSetup finished, but the connection check failed — fix what it "
                         f"says above, then run  {SETUP_COMMAND}  again.")
    print(f"\n✓ All set. Open any notebook in VS Code; if it doesn't pick the kernel by itself, "
          f"click the kernel name (top right) and choose \"{KERNEL_DISPLAY_NAME}\".")


def _step(title):
    print(f"\n── {title} ──")


def _run(cmd, quiet=False):
    result = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=quiet)
    if result.returncode != 0:
        if quiet:
            print((result.stdout or "") + (result.stderr or ""))
        raise SystemExit(f"\n✗ This command failed: {' '.join(map(str, cmd))}")
    return result


# ── uv ───────────────────────────────────────────────────────────────────────

def _find_uv():
    found = shutil.which("uv")
    if found:
        return found
    # Where the official installer puts it, in case this shell's PATH predates the install.
    exe = "uv.exe" if os.name == "nt" else "uv"
    for folder in (pathlib.Path.home() / ".local" / "bin", pathlib.Path.home() / ".cargo" / "bin"):
        if (folder / exe).is_file():
            return str(folder / exe)
    return None


def _install_uv():
    manual = ("Install it yourself, then run this script again:\n"
              "    macOS:   brew install uv\n"
              "    Windows: winget install --id=astral-sh.uv -e\n"
              "    Any:     see https://docs.astral.sh/uv/getting-started/installation/")
    print("This setup uses uv (https://docs.astral.sh/uv/) to manage Python packages, and it "
          "isn't installed yet.")
    if not sys.stdin.isatty() or not _ask_yes_no("Install it now with the official installer?",
                                                 default=True):
        raise SystemExit(manual)
    if os.name == "nt":
        cmd = ["powershell", "-ExecutionPolicy", "ByPass", "-c",
               "irm https://astral.sh/uv/install.ps1 | iex"]
    else:
        cmd = ["sh", "-c", "curl -LsSf https://astral.sh/uv/install.sh | sh"]
    subprocess.run(cmd)
    uv = _find_uv()
    if not uv:
        raise SystemExit("\nCouldn't install uv automatically (a company network can block it).\n"
                         + manual)
    return uv


def _sync(uv):
    """uv sync, retrying with the OS certificate store if a download fails — company networks
    that inspect TLS traffic sign it with a certificate only the OS store trusts."""
    if subprocess.run([uv, "sync"], cwd=ROOT).returncode == 0:
        return
    print("\nRetrying with your system's certificates (common behind a company proxy)…")
    env = dict(os.environ, UV_SYSTEM_CERTS="1", UV_NATIVE_TLS="1")  # new and old uv names
    if subprocess.run([uv, "sync"], cwd=ROOT, env=env).returncode == 0:
        return
    mirror = ('$env:UV_DEFAULT_INDEX="https://<your-mirror>/simple"; ' if os.name == "nt"
              else "UV_DEFAULT_INDEX=https://<your-mirror>/simple ") + SETUP_COMMAND
    raise SystemExit(
        "\n✗ Couldn't install the packages. If your company blocks PyPI, ask IT for the internal\n"
        f"  package mirror and run:  {mirror}\n"
        "  Behind a proxy? Set HTTPS_PROXY first. More in SETUP.md.")


# ── VS Code ──────────────────────────────────────────────────────────────────

def _point_vscode_at_venv():
    """Make .venv VS Code's default interpreter for this folder, so notebooks pick it up."""
    python = ("${workspaceFolder}\\.venv\\Scripts\\python.exe" if os.name == "nt"
              else "${workspaceFolder}/.venv/bin/python")
    path = ROOT / ".vscode" / "settings.json"
    try:
        settings = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except ValueError:  # VS Code allows comments in settings.json; don't rewrite one we can't parse
        print(f"• Left {path} alone (couldn't parse it). Add this line to it yourself:\n"
              f'    "python.defaultInterpreterPath": "{python}"')
        return
    if settings.get("python.defaultInterpreterPath") == python:
        return
    settings["python.defaultInterpreterPath"] = python
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    print("✓ VS Code will use .venv for this folder.")


# ── Credentials → .env ───────────────────────────────────────────────────────

def _ask_credentials():
    _step("Your Claude credentials")
    if not sys.stdin.isatty():
        print("• Not an interactive terminal — keeping .env as it is.")
        return
    for name in ENV_KEYS:
        if os.environ.get(name):
            print(f"• Note: {name} is set in your shell, and the shell wins over .env.")

    current = _read_env()
    summary = _describe(current)
    if summary and _ask_yes_no(f"Your .env already has {summary}. Keep it?", default=True):
        return

    while True:
        key = getpass.getpass("Paste your key — Anthropic API key, company gateway key, or "
                              "Amazon Bedrock API key (input is hidden): ").strip()
        if not key:
            print("  Nothing pasted — try again (Ctrl+C to quit).")
            continue
        print(f"  Got {_mask(key)}")
        if key.startswith("sk-ant-"):
            values = {"ANTHROPIC_API_KEY": key}
            break
        if key.startswith(("ABSK", "bedrock-api-key-")):
            print("  That's an Amazon Bedrock API key.")
            region = _ask("  AWS region your Claude models are enabled in", default="us-east-1")
            base_url = _ask_url("  Company gateway URL, if you go through one (Enter for none)",
                                required=False)
            prefix = _ask("  Model ID prefix your gateway expects — Enter for anthropic. "
                          "(Bedrock's standard)", default="anthropic.") if base_url else ""
            values = {"AWS_BEARER_TOKEN_BEDROCK": key, "AWS_REGION": region,
                      "ANTHROPIC_BASE_URL": base_url,
                      "ANTHROPIC_MODEL_PREFIX": "" if prefix == "anthropic." else prefix}
            break
        print("  That isn't an Anthropic API key (those start with sk-ant-) or an Amazon Bedrock "
              "key, so it's probably from a company gateway.")
        if not _ask_yes_no("  Is it a key for a company gateway?", default=True):
            print("  Check the key and paste it again.")
            continue
        base_url = _ask_url("  Gateway URL (from your gateway team)", required=True)
        prefix = _ask(f"  Model ID prefix your gateway expects — Enter for {DEFAULT_MODEL_PREFIX}, "
                      "or 'none'", default=DEFAULT_MODEL_PREFIX)
        values = {"ANTHROPIC_API_KEY": key, "ANTHROPIC_BASE_URL": base_url,
                  "ANTHROPIC_MODEL_PREFIX": "" if prefix.lower() == "none" else prefix}
        break

    _write_env({name: values.get(name, "") for name in ENV_KEYS})
    print(f"✓ Saved to {ENV_FILE} (gitignored — never committed).")


def _ask(question, default=""):
    answer = input(f"{question} [{default}]: " if default else f"{question}: ").strip()
    return answer or default


def _ask_yes_no(question, default=True):
    answer = input(f"{question} [{'Y/n' if default else 'y/N'}]: ").strip().lower()
    return default if not answer else answer.startswith("y")


def _ask_url(question, required):
    while True:
        url = _ask(question).rstrip("/")
        if not url and not required:
            return ""
        if url.startswith(("https://", "http://")):
            return url
        print("    It should start with https:// — try again.")


def _mask(secret):
    return secret[:7] + "…" + secret[-4:] if len(secret) > 14 else "…" + secret[-2:]


def _read_env():
    """The active KEY=value lines in .env — just enough to summarize what's there."""
    if not ENV_FILE.is_file():
        return {}
    values = {}
    for line in ENV_FILE.read_text(encoding="utf-8-sig").splitlines():
        match = re.match(r"\s*(?:export\s+)?([A-Z_][A-Z0-9_]*)\s*=\s*(.*)", line)
        if match:
            value = re.sub(r"\s+#.*$", "", match.group(2)).strip().strip("'\"")
            if value and not value.startswith("paste-"):
                values[match.group(1)] = value
    return values


def _describe(values):
    if values.get("ANTHROPIC_API_KEY"):
        what = f"a key ({_mask(values['ANTHROPIC_API_KEY'])})"
    elif values.get("AWS_BEARER_TOKEN_BEDROCK"):
        what = f"a Bedrock key ({_mask(values['AWS_BEARER_TOKEN_BEDROCK'])})"
    else:
        return ""
    if values.get("ANTHROPIC_BASE_URL"):
        what += f" for the gateway at {values['ANTHROPIC_BASE_URL']}"
    return what


def _write_env(values):
    """Set each KEY in .env (starting from .env.example), keeping its comments. An empty value
    comments the key out, so switching from a gateway back to a plain key leaves no leftovers."""
    source = ENV_FILE if ENV_FILE.is_file() else ENV_EXAMPLE
    lines = source.read_text(encoding="utf-8-sig").splitlines() if source.is_file() else []
    for name, value in values.items():
        pattern = re.compile(rf"\s*(#\s*)?(export\s+)?{name}\s*=")
        matches = [i for i, line in enumerate(lines) if pattern.match(line)]
        if value:
            if matches:
                lines[matches[0]] = f"{name}={value}"
            else:
                lines.append(f"{name}={value}")
            matches = matches[1:]
        for i in matches:  # any other active line for this key gets commented out
            if not lines[i].lstrip().startswith("#"):
                lines[i] = "# " + lines[i]
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if os.name != "nt":
        ENV_FILE.chmod(0o600)  # readable by you only


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        raise SystemExit(f"\nStopped. Run  {SETUP_COMMAND}  again whenever you're ready.")
