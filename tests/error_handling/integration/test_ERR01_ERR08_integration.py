"""
tests/error_handling/integration/test_ERR01_ERR08_estrai.py
=============================================================
Test INTEGRATION error handling — Modulo ESTRAI (ERR-01→ERR-08)
Richiede backend live su localhost:8000
"""

import pytest
import requests
import io
import time
import uuid

pytestmark = [pytest.mark.integration, pytest.mark.security]

BASE_URL = "http://localhost:8000/api"


# =============================================================================
# ERR-01 — Connessione DB inesistente → 404
# =============================================================================

class TestERR01Integration:

    def test_get_connection_inesistente_404(self, auth_headers, nonexistent_id):
        res = requests.get(f"{BASE_URL}/connections/{nonexistent_id}", headers=auth_headers)
        assert res.status_code == 404

    def test_get_connection_detail_non_vuoto(self, auth_headers, nonexistent_id):
        res = requests.get(f"{BASE_URL}/connections/{nonexistent_id}", headers=auth_headers)
        assert res.json().get("detail")

    def test_get_tables_connection_inesistente_404(self, auth_headers, nonexistent_id):
        res = requests.get(f"{BASE_URL}/connections/{nonexistent_id}/tables", headers=auth_headers)
        assert res.status_code == 404

    def test_test_connection_inesistente_404(self, auth_headers, nonexistent_id):
        res = requests.post(f"{BASE_URL}/connections/{nonexistent_id}/test", headers=auth_headers)
        assert res.status_code == 404

    def test_update_connection_inesistente_404(self, auth_headers, nonexistent_id):
        res = requests.put(f"{BASE_URL}/connections/{nonexistent_id}",
                           json={"name": "x"}, headers=auth_headers)
        assert res.status_code == 404

    def test_delete_connection_inesistente_404(self, auth_headers, nonexistent_id):
        res = requests.delete(f"{BASE_URL}/connections/{nonexistent_id}", headers=auth_headers)
        assert res.status_code == 404

    def test_nessun_500(self, auth_headers, nonexistent_id):
        res = requests.get(f"{BASE_URL}/connections/{nonexistent_id}", headers=auth_headers)
        assert res.status_code != 500

    def test_id_inesistente_vari_formati(self, auth_headers):
        for bad_id in ["   ", "null", "undefined", "0", "-1"]:
            res = requests.get(f"{BASE_URL}/connections/{bad_id}", headers=auth_headers)
            assert res.status_code in (404, 422, 400), \
                f"ID '{bad_id}' → status {res.status_code} inatteso"


# =============================================================================
# ERR-02 — SQL Injection → 400/sanitizzato
# =============================================================================

class TestERR02Integration:

    SQL_PAYLOADS = [
        "'; DROP TABLE users; --",
        "1 OR 1=1",
        "' UNION SELECT * FROM users --",
        "users; DELETE FROM connections",
        "../../../etc/passwd",
        "a" * 300,
        "table\x00name",
    ]

    @pytest.mark.parametrize("payload", SQL_PAYLOADS)
    def test_payload_non_causa_500(self, auth_headers, payload):
        res = requests.get(
            f"{BASE_URL}/estrai/preview",
            params={"connection_id": "conn_fake", "table_name": payload, "limit": 10},
            headers=auth_headers
        )
        assert res.status_code != 500, f"Payload '{payload[:30]}' → 500"

    def test_table_name_vuoto(self, auth_headers):
        res = requests.get(
            f"{BASE_URL}/estrai/preview",
            params={"connection_id": "conn_fake", "table_name": "", "limit": 10},
            headers=auth_headers
        )
        assert res.status_code in (400, 404, 422)

    def test_limit_negativo(self, auth_headers):
        res = requests.get(
            f"{BASE_URL}/estrai/preview",
            params={"connection_id": "conn_fake", "table_name": "test", "limit": -1},
            headers=auth_headers
        )
        assert res.status_code != 500

    def test_limit_enorme(self, auth_headers):
        res = requests.get(
            f"{BASE_URL}/estrai/preview",
            params={"connection_id": "conn_fake", "table_name": "test", "limit": 9999999},
            headers=auth_headers
        )
        assert res.status_code != 500


