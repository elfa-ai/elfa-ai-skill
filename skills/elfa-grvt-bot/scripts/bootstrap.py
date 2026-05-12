#!/usr/bin/env python3
"""
bootstrap.py: end-to-end orchestrator for elfa_grvt_bot.

What this does, in order:
  1. Validate the working directory has the bot source (or copy from skill bundle).
  2. Create venv if missing, install deps, run tests.
  3. Read .env, report any missing required vars, exit if incomplete.
  4. Detect cloudflared; auto-install via brew on macOS if absent.
  5. Start the receiver as a detached background process.
  6. Wait for the receiver to report healthy on localhost:8000/healthz.
  7. Start cloudflared as a detached background process.
  8. Tail cloudflared output until the trycloudflare URL appears.
  9. Write the URL into .env as RECEIVER_PUBLIC_URL.
  10. Verify the public tunnel responds with {"ok":true}.
  11. Print a summary plus PIDs and log paths so the user can manage processes.

After this completes the user can open their preferred agent in the working
directory and start authoring strategies. The receiver and tunnel keep running in
the background; use `bash teardown.sh` to stop both.

Idempotent: rerunning detects existing processes and reuses them where possible.
"""
from __future__ import annotations

import argparse
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve().parent
SKILL_ROOT = HERE.parent
SOURCE_DIR = SKILL_ROOT / "assets" / "source"

REQUIRED_ENV_VARS = [
    "ELFA_API_KEY",
    "GRVT_API_KEY",
    "GRVT_PRIVATE_KEY",
    "GRVT_TRADING_ACCOUNT_ID",
    "REGISTRY_DB_PATH",
]

# Optional. If both are set, Telegram alerts are enabled. If either is
# missing, alerts go to the local registry only and the agent session
# can surface them through AGENTS.md or client-specific hooks.
OPTIONAL_ENV_VARS = [
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
]

DEFAULT_TARGET = Path.home() / "elfa_grvt_bot"
LOCAL_RECEIVER_URL = "http://localhost:8000"
HEALTHZ_PATH = "/healthz"
RECEIVER_LOG = "receiver.log"
CLOUDFLARED_LOG = "cloudflared.log"
RECEIVER_PID = ".receiver.pid"
CLOUDFLARED_PID = ".cloudflared.pid"
TUNNEL_URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


def log(msg: str, ok: bool = True) -> None:
    prefix = "[ok]" if ok else "[!!]"
    print(f"{prefix} {msg}", flush=True)


# Progress tracking. Bootstrap announces phase boundaries as it runs so the
# user (and any orchestrating LLM) can see how far along we are. The
# percentages are coarse milestones, not a smooth curve — they map to the
# 9 user-visible phases listed in announce_plan().
PHASE_NAMES = [
    "deploy source",
    "create venv",
    "install dependencies",
    "run test suite",
    "validate .env",
    "install cloudflared",
    "start receiver",
    "start cloudflared tunnel",
    "verify tunnel end to end",
]


def announce_plan(skip_tests: bool) -> None:
    print()
    print("=" * 60)
    print("Setup plan — 9 phases:")
    for i, name in enumerate(PHASE_NAMES, 1):
        suffix = "  (skipped via --skip-tests)" if skip_tests and "test" in name else ""
        print(f"  {i}. {name}{suffix}")
    print("=" * 60)
    print()


def phase(idx: int, name: str) -> None:
    """Print a banner before phase `idx` (1-based) starts."""
    pct = int(round(100 * (idx - 1) / len(PHASE_NAMES)))
    print()
    print(f"--- [{pct}% complete] phase {idx}/{len(PHASE_NAMES)}: {name} ---")


def run(cmd: list, cwd: Optional[Path] = None, check: bool = True,
        capture: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, cwd=cwd, check=check,
        capture_output=capture, text=True,
    )


def parse_env_file(path: Path) -> dict:
    out: dict = {}
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def write_env_var(path: Path, key: str, value: str) -> None:
    lines = path.read_text().splitlines() if path.exists() else []
    found = False
    for i, line in enumerate(lines):
        if line.startswith(f"{key}="):
            lines[i] = f"{key}={value}"
            found = True
            break
    if not found:
        lines.append(f"{key}={value}")
    path.write_text("\n".join(lines) + "\n")


def http_ok(url: str, timeout: float = 5.0) -> bool:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "bootstrap.py"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                return False
            body = resp.read().decode(errors="replace")
            return '"ok":true' in body.replace(" ", "")
    except Exception:
        return False


def is_pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def read_pid(path: Path) -> Optional[int]:
    if not path.exists():
        return None
    try:
        pid = int(path.read_text().strip())
        return pid if is_pid_alive(pid) else None
    except (ValueError, OSError):
        return None


def write_pid(path: Path, pid: int) -> None:
    path.write_text(str(pid))


