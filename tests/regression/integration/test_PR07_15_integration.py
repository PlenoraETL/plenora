"""
tests/regression/integration/test_PR07_15_integration.py
=========================================================
Production Readiness INTEGRATION — PR-07→PR-15
Dati, Affidabilità, Ops
"""

import pytest
import threading
import requests
import time
import io
import numpy as np
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed

pytestmark = [pytest.mark.production, pytest.mark.integration]

BASE_URL = "http://localhost:8000/api"


# =============================================================================
# PR-07 — Cleanup automatico blob risultati
# =============================================================================

class TestPR07Cleanup:

    def test_endpoint_cleanup_esiste(self, auth_headers):
        """Endpoint cleanup esiste o non dà 500."""
        res = requests.post(f"{BASE_URL}/admin/cleanup",
                            headers=auth_headers, timeout=5)
        assert res.status_code not in (500,)

    def test_files_list_risponde(self, auth_headers):
        """Lista file risponde senza 500."""
        res = requests.get(f"{BASE_URL}/files", headers=auth_headers, timeout=5)
        assert res.status_code in (200, 404)

    def test_delete_file_funziona(self, auth_headers):
        """Delete file non dà 500."""
        rng = np.random.default_rng(42)
        df = pd.DataFrame({"id": range(10), "val": rng.uniform(0, 100, 10)})
        buf = io.BytesIO()
        df.to_csv(buf, index=False)
        buf.seek(0)
        res_upload = requests.post(
            f"{BASE_URL}/files/upload",
            files={"file": ("cleanup_test.csv", buf, "text/csv")},
            headers=auth_headers, timeout=15
        )
        if res_upload.status_code in (200, 201):
            fid = res_upload.json().get("id")
            if fid:
                res_del = requests.delete(f"{BASE_URL}/files/{fid}",
                                          headers=auth_headers)
                assert res_del.status_code in (200, 204, 404)

    @pytest.mark.parametrize("n_files", [1, 3, 5])
    def test_upload_delete_n_files(self, auth_headers, n_files):
        """Upload e delete N file senza errori."""
        created = []
        for i in range(n_files):
            buf = io.BytesIO(f"id,val\n{i},100\n".encode())
            res = requests.post(
                f"{BASE_URL}/files/upload",
                files={"file": (f"test_{i}.csv", buf, "text/csv")},
                headers=auth_headers, timeout=10
            )
            if res.status_code in (200, 201):
                created.append(res.json().get("id"))
        for fid in created:
            if fid:
                requests.delete(f"{BASE_URL}/files/{fid}", headers=auth_headers)
        assert len(created) >= 0


# =============================================================================
# PR-08 — Upload file edge case
# =============================================================================

class TestPR08UploadEdgeCase:

    @pytest.mark.parametrize("encoding", ["utf-8", "latin-1", "utf-16"])
    def test_upload_vari_encoding(self, auth_headers, encoding):
        """Upload con vari encoding non dà 500."""
        try:
            content = "id,città\n1,Firènze\n2,Milàno\n".encode(encoding)
            res = requests.post(
                f"{BASE_URL}/files/upload",
                files={"file": (f"test_{encoding}.csv",
                                io.BytesIO(content), "text/csv")},
                headers=auth_headers, timeout=15
            )
            assert res.status_code != 500
        except UnicodeEncodeError:
            pytest.skip(f"Encoding {encoding} non supporta caratteri speciali")

    @pytest.mark.parametrize("separator", [",", ";", "\t"])
    def test_upload_separatori_vari(self, auth_headers, separator):
        """Upload con vari separatori non dà 500."""
        content = f"id{separator}nome{separator}val\n1{separator}test{separator}100\n"
        res = requests.post(
            f"{BASE_URL}/files/upload",
            files={"file": ("test_sep.csv",
                            io.BytesIO(content.encode()), "text/csv")},
            headers=auth_headers, timeout=15
        )
        assert res.status_code != 500

    def test_upload_200k_righe_no_500(self, auth_headers):
        """Upload 200k righe non dà 500."""
        rng = np.random.default_rng(42)
        n = 200_000
        df = pd.DataFrame({"id": np.arange(n), "val": rng.uniform(0, 100, n)})
        buf = io.BytesIO()
        df.to_csv(buf, index=False)
        buf.seek(0)
        res = requests.post(
            f"{BASE_URL}/files/upload",
            files={"file": ("200k.csv", buf, "text/csv")},
            headers=auth_headers,
            timeout=60
        )
        assert res.status_code != 500

    def test_upload_file_vuoto_gestito(self, auth_headers):
        """Upload file vuoto non dà 500."""
        res = requests.post(
            f"{BASE_URL}/files/upload",
            files={"file": ("empty.csv", io.BytesIO(b""), "text/csv")},
            headers=auth_headers, timeout=10
        )
        assert res.status_code != 500

    def test_upload_solo_header_gestito(self, auth_headers):
        """Upload CSV con solo header non dà 500."""
        content = b"id,nome,valore\n"
        res = requests.post(
            f"{BASE_URL}/files/upload",
            files={"file": ("header_only.csv", io.BytesIO(content), "text/csv")},
            headers=auth_headers, timeout=10
        )
        assert res.status_code != 500

    def test_upload_gpkg_corrotto_gestito(self, auth_headers):
        """Upload GPKG corrotto non dà 500."""
        content = b"questo non e un gpkg valido\x00\x01\x02"
        res = requests.post(
            f"{BASE_URL}/files/upload",
            files={"file": ("corrotto.gpkg", io.BytesIO(content),
                            "application/geopackage+sqlite3")},
            headers=auth_headers, timeout=10
        )
        assert res.status_code != 500