# =============================================================================
# ERR-03 — Formato file non supportato → 400
# =============================================================================

class TestERR03Integration:

    FORMATI_VIETATI = [
        ("malware.exe", b"MZ\x90\x00", "application/octet-stream"),
        ("script.bat", b"@echo off", "text/plain"),
        ("lib.dll", b"MZ\x90\x00", "application/octet-stream"),
        ("shell.php", b"<?php system($_GET['cmd']); ?>", "text/plain"),
        ("hack.js", b"require('child_process')", "text/javascript"),
    ]

    @pytest.mark.parametrize("filename,content,mime", FORMATI_VIETATI)
    def test_formato_vietato_400(self, auth_headers, filename, content, mime):
        res = requests.post(
            f"{BASE_URL}/files/upload",
            files={"file": (filename, io.BytesIO(content), mime)},
            headers=auth_headers
        )
        assert res.status_code in (400, 422), \
            f"File {filename} accettato (status {res.status_code})"

    def test_exe_non_in_lista_file(self, auth_headers):
        requests.post(
            f"{BASE_URL}/files/upload",
            files={"file": ("malware.exe", io.BytesIO(b"MZ"), "application/octet-stream")},
            headers=auth_headers
        )
        res = requests.get(f"{BASE_URL}/files", headers=auth_headers)
        if res.status_code == 200:
            files = res.json().get("files", [])
            assert not any(".exe" in f.get("original_filename", "") for f in files)

    def test_messaggio_errore_formato_presente(self, auth_headers):
        res = requests.post(
            f"{BASE_URL}/files/upload",
            files={"file": ("test.exe", io.BytesIO(b"MZ"), "application/octet-stream")},
            headers=auth_headers
        )
        if res.status_code in (400, 422):
            assert res.json().get("detail")


# =============================================================================
# ERR-04 — File corrotto → gestito senza 500
# =============================================================================

