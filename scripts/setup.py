"""One-time setup: Python venv + backend packages, frontend packages, .env, and the Ollama models.

    python scripts/setup.py            # everything
    python scripts/setup.py --no-models  # skip `ollama pull`

Needs Python 3.11+, Node.js 20+ (with npm) and Ollama (https://ollama.com). Works on Windows,
macOS and Linux. Network access is only used for package installation and `ollama pull`.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENV = ROOT / ".venv"
IS_WINDOWS = os.name == "nt"


def step(message: str) -> None:
    print(f"\n==> {message}", flush=True)


def run(command: list[str], cwd: Path | None = None) -> None:
    print("   $ " + " ".join(command), flush=True)
    subprocess.run(command, cwd=cwd, check=True)


def venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")


def env_value(key: str, default: str) -> str:
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.strip().upper().startswith(key + "="):
                return line.split("=", 1)[1].strip().strip('"').strip("'") or default
    return default


def ollama_models(base_url: str) -> list[str] | None:
    try:
        with urllib.request.urlopen(f"{base_url}/api/tags", timeout=5) as response:
            return [m["name"] for m in json.load(response).get("models", [])]
    except OSError:
        return None


def has_model(models: list[str], wanted: str) -> bool:
    full = wanted if ":" in wanted else wanted + ":latest"
    return any((m if ":" in m else m + ":latest") == full for m in models)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--no-models", action="store_true", help="do not pull Ollama models")
    args = parser.parse_args()

    step(f"Python {sys.version.split()[0]} ({sys.executable})")
    if sys.version_info < (3, 11):
        print("RepoGuide needs Python 3.11 or newer.", file=sys.stderr)
        return 1

    step("Creating the virtual environment (.venv)")
    if not venv_python().exists():
        run([sys.executable, "-m", "venv", str(VENV)])
    run([str(venv_python()), "-m", "pip", "install", "--upgrade", "pip"])
    run([str(venv_python()), "-m", "pip", "install", "-r", str(ROOT / "backend" / "requirements.txt")])

    step("Installing frontend packages")
    npm = shutil.which("npm")
    if npm is None:
        print("npm not found. Install Node.js 20+ from https://nodejs.org and re-run.", file=sys.stderr)
        return 1
    lock = ROOT / "frontend" / "package-lock.json"
    run([npm, "ci" if lock.exists() else "install"], cwd=ROOT / "frontend")

    step("Configuration")
    if not (ROOT / ".env").exists():
        shutil.copy(ROOT / ".env.example", ROOT / ".env")
        print("   Created .env from .env.example")
    else:
        print("   .env already exists (kept)")

    step("Ollama")
    base_url = env_value("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
    chat_model = env_value("CHAT_MODEL", "qwen2.5-coder:7b")
    embed_model = env_value("EMBED_MODEL", "nomic-embed-text")
    ollama = shutil.which("ollama")
    if ollama is None:
        print("   Ollama is not installed. Install it from https://ollama.com, then run:\n"
              f"     ollama pull {chat_model}\n     ollama pull {embed_model}")
        return 1
    models = ollama_models(base_url)
    if models is None:
        print(f"   Ollama is installed but not answering at {base_url}. Start it (`ollama serve` or the Ollama app) "
              "and re-run this script.")
        return 1
    for model in (embed_model, chat_model):
        if has_model(models, model):
            print(f"   {model}: already available")
        elif args.no_models:
            print(f"   {model}: missing (skipped because of --no-models)")
        else:
            run([ollama, "pull", model])

    step("Done")
    starter = r".\start.ps1" if IS_WINDOWS else "./start.sh"
    print(f"   Start RepoGuide with:  {starter}   (or: {venv_python()} scripts/dev.py)")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except subprocess.CalledProcessError as exc:
        print(f"\nSetup failed while running: {' '.join(map(str, exc.cmd))}", file=sys.stderr)
        sys.exit(exc.returncode or 1)
