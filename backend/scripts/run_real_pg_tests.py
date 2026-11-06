"""Lancia i test su PostgreSQL VERO (marcatore ``real_pg``) in un comando.

UI conferma finale (06/11/2026): i test che una firma SQL non basta a
garantire (query delle sorgenti Manipola, pagine dei file, migrazioni
CONCURRENTLY con advisory lock e attese, regola del manifest) girano solo con
``PLENORA_REAL_PG_DSN``. In CI non c'e' un PostgreSQL: li salta (limite
dichiarato in ``tests2/shared/_real_pg.py``). Questo script li rende
eseguibili su qualunque macchina con Docker:

    python backend/scripts/run_real_pg_tests.py
    python backend/scripts/run_real_pg_tests.py --docker "wsl -e docker"
    python backend/scripts/run_real_pg_tests.py --dsn postgresql://u:p@host:5432/db

1. avvia un container ``postgres:17-alpine`` usa-e-getta su una porta libera
   (salvo ``--dsn``: PostgreSQL gia' acceso);
2. aspetta che accetti connessioni;
3. esegue ``pytest -m real_pg`` con ``PLENORA_REAL_PG_DSN`` sui file di tests2
   che dichiarano test ``real_pg`` (niente raccolta dell'intera suite);
4. rimuove il container (anche se i test falliscono).

Docker dentro WSL: WSL spegne la sua macchina virtuale (e i container) quando
nessun processo WSL e' attivo; con ``--docker "wsl ..."`` lo script tiene
acceso un ``sleep`` WSL per tutta la durata.

Esce con il codice di pytest. Nessun test viene saltato in silenzio: con il
DSN impostato un test ``real_pg`` o passa o fallisce.
"""

from __future__ import annotations

import argparse
import os
import shlex
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
IMAGE = "postgres:17-alpine"
PASSWORD = "plenora-real-pg"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait_ready(dsn: str, timeout: float = 60.0) -> None:
    import psycopg

    deadline = time.monotonic() + timeout
    last: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with psycopg.connect(dsn, connect_timeout=3) as conn:
                conn.execute("SELECT 1")
            return
        except Exception as exc:  # il server sta ancora partendo
            last = exc
            time.sleep(1.0)
    raise SystemExit(f"PostgreSQL non pronto in {timeout:.0f} s: {last}")


def real_pg_test_files() -> list[str]:
    """I file di tests2 con almeno un test marcato real_pg."""
    root = BACKEND / "tests2"
    marks = ("real_pg_marks", "pytest.mark.real_pg")
    return sorted(
        p.relative_to(BACKEND).as_posix()
        for p in root.rglob("test_*.py")
        if any(m in p.read_text(encoding="utf-8") for m in marks)
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--docker", default=os.environ.get("PLENORA_DOCKER", "docker"),
                        help='comando docker (es. "wsl -e docker"); default: docker o $PLENORA_DOCKER')
    parser.add_argument("--dsn", default=os.environ.get("PLENORA_REAL_PG_DSN", ""),
                        help="PostgreSQL gia' acceso: nessun container")
    parser.add_argument("pytest_args", nargs="*", help="argomenti in piu' per pytest (dopo --)")
    args = parser.parse_args(argv)

    docker = shlex.split(args.docker)
    container = None
    keepalive = None
    dsn = args.dsn
    if not dsn and Path(docker[0]).stem.lower() == "wsl":
        keepalive = subprocess.Popen([docker[0], "-e", "sleep", "86400"])
    if not dsn:
        port = _free_port()
        container = f"plenora-real-pg-{uuid.uuid4().hex[:8]}"
        subprocess.run(
            [*docker, "run", "-d", "--rm", "--name", container,
             "-e", f"POSTGRES_PASSWORD={PASSWORD}", "-p", f"{port}:5432", IMAGE],
            check=True,
        )
        dsn = f"postgresql://postgres:{PASSWORD}@127.0.0.1:{port}/postgres"
    try:
        _wait_ready(dsn)
        env = dict(os.environ, PLENORA_REAL_PG_DSN=dsn)
        cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-o", "addopts=",
               "-m", "real_pg", *real_pg_test_files(), *args.pytest_args]
        print("+", " ".join(cmd), flush=True)
        return subprocess.run(cmd, cwd=BACKEND, env=env, check=False).returncode
    finally:
        if container:
            subprocess.run([*docker, "rm", "-f", container], check=False,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if keepalive:
            keepalive.terminate()


if __name__ == "__main__":
    raise SystemExit(main())
