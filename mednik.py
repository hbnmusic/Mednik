#!/usr/bin/env python3
"""Cross-platform task runner for mednik (macOS, Linux, Windows).

    python mednik.py setup     # create backend/.venv, install Python + Node dependencies, copy .env.example → .env
    python mednik.py dev       # run API (http://localhost:8000) + dashboard (http://localhost:3000)
    python mednik.py test      # offline backend test suite
    python mednik.py verify    # Step 6 live end-to-end verification (needs internet access to FDA / NLM / CT.gov / NCBI / EBI)
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
WIN = os.name == "nt"
VENV_PY = BACKEND / ".venv" / ("Scripts/python.exe" if WIN else "bin/python")
NPM = "npm.cmd" if WIN else "npm"


# UTF-8 everywhere (Windows consoles / redirected output default to cp1252)
ENV = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}


def run(cmd: list[str], cwd: Path) -> None:
    print(f"$ {' '.join(cmd)}  (in {cwd.name})", flush=True)
    subprocess.run(cmd, cwd=cwd, check=True, env=ENV)


def check_versions() -> None:
    if sys.version_info < (3, 11):
        sys.exit(f"Python 3.11+ required (found {sys.version.split()[0]}).")
    node = shutil.which("node")
    if not node:
        sys.exit("Node.js 20+ is required for the dashboard: https://nodejs.org/")
    out = subprocess.run([node, "--version"], capture_output=True, text=True).stdout.strip()
    if int(out.lstrip("v").split(".")[0]) < 20:
        sys.exit(f"Node.js 20+ required (found {out}).")


def setup() -> None:
    check_versions()
    if not VENV_PY.exists():
        run([sys.executable, "-m", "venv", ".venv"], BACKEND)
    run([str(VENV_PY), "-m", "pip", "install", "--upgrade", "pip"], BACKEND)
    run([str(VENV_PY), "-m", "pip", "install", "-r", "requirements.txt"], BACKEND)
    run([NPM, "install"], FRONTEND)
    env = ROOT / ".env"
    if not env.exists():
        shutil.copy(ROOT / ".env.example", env)
        print("Created .env from .env.example — add GEMINI_API_KEY to enable LLM extraction (optional).")
    print("\nSetup complete. Next: python mednik.py dev")


def dev() -> None:
    if not VENV_PY.exists():
        sys.exit("Run `python mednik.py setup` first.")
    procs = [
        subprocess.Popen([str(VENV_PY), "-m", "uvicorn", "mednik.api.main:app", "--port", "8000", "--reload"], cwd=BACKEND, env=ENV),
        subprocess.Popen([NPM, "run", "dev", "--", "-p", "3000"], cwd=FRONTEND, env=ENV),
    ]
    print("\nmednik API → http://localhost:8000/docs   dashboard → http://localhost:3000   (Ctrl+C to stop)\n", flush=True)
    try:
        while all(p.poll() is None for p in procs):
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        for p in procs:
            if p.poll() is None:
                if WIN:
                    p.terminate()
                else:
                    p.send_signal(signal.SIGINT)
        for p in procs:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()


def test() -> None:
    run([str(VENV_PY), "-m", "pytest", "-q"], BACKEND)


def verify() -> None:
    run([str(VENV_PY), "scripts/verify_e2e.py", *sys.argv[2:]], BACKEND)


if __name__ == "__main__":
    cmds = {"setup": setup, "dev": dev, "test": test, "verify": verify}
    if len(sys.argv) < 2 or sys.argv[1] not in cmds:
        print(__doc__)
        sys.exit(1)
    cmds[sys.argv[1]]()