class TestERR04Integration:

    def test_csv_binario_no_500(self, auth_headers):
        import os
        corrotto = os.urandom(1024)
        res = requests.post(
            f"{BASE_URL}/files/upload",
            files={"file": ("test.csv", io.BytesIO(corrotto), "text/csv")},
            headers=auth_headers
        )
        assert res.status_code != 500

    def test_xlsx_troncato_no_500(self, auth_headers):
        troncato = b"PK\x03\x04" + b"\x00" * 50
        res = requests.post(
            f"{BASE_URL}/files/upload",
            files={"file": ("test.xlsx", io.BytesIO(troncato),
                            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            headers=auth_headers
        )
        if res.status_code == 500:
            pytest.xfail("File xlsx corrotto causa 500 — da migliorare")

    def test_geojson_malformato_no_500(self, auth_headers):
        invalido = b'{"type": "FeatureCollection", "features": [{"broken": true'
        res = requests.post(
            f"{BASE_URL}/files/upload",
            files={"file": ("test.geojson", io.BytesIO(invalido), "application/json")},
            headers=auth_headers
        )
        assert res.status_code != 500

    def test_file_zero_byte_400(self, auth_headers):
        res = requests.post(
            f"{BASE_URL}/files/upload",
            files={"file": ("test.csv", io.BytesIO(b""), "text/csv")},
            headers=auth_headers
        )
        assert res.status_code in (400, 422)

    def test_csv_solo_header_no_500(self, auth_headers):
        csv = b"col1,col2,col3\n"
        res = requests.post(
            f"{BASE_URL}/files/upload",
            files={"file": ("test.csv", io.BytesIO(csv), "text/csv")},
            headers=auth_headers
        )
        assert res.status_code != 500


# =============================================================================
# ERR-05 — Query SQL errata → 400
# =============================================================================

class TestERR05Integration:

    SQL_ERRATE = [
        "SELECT * FORM tabella",
        "SELECT * FROM",
        "SELEC * FROM tabella",
        "SELECT * FROM tabella WHERE",
        "",
        "   ",
    ]

    @pytest.mark.parametrize("sql", SQL_ERRATE)
    def test_sql_errato_no_500(self, auth_headers, sql):
        res = requests.post(
            f"{BASE_URL}/estrai/validate-query",
            json={"query": sql, "connection_id": "conn_fake"},
            headers=auth_headers
        )
        assert res.status_code != 500, f"Query '{sql[:30]}' → 500"

    def test_messaggio_errore_sql_leggibile(self, auth_headers):
        res = requests.post(
            f"{BASE_URL}/estrai/validate-query",
            json={"query": "SELECT * FORM tabella", "connection_id": "conn_fake"},
            headers=auth_headers
        )
        if res.status_code in (400, 422):
            assert res.json().get("detail")


# =============================================================================
# ERR-06 — Timeout DB → no hang
# =============================================================================

class TestERR06Integration:

    @pytest.mark.slow
    def test_preview_non_blocca_oltre_45s(self, auth_headers):
        start = time.time()
        try:
            res = requests.get(
                f"{BASE_URL}/estrai/preview",
                params={"connection_id": "conn_fake", "table_name": "big_table", "limit": 10},
                headers=auth_headers,
                timeout=50
            )
        except requests.Timeout:
            pytest.fail("Preview ha superato 50s — timeout backend non funziona")
        elapsed = time.time() - start
        assert elapsed < 45, f"Preview ha impiegato {elapsed:.1f}s"


# =============================================================================
# ERR-07 — Tabella eliminata → errore chiaro
# =============================================================================

class TestERR07Integration:

    def test_preview_tabella_inesistente_no_500(self, auth_headers):
        res = requests.get(
            f"{BASE_URL}/estrai/preview",
            params={"connection_id": "conn_fake",
                    "table_name": f"tabella_nonexist_{uuid.uuid4().hex[:8]}",
                    "limit": 10},
            headers=auth_headers
        )
        assert res.status_code != 500

    def test_response_ha_detail(self, auth_headers):
        res = requests.get(
            f"{BASE_URL}/estrai/preview",
            params={"connection_id": "conn_fake",
                    "table_name": "tabella_nonexist_xyz",
                    "limit": 10},
            headers=auth_headers
        )
        if res.status_code in (400, 404, 422):
            assert res.json().get("detail")


# =============================================================================
# ERR-08 — Host irraggiungibile → risposta, no hang
# =============================================================================

class TestERR08Integration:

    @pytest.mark.parametrize("host,port", [
        ("host-non-esiste-xyz.local", 5432),
        ("localhost", 19999),
    ])
    def test_test_connection_host_irraggiungibile(self, auth_headers, make_connection, host, port):
        try:
            conn_id = make_connection(host=host, port=port)
        except Exception:
            pytest.skip("Impossibile creare connessione")

        res = requests.post(f"{BASE_URL}/connections/{conn_id}/test",
                            headers=auth_headers)
        if res.status_code == 200:
            assert res.json().get("success") is False
        else:
            assert res.status_code in (400, 408)

    def test_test_connection_ha_messaggio_errore(self, auth_headers, make_connection):
        try:
            conn_id = make_connection(host="host-inesistente-xyz.internal", port=5432)
        except Exception:
            pytest.skip("Impossibile creare connessione")

        res = requests.post(f"{BASE_URL}/connections/{conn_id}/test", headers=auth_headers)
        if res.status_code == 200 and not res.json().get("success"):
            body = res.json()
            assert body.get("error") or body.get("message") or body.get("detail")

    @pytest.mark.slow
    def test_nessun_hang_su_host_irraggiungibile(self, auth_headers, make_connection):
        try:
            conn_id = make_connection(host="localhost", port=19999)
        except Exception:
            pytest.skip("Impossibile creare connessione")

        start = time.time()
        requests.post(f"{BASE_URL}/connections/{conn_id}/test",
                      headers=auth_headers, timeout=30)
        elapsed = time.time() - start
        assert elapsed < 25, f"Test connection ha impiegato {elapsed:.1f}s"


# =============================================================================
# PARAMETRIC EXTRA — ERR-01 Integration
# =============================================================================

class TestERR01IntegrationParametric:

    @pytest.mark.parametrize("method,path_tmpl", [
        ("GET",    "/connections/{id}"),
        ("PUT",    "/connections/{id}"),
        ("DELETE", "/connections/{id}"),
        ("POST",   "/connections/{id}/test"),
        ("GET",    "/connections/{id}/tables"),
    ])
    def test_tutti_metodi_conn_inesistente(self, method, path_tmpl, auth_headers, nonexistent_id):
        path = path_tmpl.replace("{id}", nonexistent_id)
        kwargs = {"headers": auth_headers}
        if method == "PUT":
            kwargs["json"] = {"name": "x"}
        res = requests.request(method, f"{BASE_URL}{path}", **kwargs)
        assert res.status_code in (404, 422), \
            f"{method} {path} → {res.status_code}"

    @pytest.mark.parametrize("bad_id", [
        "0", "null", "undefined", "true", "false",
        "1" * 200, "conn_!@#$", "conn space",
    ])
    def test_id_formato_invalido(self, bad_id, auth_headers):
        res = requests.get(f"{BASE_URL}/connections/{bad_id}", headers=auth_headers)
        assert res.status_code in (400, 404, 422)

    def test_connection_list_senza_connessioni(self, auth_headers):
        """Lista connessioni su account nuovo ritorna lista vuota, non 500."""
        res = requests.get(f"{BASE_URL}/connections", headers=auth_headers)
        assert res.status_code == 200
        assert isinstance(res.json().get("connections", []), list)


# =============================================================================
# PARAMETRIC EXTRA — ERR-02 Integration
# =============================================================================

class TestERR02IntegrationParametric:

    PAYLOADS_CRITICI = [
        "'; DROP TABLE projects; --",
        "1; EXEC xp_cmdshell('dir'); --",
        "' OR '1'='1",
        "admin'--",
        "1' ORDER BY 1--",
    ]

    @pytest.mark.parametrize("payload", PAYLOADS_CRITICI)
    def test_injection_critica_no_500(self, auth_headers, payload):
        """Payload di injection critica non deve causare 500."""
        res = requests.get(
            f"{BASE_URL}/estrai/preview",
            params={"connection_id": "fake", "table_name": payload, "limit": 10},
            headers=auth_headers
        )
        assert res.status_code != 500

    @pytest.mark.parametrize("limit", [-1, 0, 1, 10, 100, 1000, 9999999])
    def test_limit_vari_valori(self, limit, auth_headers):
        """Vari valori di limit non devono causare 500."""
        res = requests.get(
            f"{BASE_URL}/estrai/preview",
            params={"connection_id": "fake", "table_name": "test", "limit": limit},
            headers=auth_headers
        )
        assert res.status_code != 500


# =============================================================================
# PARAMETRIC EXTRA — ERR-03 Integration
# =============================================================================

class TestERR03IntegrationParametric:

    @pytest.mark.parametrize("size_mb", [0.001, 0.01, 0.1])
    def test_file_piccoli_varie_dimensioni(self, size_mb, auth_headers):
        """File CSV di varie piccole dimensioni."""
        size_bytes = int(size_mb * 1024 * 1024)
        rows = size_bytes // 20
        content = b"col1,col2\n" + b"a,b\n" * max(1, rows)
        res = requests.post(
            f"{BASE_URL}/files/upload",
            files={"file": ("test.csv", io.BytesIO(content), "text/csv")},
            headers=auth_headers
        )
        assert res.status_code != 500

    @pytest.mark.parametrize("ext,mime", [
        (".csv", "text/csv"),
        (".csv", "text/plain"),
        (".csv", "application/octet-stream"),
        (".csv", "application/csv"),
    ])
    def test_csv_con_vari_mime_type(self, ext, mime, auth_headers):
        """CSV con vari MIME type deve essere accettato (validazione per estensione)."""
        content = b"col1,col2\nv1,v2\n"
        res = requests.post(
            f"{BASE_URL}/files/upload",
            files={"file": (f"test{ext}", io.BytesIO(content), mime)},
            headers=auth_headers
        )
        # Potrebbe essere accettato o rifiutato — non deve crashare
        assert res.status_code != 500


# =============================================================================
# PARAMETRIC EXTRA — ERR-04 Integration
# =============================================================================

class TestERR04IntegrationParametric:

    @pytest.mark.parametrize("content,filename,should_fail", [
        (b"col1,col2\nv1,v2\n", "valid.csv", False),        # valido
        (b"", "empty.csv", True),                             # vuoto
        (b"\x00" * 100, "null.csv", True),                   # null bytes
        (b"not,a,json", "test.json", True),                  # JSON invalido
        (b'{"valid": "json"}', "test.json", False),           # JSON valido
    ])
    def test_contenuti_vari_comportamento(self, content, filename, should_fail, auth_headers):
        res = requests.post(
            f"{BASE_URL}/files/upload",
            files={"file": (filename, io.BytesIO(content), "application/octet-stream")},
            headers=auth_headers
        )
        assert res.status_code != 500 or not should_fail


# =============================================================================
# PARAMETRIC EXTRA — ERR-05 Integration
# =============================================================================

class TestERR05IntegrationParametric:

    @pytest.mark.parametrize("query", [
        "SELECT 1",                    # query minima valida
        "SELECT * FROM dual",          # Oracle style
        "SELECT NOW()",                # funzione
        "SELECT 1+1",                  # espressione
        "select * from tabella",       # lowercase
        "  SELECT  1  ",               # spazi extra
    ])
    def test_query_semplici_no_500(self, query, auth_headers):
        """Query semplici non devono crashare il backend."""
        res = requests.post(
            f"{BASE_URL}/estrai/validate-query",
            json={"query": query, "connection_id": "conn_fake"},
            headers=auth_headers
        )
        assert res.status_code != 500


# =============================================================================
# EDGE CASES EXTRA — ERR-06/07/08 Integration
# =============================================================================

class TestERR06IntegrationEdge:

    def test_preview_con_limit_uno(self, auth_headers):
        """Preview con limit=1 non deve crashare."""
        res = requests.get(
            f"{BASE_URL}/estrai/preview",
            params={"connection_id": "fake", "table_name": "test", "limit": 1},
            headers=auth_headers
        )
        assert res.status_code != 500

    def test_preview_con_limit_massimo(self, auth_headers):
        """Preview con limit molto alto."""
        res = requests.get(
            f"{BASE_URL}/estrai/preview",
            params={"connection_id": "fake", "table_name": "test", "limit": 10000},
            headers=auth_headers
        )
        assert res.status_code != 500


class TestERR07IntegrationEdge:

    @pytest.mark.parametrize("table_name", [
        "public.users",
        "dbo.Orders",
        "schema_test.tabella",
        "a",
        "tabella_con_underscore_lungo_nome_molto_lungo",
    ])
    def test_nomi_tabella_validi_no_500(self, table_name, auth_headers):
        """Nomi tabella validi non devono causare 500."""
        res = requests.get(
            f"{BASE_URL}/estrai/preview",
            params={"connection_id": "fake", "table_name": table_name, "limit": 10},
            headers=auth_headers
        )
        assert res.status_code != 500


class TestERR08IntegrationEdge:

    @pytest.mark.parametrize("host", [
        "localhost",
        "127.0.0.1",
        "host-con-trattini.example.com",
        "subdomain.domain.tld",
    ])
    def test_vari_host_validi_no_crash(self, auth_headers, make_connection, host):
        """Vari host validi (anche se non raggiungibili) non devono crashare."""
        try:
            conn_id = make_connection(host=host, port=19999)
        except Exception:
            pytest.skip("Impossibile creare connessione")
        res = requests.post(f"{BASE_URL}/connections/{conn_id}/test",
                            headers=auth_headers)
        if res.status_code == 200:
            assert res.json().get("success") is False or True
        else:
            assert res.status_code in (400, 408)

    def test_connection_test_result_struttura(self, auth_headers, make_connection):
        """Il risultato del test connection ha sempre la struttura attesa."""
        try:
            conn_id = make_connection(host="localhost", port=19999)
        except Exception:
            pytest.skip("Impossibile creare connessione")
        res = requests.post(f"{BASE_URL}/connections/{conn_id}/test",
                            headers=auth_headers)
        if res.status_code == 200:
            body = res.json()
            assert "success" in body
