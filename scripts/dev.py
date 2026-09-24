"""Local stack commands, run through npm from the project root.

Usage:
    npm start          # databases + API on :8000 + web client on :5500
    npm run seed       # load seed data (skips if already present)
    npm run reset      # wipe both databases, then reseed
    npm run stop       # stop the database containers

Every command first brings up what it depends on: docker compose (waits for
healthy), .venv + requirements, alembic upgrade head. Each step skips when
already done. Ctrl+C on `start` stops both servers; the containers keep
running.

Stdlib only, so it runs with any system Python before the venv exists, on
Linux, macOS and Windows. scripts/run.mjs picks the Python.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
VENV = ROOT / ".venv"
IS_WINDOWS = os.name == "nt"
VENV_PY = VENV / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")


def step(msg: str) -> None:
    print(f"\n==> {msg}", flush=True)


def run(*cmd: str | Path, cwd: Path = ROOT) -> None:
    result = subprocess.run([str(c) for c in cmd], cwd=cwd)
    if result.returncode != 0:
        sys.exit(f"failed ({result.returncode}): {' '.join(map(str, cmd))}")


def require(tool: str) -> str:
    path = shutil.which(tool)
    if path is None:
        sys.exit(f"'{tool}' not found on PATH")
    return path


def check_env() -> None:
    env = ROOT / ".env"
    if not env.exists():
        shutil.copy(ROOT / ".env.example", env)
        sys.exit(".env created from .env.example - set the passwords and JWT_SECRET, then re-run")
    if "JWT_SECRET=REPLACE_ME" in env.read_text():
        sys.exit(
            "JWT_SECRET in .env is still the placeholder. Generate one:\n"
            '  python -c "import secrets; print(secrets.token_urlsafe(48))"'
        )


def ensure_venv() -> None:
    # Rebuild when the venv was copied from another OS (no interpreter at this
    # platform's path), half-created (no pip), or built by a different Python
    # than the one running this script.
    want = f"{sys.version_info.major}.{sys.version_info.minor}"
    probe = "import sys, pip; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
    usable = VENV_PY.exists() and subprocess.run(
        [str(VENV_PY), "-c", probe], capture_output=True, text=True
    ).stdout.strip() == want
    if not usable:
        step(f"creating .venv (Python {want})")
        run(sys.executable, "-m", "venv", "--clear", VENV)

    # Reinstall only when requirements.txt changes.
    reqs = ROOT / "requirements.txt"
    digest = hashlib.sha256(reqs.read_bytes()).hexdigest()
    stamp = VENV / ".requirements.sha256"
    if stamp.exists() and stamp.read_text() == digest:
        return
    step("installing Python requirements")
    run(VENV_PY, "-m", "pip", "install", "--quiet", "--upgrade", "pip")
    run(VENV_PY, "-m", "pip", "install", "-r", reqs)
    stamp.write_text(digest)


def ensure_node_modules(npm: str) -> None:
    # npm writes node_modules/.package-lock.json on every install; if it is
    # older than the lockfile, dependencies changed. A missing or non-executable
    # bin/vite means a node_modules copied from another OS.
    installed = FRONTEND / "node_modules" / ".package-lock.json"
    lock = FRONTEND / "package-lock.json"
    vite = FRONTEND / "node_modules" / ".bin" / ("vite.cmd" if IS_WINDOWS else "vite")
    fresh = (
        installed.exists()
        and os.access(vite, os.X_OK)
        and installed.stat().st_mtime >= lock.stat().st_mtime
    )
    if fresh:
        return
    step("installing frontend dependencies")
    run(npm, "install", cwd=FRONTEND)


def serve(npm: str) -> int:
    step("starting API on http://localhost:8000 and web client on http://localhost:5500")
    procs = [
        subprocess.Popen([str(VENV_PY), "-m", "uvicorn", "app.main:app", "--reload"], cwd=ROOT),
        subprocess.Popen([npm, "run", "dev"], cwd=FRONTEND),
    ]
    try:
        # Exit as soon as either server dies so a crash is not hidden.
        while all(p.poll() is None for p in procs):
            time.sleep(0.5)
        return next(p.returncode for p in procs if p.returncode is not None)
    except KeyboardInterrupt:
        return 0
    finally:
        for p in procs:
            if p.poll() is None:
                p.terminate()
        for p in procs:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()


def prepare_backend(docker: str) -> None:
    check_env()
    step("starting PostgreSQL and MongoDB")
    run(docker, "compose", "up", "-d", "--wait")
    ensure_venv()
    step("applying migrations")
    run(VENV_PY, "-m", "alembic", "upgrade", "head")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["start", "seed", "reset"])
    args = parser.parse_args()

    docker = require("docker")

    if args.command == "seed":
        prepare_backend(docker)
        step("seeding")
        run(VENV_PY, "scripts/seed.py")
        return 0

    if args.command == "reset":
        prepare_backend(docker)
        step("wiping both databases and reseeding")
        run(VENV_PY, "scripts/seed.py", "--reset")
        return 0

    npm = require("npm")
    prepare_backend(docker)
    # --verify exits non-zero when the tables hold less than the seed minimum.
    if subprocess.run([str(VENV_PY), "scripts/seed.py", "--verify"], cwd=ROOT, capture_output=True).returncode:
        print("\n  Databases are empty or partial - run `npm run seed` to load demo data.")
    ensure_node_modules(npm)
    return serve(npm)


if __name__ == "__main__":
    sys.exit(main())