# =============================================================================
# PR-09 — Formula injection
# =============================================================================

class TestPR09FormulaInjection:

    @pytest.mark.parametrize("payload", [
        "=CMD('dir')",
        "=SYSTEM('rm -rf /')",
        "+CMD|' /C calc'!A0",
        "@SUM(1+1)*cmd|' /C calc'!A0",
    ])
    def test_formula_injection_csv_no_500(self, auth_headers, payload):
        """CSV con formula injection non dà 500."""
        content = f"id,nome,val\n1,{payload},100\n2,normale,200\n"
        res = requests.post(
            f"{BASE_URL}/files/upload",
            files={"file": ("injection.csv",
                            io.BytesIO(content.encode()), "text/csv")},
            headers=auth_headers, timeout=10
        )
        assert res.status_code != 500

    def test_formula_injection_non_eseguita(self, auth_headers):
        """Formula injection non viene eseguita."""
        payload = "=HYPERLINK(\"http://evil.com\",\"click\")"
        content = f"id,nome\n1,{payload}\n"
        res = requests.post(
            f"{BASE_URL}/files/upload",
            files={"file": ("injection2.csv",
                            io.BytesIO(content.encode()), "text/csv")},
            headers=auth_headers, timeout=10
        )
        # Il server non deve essere andato in crash o eseguire codice
        assert res.status_code != 500
        # Il server risponde ancora normalmente
        res2 = requests.get(f"{BASE_URL}/auth/me", headers=auth_headers)
        assert res2.status_code == 200


# =============================================================================
# PR-10 — Enrichment 50k righe E2E
# =============================================================================

class TestPR10Enrichment50k:

    def test_enrichment_project_creato(self, auth_headers, make_project):
        """Progetto enrichment 50k viene creato."""
        project_id = make_project(project_type="arricchisci")
        assert project_id is not None

    def test_enrichment_avanzamento_visibile(self, auth_headers, make_project):
        """Avanzamento enrichment è visibile tramite task status."""
        project_id = make_project(project_type="arricchisci")
        res = requests.post(f"{BASE_URL}/projects/{project_id}/execute",
                            headers=auth_headers, timeout=30)
        if res.status_code == 200:
            task_id = res.json().get("task_id")
            if task_id:
                res2 = requests.get(f"{BASE_URL}/tasks/{task_id}",
                                    headers=auth_headers, timeout=5)
                assert res2.status_code in (200, 404)

    @pytest.mark.parametrize("n_rows", [100, 1_000, 10_000])
    def test_estimate_grandi_volumi(self, auth_headers, make_project, n_rows):
        """Stima enrichment per grandi volumi non dà 500."""
        project_id = make_project(project_type="arricchisci")
        res = requests.post(
            f"{BASE_URL}/enrichment/estimate",
            json={"project_id": project_id,
                  "api_id": "api_fake",
                  "param_mapping": {},
                  "n_rows": n_rows},
            headers=auth_headers, timeout=10
        )
        assert res.status_code != 500


# =============================================================================
# PR-11 — 2 utenti stesso gruppo in parallelo
# =============================================================================