def step_deploy_source(target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    pyproject = target / "pyproject.toml"
    if pyproject.exists():
        log(f"target already has source ({target}); skipping copy")
        return
    if not SOURCE_DIR.exists():
        raise SystemExit(
            f"bundled source not found at {SOURCE_DIR}. "
            "Are you running bootstrap.py from inside the unpacked .skill?"
        )
    log(f"copying source from {SOURCE_DIR} into {target}")
    run(["cp", "-R", f"{SOURCE_DIR}/.", f"{target}/"])
    agent_template = target / "AGENTS.template.md"
    agent_instructions = target / "AGENTS.md"
    if agent_template.exists() and not agent_instructions.exists():
        shutil.copyfile(agent_template, agent_instructions)
    log(f"source copied ({sum(1 for _ in target.rglob('*') if _.is_file())} files)")


def step_create_venv(target: Path) -> Path:
    venv = target / ".venv"
    if venv.exists():
        log("venv already exists")
    else:
        log("creating venv")
        run([sys.executable, "-m", "venv", str(venv)])
    return venv


def step_install_deps(target: Path, venv: Path) -> None:
    pip = venv / "bin" / "pip"
    log("installing dependencies (this can take a minute)")
    run([str(pip), "install", "-q", "--upgrade", "pip"], cwd=target)
    run([str(pip), "install", "-q", "-e", ".[dev]"], cwd=target)


def step_run_tests(target: Path, venv: Path) -> None:
    pytest = venv / "bin" / "pytest"
    log("running test suite")
    proc = run([str(pytest), "-q"], cwd=target, check=False, capture=True)
    if proc.returncode != 0:
        print(proc.stdout)
        print(proc.stderr, file=sys.stderr)
        raise SystemExit("tests failed; fix before continuing")
    last = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else "(no output)"
    print(last)


def step_check_env(target: Path) -> dict:
    env_path = target / ".env"
    example = target / ".env.example"
    if not env_path.exists():
        if example.exists():
            shutil.copyfile(example, env_path)
            log(f"created {env_path} from .env.example")
        else:
            raise SystemExit(f"no {env_path} and no .env.example to copy from")

    env = parse_env_file(env_path)
    missing = [k for k in REQUIRED_ENV_VARS if not env.get(k)]
    if missing:
        print()
        print("Required env vars are not set in .env:")
        for k in missing:
            print(f"  - {k}")
        print()
        print(f"Edit {env_path} and re-run bootstrap.")
        raise SystemExit("env incomplete")

    optional_missing = [k for k in OPTIONAL_ENV_VARS if not env.get(k)]
    if optional_missing:
        log(
            "optional vars not set: "
            + ", ".join(optional_missing)
            + " (Telegram alerts disabled; in-chat updates still work)"
        )
    else:
        log("Telegram credentials present; real-time push enabled")

    log(".env has all required vars")
    return env


def step_install_cloudflared() -> str:
    path = shutil.which("cloudflared")
    if path:
        log(f"cloudflared found at {path}")
        return path

    if platform.system() == "Darwin" and shutil.which("brew"):
        log("cloudflared not found; installing via brew")
        run(["brew", "install", "cloudflared"])
        path = shutil.which("cloudflared")
        if path:
            return path

    raise SystemExit(
        "cloudflared is not installed. Install manually:\n"
        "  macOS:  brew install cloudflared\n"
        "  Linux:  https://github.com/cloudflare/cloudflared/releases\n"
        "Then re-run bootstrap."
    )


def step_start_receiver(target: Path, venv: Path) -> int:
    pid_file = target / RECEIVER_PID
    existing = read_pid(pid_file)
    if existing and http_ok(LOCAL_RECEIVER_URL + HEALTHZ_PATH):
        log(f"receiver already running (pid {existing})")
        return existing
    if existing:
        log(f"stale receiver pid file (pid {existing} not responsive); restarting", ok=False)

    python = venv / "bin" / "python"
    log_file = target / RECEIVER_LOG
    log(f"starting receiver, logging to {log_file}")

    env = os.environ.copy()
    env.update(parse_env_file(target / ".env"))

    f = open(log_file, "ab")
    proc = subprocess.Popen(
        [str(python), "-m", "elfa_grvt_bot"],
        cwd=target, env=env,
        stdout=f, stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    write_pid(pid_file, proc.pid)
    log(f"receiver pid={proc.pid}")

    deadline = time.time() + 30
    while time.time() < deadline:
        if not is_pid_alive(proc.pid):
            print(log_file.read_text()[-2000:], file=sys.stderr)
            raise SystemExit("receiver exited during startup; see log above")
        if http_ok(LOCAL_RECEIVER_URL + HEALTHZ_PATH):
            log("receiver is healthy")
            return proc.pid
        time.sleep(1)

    raise SystemExit(f"receiver did not become healthy in 30s; check {log_file}")


def step_start_tunnel(target: Path, cloudflared: str):
    pid_file = target / CLOUDFLARED_PID
    log_file = target / CLOUDFLARED_LOG

    existing = read_pid(pid_file)
    if existing:
        if log_file.exists():
            for line in reversed(log_file.read_text().splitlines()):
                m = TUNNEL_URL_RE.search(line)
                if m:
                    log(f"tunnel already running (pid {existing}, {m.group(0)})")
                    return existing, m.group(0)

    log(f"starting cloudflared tunnel, logging to {log_file}")
    f = open(log_file, "ab")
    proc = subprocess.Popen(
        [cloudflared, "tunnel", "--url", LOCAL_RECEIVER_URL],
        cwd=target,
        stdout=f, stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    write_pid(pid_file, proc.pid)
    log(f"cloudflared pid={proc.pid}")

    deadline = time.time() + 60
    url: Optional[str] = None
    while time.time() < deadline:
        if not is_pid_alive(proc.pid):
            print(log_file.read_text()[-2000:], file=sys.stderr)
            raise SystemExit("cloudflared exited during startup")
        if log_file.exists():
            text = log_file.read_text()
            m = TUNNEL_URL_RE.search(text)
            if m:
                url = m.group(0)
                break
        time.sleep(1)

    if not url:
        raise SystemExit(f"cloudflared did not print a URL in 60s; check {log_file}")

    log(f"tunnel URL: {url}")
    return proc.pid, url


def step_write_tunnel_url(target: Path, url: str) -> None:
    write_env_var(target / ".env", "RECEIVER_PUBLIC_URL", url)
    log(f"wrote RECEIVER_PUBLIC_URL={url} to .env")


def step_verify_tunnel(url: str) -> None:
    log("verifying tunnel reachability via Cloudflare edge")
    deadline = time.time() + 30
    while time.time() < deadline:
        if http_ok(url + HEALTHZ_PATH):
            log("tunnel responds {ok:true}")
            return
        time.sleep(2)
    log(
        "tunnel did not respond within 30s. Cloudflare's edge often takes "
        "a moment to propagate; you can re-check with curl. Elfa's edge "
        "can resolve fresh trycloudflare hostnames immediately so this "
        "is usually not a blocker.",
        ok=False,
    )


def step_write_teardown(target: Path) -> None:
    script = target / "teardown.sh"
    script.write_text(
        "#!/usr/bin/env bash\n"
        "# teardown.sh: stop the receiver and cloudflared started by bootstrap.py.\n"
        "set -e\n"
        "cd \"$(dirname \"$0\")\"\n"
        "for f in .receiver.pid .cloudflared.pid; do\n"
        "  if [[ -f \"$f\" ]]; then\n"
        "    pid=$(cat \"$f\")\n"
        "    if kill -0 \"$pid\" 2>/dev/null; then\n"
        "      echo \"stopping pid $pid (from $f)\"\n"
        "      kill \"$pid\" 2>/dev/null || true\n"
        "      sleep 1\n"
        "      kill -9 \"$pid\" 2>/dev/null || true\n"
        "    fi\n"
        "    rm -f \"$f\"\n"
        "  fi\n"
        "done\n"
        "echo \"teardown complete\"\n"
    )
    script.chmod(0o755)


def main() -> None:
    parser = argparse.ArgumentParser(description="elfa_grvt_bot bootstrap")
    parser.add_argument(
        "--target", default=str(DEFAULT_TARGET),
        help="working directory for the bot (default: ~/elfa_grvt_bot)",
    )
    parser.add_argument(
        "--skip-tests", action="store_true",
        help="skip running pytest after install",
    )
    args = parser.parse_args()

    target = Path(args.target).expanduser().resolve()
    log(f"target: {target}")

    announce_plan(args.skip_tests)

    phase(1, "deploy source")
    step_deploy_source(target)

    phase(2, "create venv")
    venv = step_create_venv(target)

    phase(3, "install dependencies")
    step_install_deps(target, venv)

    if not args.skip_tests:
        phase(4, "run test suite")
        step_run_tests(target, venv)

    phase(5, "validate .env")
    step_check_env(target)

    phase(6, "install cloudflared")
    cloudflared = step_install_cloudflared()

    phase(7, "start receiver")
    receiver_pid = step_start_receiver(target, venv)

    phase(8, "start cloudflared tunnel")
    tunnel_pid, tunnel_url = step_start_tunnel(target, cloudflared)
    step_write_tunnel_url(target, tunnel_url)

    phase(9, "verify tunnel end to end")
    step_verify_tunnel(tunnel_url)
    step_write_teardown(target)

    print()
    print("--- [100% complete] all phases done ---")

    print()
    print("=" * 60)
    print("Bootstrap complete.")
    print()
    print(f"Working directory:    {target}")
    print(f"Receiver pid:         {receiver_pid}   (log: {target}/{RECEIVER_LOG})")
    print(f"Cloudflared pid:      {tunnel_pid}   (log: {target}/{CLOUDFLARED_LOG})")
    print(f"Public URL:           {tunnel_url}")
    print()
    print("Next steps:")
    print(f"  1. cd {target}")
    print(f"  2. Open your preferred agent in this directory")
    print(f"  3. Describe a strategy in chat. The agent follows AGENTS.md.")
    print()
    print(f"To stop the receiver and tunnel:")
    print(f"  bash {target}/teardown.sh")
    print()
    print(
        "Tunnel URLs are ephemeral (random subdomain reassigned on each "
        "cloudflared restart). For sustained use, set up a named "
        "cloudflared tunnel or migrate to a PaaS (Fly.io, Railway). "
        "See references/setup.md."
    )


if __name__ == "__main__":
    main()
