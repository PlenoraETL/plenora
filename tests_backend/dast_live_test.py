"""
DAST (Dynamic Application Security Testing) — test su server live.

Requisiti:
  - Server su http://127.0.0.1:8000
  - Utente admin/admin123 esistente
  - Redis running (per Celery)

Esegui: python dast_live_test.py
"""

import requests
import json
import time
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE = "http://127.0.0.1:8000/api"
RESULTS = {"pass": 0, "fail": 0, "errors": []}


def login(username="admin", password="admin123"):
    r = requests.post(f"{BASE}/auth/login", json={"username": username, "password": password})
    if r.status_code != 200:
        print(f"FATAL: login failed: {r.status_code} {r.text[:200]}")
        sys.exit(1)
    return r.json()["access_token"]


def auth_header(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def test(name, condition, detail=""):
    if condition:
        RESULTS["pass"] += 1
        print(f"  [PASS] {name}")
    else:
        RESULTS["fail"] += 1
        RESULTS["errors"].append(f"{name}: {detail}")
        print(f"  [FAIL] {name} — {detail}")


def run_dast():
    token = login()
    h = auth_header(token)

    print("\n=== 1. AUTH BYPASS TESTS ===")

    # No token → 401
    r = requests.get(f"{BASE}/projects")
    test("No token → 401", r.status_code == 401, f"got {r.status_code}")

    # Invalid token → 401
    r = requests.get(f"{BASE}/projects", headers={"Authorization": "Bearer invalid.token.here"})
    test("Invalid token → 401", r.status_code == 401, f"got {r.status_code}")

    # Expired-like token (malformed) → 401
    r = requests.get(f"{BASE}/projects", headers={"Authorization": "Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.invalid"})
    test("Malformed JWT → 401", r.status_code == 401, f"got {r.status_code}")

    print("\n=== 2. SQL INJECTION TESTS (live) ===")

    # SQL injection in project name (POST)
    r = requests.post(f"{BASE}/projects", headers=h, json={
        "name": "'; DROP TABLE projects; --",
        "project_type": "estrai"
    })
    test("SQLi in project name → not 500", r.status_code != 500, f"got {r.status_code}")
    # Cleanup: delete if created
    if r.status_code == 200:
        pid = r.json().get("metadata", {}).get("id")
        if pid:
            requests.delete(f"{BASE}/projects/{pid}", headers=h)

    # SQL injection in search (logs)
    r = requests.get(f"{BASE}/logs?search=' OR '1'='1", headers=h)
    test("SQLi in log search → not 500", r.status_code != 500, f"got {r.status_code}")

    # SQL injection in search with wildcards
    r = requests.get(f"{BASE}/logs?search=%25password%25", headers=h)
    test("LIKE wildcard in search → not 500", r.status_code != 500, f"got {r.status_code}")

    print("\n=== 3. XSS/INJECTION IN RESPONSES ===")

    # Create project with XSS payload
    r = requests.post(f"{BASE}/projects", headers=h, json={
        "name": "<script>alert('xss')</script>",
        "project_type": "estrai"
    })
    if r.status_code == 200:
        data = r.json()
        name_back = data.get("metadata", {}).get("name", "")
        test("XSS in name: returned as-is (API, not HTML)",
             "<script>" in name_back,  # API returns raw — OK for JSON API
             f"name={name_back}")
        pid = data.get("metadata", {}).get("id")
        if pid:
            requests.delete(f"{BASE}/projects/{pid}", headers=h)

    print("\n=== 4. PATH TRAVERSAL TESTS ===")

    # Path traversal in project download
    r = requests.get(f"{BASE}/projects/nonexistent/download?file=../../etc/passwd", headers=h)
    test("Path traversal in download → not 200", r.status_code != 200, f"got {r.status_code}")

    # Path traversal in project ID
    r = requests.get(f"{BASE}/projects/../../../etc/passwd", headers=h)
    test("Path traversal in project_id → not 200", r.status_code != 200, f"got {r.status_code}")

    print("\n=== 5. RATE LIMITING / DoS TESTS ===")

    # Rapid fire: 50 requests in 2 seconds
    start = time.time()
    statuses = []
    for _ in range(50):
        r = requests.get(f"{BASE}/projects", headers=h)
        statuses.append(r.status_code)
    elapsed = time.time() - start
    test(f"50 rapid requests in {elapsed:.1f}s → all 200",
         all(s == 200 for s in statuses),
         f"statuses: {set(statuses)}")

    # Login brute force: should get rate limited
    fail_statuses = []
    for i in range(15):
        r = requests.post(f"{BASE}/auth/login", json={"username": "admin", "password": "wrong"})
        fail_statuses.append(r.status_code)
    test("Login brute force → rate limited (429 after N attempts)",
         429 in fail_statuses,
         f"statuses: {fail_statuses}")

    print("\n=== 6. CORS TESTS ===")

    # Preflight with evil origin
    r = requests.options(f"{BASE}/projects", headers={
        "Origin": "https://evil.com",
        "Access-Control-Request-Method": "GET",
    })
    cors_origin = r.headers.get("access-control-allow-origin", "")
    test("CORS: evil origin not reflected",
         "evil.com" not in cors_origin,
         f"CORS origin: {cors_origin}")

    print("\n=== 7. INFORMATION DISCLOSURE TESTS ===")

    # /docs accessible?
    r = requests.get("http://127.0.0.1:8000/docs")
    test("/docs accessible (expected in dev)", r.status_code == 200,
         f"status: {r.status_code}")

    # /openapi.json accessible?
    r = requests.get("http://127.0.0.1:8000/openapi.json")
    test("/openapi.json accessible (expected in dev)", r.status_code == 200,
         f"status: {r.status_code}")

    # Error response doesn't leak stack trace
    r = requests.get(f"{BASE}/projects/nonexistent-id-12345", headers=h)
    if r.status_code >= 400:
        body = r.text
        test("Error response no stack trace",
             "Traceback" not in body and "File \"" not in body,
             f"body contains traceback!")

    # Health check doesn't leak system info
    r = requests.get("http://127.0.0.1:8000/")
    body = r.json()
    test("Health check minimal info",
         set(body.keys()) <= {"status", "service", "version"},
         f"keys: {list(body.keys())}")

    print("\n=== 8. INPUT VALIDATION ON LIVE SERVER ===")

    # Very long name
    r = requests.post(f"{BASE}/projects", headers=h, json={
        "name": "X" * 300,
        "project_type": "estrai"
    })
    test("Very long name → 422", r.status_code == 422, f"got {r.status_code}")

    # Invalid port in connection
    r = requests.post(f"{BASE}/connections", headers=h, json={
        "name": "test", "type": "postgres", "host": "localhost",
        "port": -1, "database": "db", "username": "u", "password": "p"
    })
    test("Negative port → 422", r.status_code == 422, f"got {r.status_code}")

    # Port > 65535
    r = requests.post(f"{BASE}/connections", headers=h, json={
        "name": "test", "type": "postgres", "host": "localhost",
        "port": 99999, "database": "db", "username": "u", "password": "p"
    })
    test("Port 99999 → 422", r.status_code == 422, f"got {r.status_code}")

    # Null byte in name
    r = requests.post(f"{BASE}/projects", headers=h, json={
        "name": "test\x00evil",
        "project_type": "estrai"
    })
    test("Null byte in name → not 500", r.status_code != 500, f"got {r.status_code}")
    if r.status_code == 200:
        pid = r.json().get("metadata", {}).get("id")
        if pid:
            requests.delete(f"{BASE}/projects/{pid}", headers=h)

    print("\n=== 9. CONCURRENT ACCESS TESTS ===")

    # 20 concurrent requests to same endpoint
    def fetch_projects():
        return requests.get(f"{BASE}/projects", headers=h).status_code

    with ThreadPoolExecutor(max_workers=20) as pool:
        futures = [pool.submit(fetch_projects) for _ in range(20)]
        concurrent_statuses = [f.result() for f in as_completed(futures)]
    test("20 concurrent GET /projects → all 200",
         all(s == 200 for s in concurrent_statuses),
         f"statuses: {set(concurrent_statuses)}")

    print("\n=== 10. HTTP METHOD TESTS ===")

    # PATCH on endpoint that doesn't support it
    r = requests.patch(f"{BASE}/projects", headers=h, json={"name": "test"})
    test("PATCH /projects → 405",
         r.status_code == 405,
         f"got {r.status_code}")

    # DELETE on list endpoint
    r = requests.delete(f"{BASE}/projects", headers=h)
    test("DELETE /projects (list) → 405",
         r.status_code == 405,
         f"got {r.status_code}")

    # ── SUMMARY ──
    print("\n" + "=" * 60)
    print(f"DAST COMPLETE: {RESULTS['pass']} passed, {RESULTS['fail']} failed")
    if RESULTS["errors"]:
        print("\nFAILURES:")
        for e in RESULTS["errors"]:
            print(f"  - {e}")
    print("=" * 60)

    return RESULTS["fail"] == 0


if __name__ == "__main__":
    success = run_dast()
    sys.exit(0 if success else 1)
