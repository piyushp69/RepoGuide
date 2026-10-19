"""Start RepoGuide: the FastAPI backend and the Vite frontend together (Ctrl+C stops both).

    python scripts/dev.py          # development: backend :8000 + Vite dev server :5173 (hot reload)
    python scripts/dev.py --prod   # build the frontend once and serve everything from the backend on :8000

Works on Windows, macOS and Linux. Run scripts/setup.py first.
"""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
IS_WINDOWS = os.name == "nt"


def venv_python() -> str:
    for candidate in (ROOT / ".venv" / "Scripts" / "python.exe", ROOT / ".venv" / "bin" / "python"):
        if candidate.exists():
            return str(candidate)
    print("No .venv found - run `python scripts/setup.py` first. Using the current interpreter.", file=sys.stderr)
    return sys.executable


def read_env() -> dict[str, str]:
    values: dict[str, str] = {}
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key.strip().upper()] = value.strip().strip('"').strip("'")
    return values


def pump(prefix: str, process: subprocess.Popen) -> None:
    assert process.stdout is not None
    for line in process.stdout:
        sys.stdout.write(f"[{prefix}] {line}")
        sys.stdout.flush()


def stop(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if IS_WINDOWS:
        # npm spawns child processes; kill the whole tree.
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(process.pid)], capture_output=True)
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()


def spawn(command: list[str], cwd: Path, env: dict[str, str]) -> subprocess.Popen:
    kwargs: dict = {}
    if not IS_WINDOWS:
        kwargs["start_new_session"] = True  # own process group so the whole tree can be stopped
    return subprocess.Popen(command, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace", bufsize=1, **kwargs)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--prod", action="store_true", help="serve the built frontend from the backend")
    args = parser.parse_args()

    if not (ROOT / ".env").exists() and (ROOT / ".env.example").exists():
        shutil.copy(ROOT / ".env.example", ROOT / ".env")
        print("Created .env from .env.example")
    env_values = read_env()
    host = env_values.get("BACKEND_HOST", "127.0.0.1")
    port = env_values.get("BACKEND_PORT", "8000")
    frontend_port = env_values.get("FRONTEND_PORT", "5173")

    npm = shutil.which("npm")
    if npm is None:
        print("npm was not found on PATH. Install Node.js 20+ and run scripts/setup.py.", file=sys.stderr)
        return 1
    if not (FRONTEND / "node_modules").exists():
        print("Installing frontend dependencies (npm ci)...")
        subprocess.run([npm, "ci"], cwd=FRONTEND, check=True)

    env = dict(os.environ)
    env.update({"PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8",
                "REPOGUIDE_BACKEND_URL": f"http://{host}:{port}", "REPOGUIDE_FRONTEND_PORT": frontend_port})

    processes: list[tuple[str, subprocess.Popen]] = []
    if args.prod:
        print("Building the frontend...")
        subprocess.run([npm, "run", "build"], cwd=FRONTEND, check=True, env=env)
    processes.append(("api", spawn([venv_python(), "-m", "repoguide"], BACKEND, env)))
    if not args.prod:
        processes.append(("web", spawn([npm, "run", "dev"], FRONTEND, env)))
    for name, process in processes:
        threading.Thread(target=pump, args=(name, process), daemon=True).start()

    url = f"http://{host}:{port}" if args.prod else f"http://127.0.0.1:{frontend_port}"
    print(f"\nRepoGuide is starting. Open {url}  (API docs: http://{host}:{port}/docs). Press Ctrl+C to stop.\n")
    exit_code = 0
    try:
        while True:
            for name, process in processes:
                code = process.poll()
                if code is not None:
                    print(f"\n[{name}] exited with code {code}; stopping the rest.")
                    exit_code = code or 1
                    raise KeyboardInterrupt
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        for _, process in processes:
            stop(process)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
