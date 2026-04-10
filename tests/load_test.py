"""
Load Testing — URBY-ETL

Testa il comportamento del server sotto carico:
  - Throughput: quante req/s gestisce?
  - Latenza: p50, p95, p99
  - Errori sotto carico: 500, timeout, connection refused?
  - Concurrency: 50 thread paralleli
  - Memory: il server regge senza OOM?

Esegui: PYTHONIOENCODING=utf-8 python load_test.py
"""

import requests
import time
import statistics
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE = "http://127.0.0.1:8000/api"


def login():
    r = requests.post(f"{BASE}/auth/login", json={"username": "admin", "password": "admin123"})
    return r.json()["access_token"]


def timed_request(method, url, headers, json_data=None, timeout=10):
    """Esegue una request e ritorna (status_code, latency_ms, error)."""
    start = time.perf_counter()
    try:
        if method == "GET":
            r = requests.get(url, headers=headers, timeout=timeout)
        elif method == "POST":
            r = requests.post(url, headers=headers, json=json_data, timeout=timeout)
        elif method == "PUT":
            r = requests.put(url, headers=headers, json=json_data, timeout=timeout)
        elif method == "DELETE":
            r = requests.delete(url, headers=headers, timeout=timeout)
        else:
            return 0, 0, f"Unknown method {method}"
        latency = (time.perf_counter() - start) * 1000
        return r.status_code, latency, None
    except requests.exceptions.Timeout:
        return 0, (time.perf_counter() - start) * 1000, "TIMEOUT"
    except requests.exceptions.ConnectionError:
        return 0, (time.perf_counter() - start) * 1000, "CONN_REFUSED"
    except Exception as e:
        return 0, (time.perf_counter() - start) * 1000, str(e)


def run_load_test(name, method, url, headers, json_data=None,
                  concurrency=10, total_requests=100):
    """Esegue un load test e stampa i risultati."""
    print(f"\n--- {name} ({total_requests} req, {concurrency} concurrent) ---")

    latencies = []
    errors = []
    status_counts = {}

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [
            pool.submit(timed_request, method, url, headers, json_data)
            for _ in range(total_requests)
        ]
        for f in as_completed(futures):
            status, latency, error = f.result()
            latencies.append(latency)
            status_counts[status] = status_counts.get(status, 0) + 1
            if error:
                errors.append(error)

    latencies.sort()
    total_time = sum(latencies) / 1000  # seconds (approximate wall time with concurrency)

    print(f"  Statuses: {status_counts}")
    print(f"  Latency p50: {latencies[len(latencies)//2]:.1f}ms")
    print(f"  Latency p95: {latencies[int(len(latencies)*0.95)]:.1f}ms")
    print(f"  Latency p99: {latencies[int(len(latencies)*0.99)]:.1f}ms")
    print(f"  Min/Max: {min(latencies):.1f}ms / {max(latencies):.1f}ms")
    print(f"  Avg: {statistics.mean(latencies):.1f}ms")
    if errors:
        unique_errors = set(errors)
        print(f"  ERRORS: {len(errors)} ({unique_errors})")

    # Verdetto
    ok_count = sum(v for k, v in status_counts.items() if 200 <= k < 400)
    err_count = sum(v for k, v in status_counts.items() if k >= 500) + len(errors)
    error_rate = err_count / total_requests * 100

    if error_rate > 5:
        print(f"  VERDICT: FAIL (error rate {error_rate:.1f}%)")
        return False
    elif latencies[int(len(latencies)*0.95)] > 5000:
        print(f"  VERDICT: SLOW (p95 > 5s)")
        return False
    else:
        print(f"  VERDICT: PASS (error rate {error_rate:.1f}%, p95 {latencies[int(len(latencies)*0.95)]:.0f}ms)")
        return True


def main():
    print("=" * 60)
    print("LOAD TEST - URBY-ETL")
    print("=" * 60)

    token = login()
    h = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    results = {}

    # Test 1: Health check (baseline)
    results["health"] = run_load_test(
        "Health Check (baseline)", "GET", "http://127.0.0.1:8000/",
        {}, concurrency=20, total_requests=200
    )

    # Test 2: Auth - GET /me (JWT decode per request)
    results["auth_me"] = run_load_test(
        "GET /auth/me (JWT decode)", "GET", f"{BASE}/auth/me",
        h, concurrency=20, total_requests=200
    )

    # Test 3: List projects (DB read)
    results["list_projects"] = run_load_test(
        "GET /projects (DB read)", "GET", f"{BASE}/projects",
        h, concurrency=20, total_requests=200
    )

    # Test 4: List connections (DB read + sharing)
    results["list_connections"] = run_load_test(
        "GET /connections (DB + sharing)", "GET", f"{BASE}/connections",
        h, concurrency=20, total_requests=200
    )

    # Test 5: List logs (DB read + group filter)
    results["list_logs"] = run_load_test(
        "GET /logs (DB + group filter)", "GET", f"{BASE}/logs?limit=50",
        h, concurrency=10, total_requests=100
    )

    # Test 6: Create + Delete project (write stress)
    print(f"\n--- Create+Delete Project (write stress, 50 cycles) ---")
    write_latencies = []
    write_errors = 0
    for i in range(50):
        # Create
        s1, l1, e1 = timed_request("POST", f"{BASE}/projects", h,
                                    {"name": f"loadtest_{i}", "project_type": "estrai"})
        if s1 == 200:
            try:
                import json as _json
                # Quick parse to get ID - use requests directly
                r = requests.post(f"{BASE}/projects", headers=h,
                                  json={"name": f"loadtest_del_{i}", "project_type": "estrai"})
                pid = r.json().get("metadata", {}).get("id")
                if pid:
                    s2, l2, e2 = timed_request("DELETE", f"{BASE}/projects/{pid}", h)
                    write_latencies.append(l1 + l2)
                else:
                    write_errors += 1
            except Exception:
                write_errors += 1
        else:
            write_errors += 1

    if write_latencies:
        write_latencies.sort()
        print(f"  Cycles: {len(write_latencies)} OK, {write_errors} errors")
        print(f"  Latency p50: {write_latencies[len(write_latencies)//2]:.1f}ms (create+delete)")
        print(f"  Latency p95: {write_latencies[int(len(write_latencies)*0.95)]:.1f}ms")
        print(f"  VERDICT: {'PASS' if write_errors < 5 else 'FAIL'}")
        results["write_stress"] = write_errors < 5

    # Test 7: High concurrency spike (50 threads)
    results["spike_50"] = run_load_test(
        "Spike 50 concurrent (stress)", "GET", f"{BASE}/projects",
        h, concurrency=50, total_requests=200
    )

    # Test 8: Login stress (auth + rate limiting)
    results["login_stress"] = run_load_test(
        "POST /login (auth stress)", "POST", f"{BASE}/auth/login",
        {"Content-Type": "application/json"},
        json_data={"username": "admin", "password": "admin123"},
        concurrency=5, total_requests=30
    )

    # Summary
    print("\n" + "=" * 60)
    print("LOAD TEST SUMMARY")
    print("=" * 60)
    all_pass = True
    for name, passed in results.items():
        status = "PASS" if passed else "FAIL"
        print(f"  [{status}] {name}")
        if not passed:
            all_pass = False

    print(f"\nOverall: {'ALL PASS' if all_pass else 'SOME FAILED'}")
    print("=" * 60)
    return all_pass


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
