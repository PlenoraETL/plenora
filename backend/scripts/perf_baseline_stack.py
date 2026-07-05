"""Baseline performance dello stack Plenora.

Misura 4 metriche operative dello stack runtime, da rieseguire con gli
stessi parametri quando cambia l'infrastruttura per confronti omogenei:

1. latenza per-richiesta autenticata (check blacklist JWT per-request);
2. latenza dispatch + lettura stato task (broker+result backend);
3. roundtrip task no-op (proxy dell'overhead infrastrutturale pipeline);
4. tentativi/sec sostenuti dal rate-limit login.

Eseguire DENTRO il container backend (rete compose, import app):

    docker compose exec -T backend python scripts/perf_baseline_stack.py \
        --username perfadmin --password '...' > baseline.json

Output: JSON su stdout (p50/p95/p99 in ms). Versionare il risultato in
docs/perf/.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time

import requests

BASE = "http://localhost:5004"


def _percentiles(samples_ms: list[float]) -> dict:
    s = sorted(samples_ms)
    n = len(s)
    return {
        "n": n,
        "p50_ms": round(s[int(n * 0.50)], 2),
        "p95_ms": round(s[min(n - 1, int(n * 0.95))], 2),
        "p99_ms": round(s[min(n - 1, int(n * 0.99))], 2),
        "mean_ms": round(statistics.fmean(s), 2),
    }


def bench_authenticated_requests(token: str, iterations: int) -> dict:
    """GET /api/auth/me: auth-only e leggero — domina il check blacklist."""
    headers = {"Authorization": f"Bearer {token}"}
    session = requests.Session()
    # warmup
    for _ in range(10):
        session.get(f"{BASE}/api/auth/me", headers=headers, timeout=10)
    samples: list[float] = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        r = session.get(f"{BASE}/api/auth/me", headers=headers, timeout=10)
        samples.append((time.perf_counter() - t0) * 1000)
        assert r.status_code == 200, r.text
    return _percentiles(samples)


def bench_health_control(iterations: int) -> dict:
    """Controllo: stesso percorso HTTP senza auth (isola l'overhead JWT+blacklist)."""
    session = requests.Session()
    for _ in range(10):
        session.get(f"{BASE}/health", timeout=10)
    samples: list[float] = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        session.get(f"{BASE}/health", timeout=10)
        samples.append((time.perf_counter() - t0) * 1000)
    return _percentiles(samples)


def bench_task_roundtrip(iterations: int) -> dict:
    """Dispatch cleanroom.ping + polling stato fino a SUCCESS.

    Misura separatamente la latenza del solo dispatch (broker write) e il
    roundtrip completo (worker pickup + stato task su PostgreSQL).
    """
    from app.core.task_runtime.dispatch import dispatch_task_by_name
    from app.core.orchestrator.pg_task_runtime import PgResultTaskRuntime

    runtime = PgResultTaskRuntime()
    dispatch_ms: list[float] = []
    roundtrip_ms: list[float] = []
    poll_reads_ms: list[float] = []

    for _ in range(iterations):
        t0 = time.perf_counter()
        handle = dispatch_task_by_name("cleanroom.ping")
        dispatch_ms.append((time.perf_counter() - t0) * 1000)

        task_id = handle.id
        deadline = time.time() + 60
        while time.time() < deadline:
            tr = time.perf_counter()
            result = runtime.get_result(task_id)
            state = str(result.state).upper()
            poll_reads_ms.append((time.perf_counter() - tr) * 1000)
            if state in ("SUCCESS", "FAILURE"):
                break
            time.sleep(0.05)
        roundtrip_ms.append((time.perf_counter() - t0) * 1000)

    return {
        "dispatch": _percentiles(dispatch_ms),
        "roundtrip": _percentiles(roundtrip_ms),
        "status_read": _percentiles(poll_reads_ms),
    }


def bench_login_rate_limit(iterations: int) -> dict:
    """POST /login con password errata: latenza per-tentativo del percorso
    rate-limiter e soglia di blocco osservata."""
    session = requests.Session()
    samples: list[float] = []
    first_429_at = None
    t_start = time.perf_counter()
    for i in range(iterations):
        t0 = time.perf_counter()
        r = session.post(
            f"{BASE}/api/auth/login",
            json={"username": "rate-limit-probe-user", "password": "wrong-password-123"},
            timeout=10,
        )
        samples.append((time.perf_counter() - t0) * 1000)
        if r.status_code == 429 and first_429_at is None:
            first_429_at = i + 1
    elapsed = time.perf_counter() - t_start
    return {
        "attempt_latency": _percentiles(samples),
        "attempts_per_sec": round(iterations / elapsed, 1),
        "first_429_at_attempt": first_429_at,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--iterations", type=int, default=200)
    parser.add_argument("--task-iterations", type=int, default=20)
    args = parser.parse_args()

    login = requests.post(
        f"{BASE}/api/auth/login",
        json={"username": args.username, "password": args.password},
        timeout=10,
    )
    login.raise_for_status()
    token = login.json()["access_token"]

    report = {
        "stack": "current-runtime",
        "health_unauthenticated_control": bench_health_control(args.iterations),
        "authenticated_request_blacklist_path": bench_authenticated_requests(
            token, args.iterations
        ),
        "task_dispatch_and_status": bench_task_roundtrip(args.task_iterations),
        "login_rate_limit": bench_login_rate_limit(60),
    }
    json.dump(report, sys.stdout, indent=2)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