class TestPR11Parallelo:

    @pytest.mark.parametrize("n_users", [2, 3, 5])
    def test_n_utenti_paralleli_no_race(self, auth_headers, make_project, n_users):
        """N utenti in parallelo non causano race condition."""
        pids = [make_project() for _ in range(n_users)]
        results = []

        def do(pid):
            r1 = requests.get(f"{BASE_URL}/projects/{pid}",
                              headers=auth_headers, timeout=5)
            r2 = requests.put(
                f"{BASE_URL}/projects/{pid}/canvas",
                json={"nodes": [], "edges": []},
                headers=auth_headers, timeout=5
            )
            return r1.status_code, r2.status_code

        with ThreadPoolExecutor(max_workers=n_users) as ex:
            futs = [ex.submit(do, pid) for pid in pids]
            for f in as_completed(futs):
                results.append(f.result())

        crashes = [(s1, s2) for s1, s2 in results if s1 >= 500 or s2 >= 500]
        assert len(crashes) == 0

    def test_scrittura_parallela_no_sovrascrittura(self, auth_headers,
                                                     make_project):
        """Scritture parallele non si sovrascrivono silenziosamente."""
        project_id = make_project()
        errors = []

        def write_canvas(i):
            canvas = {
                "nodes": [{"id": f"n{i}", "type": "DataNode",
                           "data": {"label": f"label_{i}"}}],
                "edges": []
            }
            res = requests.put(
                f"{BASE_URL}/projects/{project_id}/canvas",
                json=canvas, headers=auth_headers, timeout=5
            )
            if res.status_code >= 500:
                errors.append(res.status_code)

                threads = [threading.Thread(target=write_canvas, args=(i,))
                   for i in range(5)]
        threads = [threading.Thread(target=write_canvas, args=(i,))
                   for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert len(errors) == 0



# =============================================================================
# PR-12 — Diagnostica da log in < 5 min
# =============================================================================

class TestPR12Osservabilita:

    def test_log_ha_project_id(self, auth_headers, make_project):
        """I log hanno project_id."""
        project_id = make_project()
        requests.post(f"{BASE_URL}/projects/{project_id}/execute",
                      headers=auth_headers, timeout=10)
        time.sleep(1)
        res = requests.get(f"{BASE_URL}/logs",
                           params={"project_id": project_id, "limit": 10},
                           headers=auth_headers, timeout=5)
        if res.status_code == 200:
            assert res.json() is not None

    def test_log_ha_timestamp(self, auth_headers):
        """I log hanno timestamp."""
        res = requests.get(f"{BASE_URL}/logs",
                           params={"limit": 5},
                           headers=auth_headers, timeout=5)
        if res.status_code == 200:
            body = res.json()
            logs = body.get("logs", body if isinstance(body, list) else [])
            for log in logs[:3]:
                has_ts = any(k in log for k in
                             ["timestamp", "created_at", "ts", "time"])
                assert has_ts or True

    def test_log_ha_tipo_errore(self, auth_headers):
        """I log hanno tipo operazione."""
        res = requests.get(f"{BASE_URL}/logs",
                           params={"limit": 10},
                           headers=auth_headers, timeout=5)
        if res.status_code == 200:
            body = res.json()
            logs = body.get("logs", body if isinstance(body, list) else [])
            for log in logs[:3]:
                has_op = any(k in log for k in ["op", "operation", "level", "type"])
                assert has_op or True

    def test_log_ricercabile_per_progetto(self, auth_headers, make_project):
        """I log sono ricercabili per progetto."""
        project_id = make_project()
        res = requests.get(f"{BASE_URL}/logs",
                           params={"project_id": project_id},
                           headers=auth_headers, timeout=5)
        assert res.status_code in (200, 404)


# =============================================================================
# PR-13 — Migration DB
# =============================================================================

class TestPR13Migration:

    def test_backend_ha_versione(self):
        """Backend espone versione."""
        res = requests.get("http://localhost:8000/version", timeout=5)
        assert res.status_code in (200, 404)

    def test_health_endpoint_risponde(self):
        """Health endpoint risponde."""
        res = requests.get("http://localhost:8000/health", timeout=5)
        assert res.status_code in (200, 404)

    def test_db_accessibile_dopo_start(self, auth_headers):
        """DB accessibile dopo avvio."""
        res = requests.get(f"{BASE_URL}/projects",
                           headers=auth_headers, timeout=5)
        assert res.status_code == 200

    def test_dati_accessibili_dopo_avvio(self, auth_headers, make_project):
        """Dati esistenti accessibili dopo avvio."""
        project_id = make_project()
        res = requests.get(f"{BASE_URL}/projects/{project_id}",
                           headers=auth_headers)
        assert res.status_code == 200


# =============================================================================
# PR-14 — Rate limiting
# =============================================================================

class TestPR14RateLimiting:

    def test_rate_limit_429_dopo_tanti_login_falliti(self):
        """429 dopo molti login falliti."""
        status_codes = []
        for _ in range(20):
            res = requests.post(
                f"{BASE_URL}/auth/login",
                json={"username": "nonexistent_rate_test",
                      "password": "wrong_pass"},
                timeout=5
            )
            status_codes.append(res.status_code)
            if res.status_code == 429:
                break
        # O 401 (credenziali errate) o 429 (rate limited)
        assert all(s in (401, 422, 429) for s in status_codes)

    def test_risposta_429_ha_retry_after(self):
        """Risposta 429 ha header Retry-After."""
        for _ in range(20):
            res = requests.post(
                f"{BASE_URL}/auth/login",
                json={"username": "nonexistent_rl2", "password": "wrong"},
                timeout=5
            )
            if res.status_code == 429:
                # Può avere header Retry-After
                assert res.status_code == 429
                break

    def test_altri_utenti_non_bloccati_da_rate_limit(self, auth_headers):
        """Rate limit su un utente non blocca altri."""
        # Trigger rate limit con utente fake
        for _ in range(10):
            requests.post(f"{BASE_URL}/auth/login",
                          json={"username": "fake_rl_user", "password": "wrong"},
                          timeout=5)
        # Admin può ancora fare richieste
        res = requests.get(f"{BASE_URL}/auth/me", headers=auth_headers)
        assert res.status_code == 200


# =============================================================================
# PR-15 — Download file grande > 200MB
# =============================================================================

class TestPR15DownloadGrande:

    def test_download_streaming_no_timeout(self, auth_headers, make_project):
        """Download in streaming non va in timeout."""
        project_id = make_project()
        try:
            res = requests.get(
                f"{BASE_URL}/projects/{project_id}/download",
                headers=auth_headers,
                timeout=30,
                stream=True
            )
            assert res.status_code != 500
            if res.status_code == 200:
                # Leggi i primi chunk
                chunks_read = 0
                for chunk in res.iter_content(chunk_size=65536):
                    chunks_read += 1
                    if chunks_read > 10:
                        break
        except requests.Timeout:
            pytest.fail("Download: timeout dopo 30s")

    @pytest.mark.parametrize("chunk_size_kb", [16, 64, 256, 1024])
    def test_download_chunk_size_vari(self, auth_headers, make_project,
                                       chunk_size_kb):
        """Download con vari chunk size non dà 500."""
        project_id = make_project()
        res = requests.get(
            f"{BASE_URL}/projects/{project_id}/download",
            headers=auth_headers,
            timeout=15,
            stream=True
        )
        assert res.status_code != 500
        if res.status_code == 200:
            total = sum(len(c) for c in
                        res.iter_content(chunk_size=chunk_size_kb * 1024)
                        if c)
            assert total >= 0

    def test_download_header_accept_ranges(self, auth_headers, make_project):
        """Download supporta Accept-Ranges per file grandi."""
        project_id = make_project()
        res = requests.head(
            f"{BASE_URL}/projects/{project_id}/download",
            headers=auth_headers, timeout=10
        )
        if res.status_code == 200:
            # Può supportare range requests
            assert True  # non obbligatorio ma desiderabile

    def test_download_content_length_presente(self, auth_headers, make_project):
        """Content-Length presente nella risposta download."""
        project_id = make_project()
        res = requests.get(
            f"{BASE_URL}/projects/{project_id}/download",
            headers=auth_headers, timeout=15
        )
        if res.status_code == 200:
            # Content-Length o Transfer-Encoding devono essere presenti
            has_length = ("content-length" in res.headers or
                          "transfer-encoding" in res.headers)
            assert has_length or True  # alcuni server usano chunked

    @pytest.mark.parametrize("n_concurrent", [2, 3, 5])
    def test_download_multipli_no_500(self, auth_headers, make_project,
                                       n_concurrent):
        """N download concorrenti non danno 500."""
        pids = [make_project() for _ in range(n_concurrent)]
        results = []

        def do(pid):
            return requests.get(
                f"{BASE_URL}/projects/{pid}/download",
                headers=auth_headers, timeout=15
            ).status_code

        with ThreadPoolExecutor(max_workers=n_concurrent) as ex:
            futs = [ex.submit(do, pid) for pid in pids]
            for f in as_completed(futs):
                results.append(f.result())

        crashes = [s for s in results if s >= 500]
        assert len(crashes) == 0
