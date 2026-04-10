"""
E-06 — Credenziali errate → errore gestito
===========================================
Verifica che connessioni con password sbagliata vengano gestite
correttamente su tutti i DB supportati.

Comportamento atteso (reale del backend):
  - POST /connections       → 200 (la connessione viene salvata comunque)
  - POST /connections/{id}/test → 200 con body {"success": false, "message": "..."}
  - Il messaggio deve essere non vuoto e leggibile (non un stack trace raw)
  - Il server non crasha

Nota: il backend NON lancia HTTPException 400 — restituisce sempre
un ConnectionTestResult con success=False e un messaggio d'errore.
Questo è il comportamento corretto: la connessione può essere salvata
anche se non funziona, e il test esplicito dice se funziona o no.

Prerequisiti:
  - Docker PostgreSQL, SQL Server, MySQL, Oracle attivi
  - Backend FastAPI attivo su localhost:8000
"""

import pytest

pytestmark = [pytest.mark.estrai]

# ─── Connessioni con password sbagliata ───────────────────────────────────────

WRONG_CREDENTIALS = [
    pytest.param(
        {
            "name": "[AUTO] E-06 PG wrong pwd",
            "type": "postgres",
            "host": "localhost", "port": 5432,
            "database": "dataflow_test",
            "username": "dataflow",
            "password": "PASSWORD_SBAGLIATA",
        },
        id="postgres",
        marks=pytest.mark.postgres,
    ),
    pytest.param(
        {
            "name": "[AUTO] E-06 MSSQL wrong pwd",
            "type": "mssql",
            "host": "localhost", "port": 1433,
            "database": "master",
            "username": "sa",
            "password": "PASSWORD_SBAGLIATA",
        },
        id="mssql",
        marks=pytest.mark.mssql,
    ),
    pytest.param(
        {
            "name": "[AUTO] E-06 MySQL wrong pwd",
            "type": "mysql",
            "host": "localhost", "port": 3306,
            "database": "dataflow_test",
            "username": "dataflow",
            "password": "PASSWORD_SBAGLIATA",
        },
        id="mysql",
        marks=pytest.mark.mysql,
    ),
    pytest.param(
        {
            "name": "[AUTO] E-06 Oracle wrong pwd",
            "type": "oracle",
            "host": "localhost", "port": 1521,
            "database": "FREEPDB1",
            "username": "dataflow",
            "password": "PASSWORD_SBAGLIATA",
        },
        id="oracle",
        marks=pytest.mark.oracle,
    ),
]


class TestE06:

    @pytest.mark.parametrize("conn_data", WRONG_CREDENTIALS)
    def test_credenziali_errate_gestite(self, client, conn_data):
        """
        E-06: connessione con password sbagliata → test fallisce con messaggio chiaro.

        Verifica:
          1. Il salvataggio della connessione va a buon fine (200)
          2. Il test esplicito restituisce success=False
          3. Il messaggio d'errore è presente e non è uno stack trace raw
          4. Il server non crasha (niente 500)
        """
        # Registra connessione con credenziali sbagliate
        res = client.post("/connections", json=conn_data)
        assert res.status_code == 200, (
            f"Salvataggio connessione fallito inaspettatamente: {res.text}"
        )
        conn_id = (res.json().get("id") or res.json().get("metadata", {}).get("id"))
        assert conn_id, f"id connessione mancante: {res.json()}"

        try:
            # Testa la connessione → deve fallire con messaggio chiaro
            res_test = client.post(f"/connections/{conn_id}/test")
            assert res_test.status_code == 200, (
                f"Endpoint /test ha restituito {res_test.status_code}: {res_test.text}"
            )

            data = res_test.json()

            # success deve essere False
            assert data.get("success") is False, (
                f"Atteso success=False con password sbagliata, ricevuto: {data}"
            )

            # Il messaggio deve esistere e non essere vuoto
            message = data.get("message", "")
            assert message, "Messaggio d'errore vuoto"
            assert len(message) > 5, f"Messaggio troppo corto: '{message}'"

            # Non deve essere uno stack trace raw Python
            assert "Traceback" not in message, (
                f"Messaggio contiene stack trace raw: {message[:200]}"
            )
            assert "File \"" not in message, (
                f"Messaggio contiene path Python: {message[:200]}"
            )

        finally:
            # Cleanup: elimina la connessione
            client.delete(f"/connections/{conn_id}")


# ─── Edge cases ───────────────────────────────────────────────────────────────

PARTIAL_CREDENTIALS = [
    pytest.param(
        {
            "name": "[AUTO] E-06 PG no password",
            "type": "postgres",
            "host": "localhost", "port": 5432,
            "database": "dataflow_test",
            "username": "dataflow",
            "password": "",
        },
        id="postgres_no_pwd",
        marks=pytest.mark.postgres,
    ),
    pytest.param(
        {
            "name": "[AUTO] E-06 PG db inesistente",
            "type": "postgres",
            "host": "localhost", "port": 5432,
            "database": "db_che_non_esiste_xyz",
            "username": "dataflow",
            "password": "dataflow_test_2026",
        },
        id="postgres_db_inesistente",
        marks=pytest.mark.postgres,
    ),
    pytest.param(
        {
            "name": "[AUTO] E-06 PG host inesistente",
            "type": "postgres",
            "host": "host.inesistente.xyz", "port": 5432,
            "database": "dataflow_test",
            "username": "dataflow",
            "password": "dataflow_test_2026",
        },
        id="postgres_host_inesistente",
        marks=pytest.mark.postgres,
    ),
]


class TestE06Edge:

    @pytest.mark.parametrize("conn_data", PARTIAL_CREDENTIALS)
    def test_credenziali_parziali_o_db_errato(self, client, conn_data):
        """
        E-06 edge: password vuota, DB inesistente, host irraggiungibile.
        In tutti i casi: salvataggio OK, test → success=False, nessun crash.
        """
        res = client.post("/connections", json=conn_data)
        assert res.status_code == 200, f"Salvataggio fallito: {res.text}"
        conn_id = (res.json().get("id") or res.json().get("metadata", {}).get("id"))
        assert conn_id

        try:
            res_test = client.post(f"/connections/{conn_id}/test")
            assert res_test.status_code == 200, (
                f"Endpoint /test ha crashato: {res_test.status_code} {res_test.text}"
            )
            data = res_test.json()
            assert data.get("success") is False, (
                f"Atteso success=False, ricevuto: {data}"
            )
            assert data.get("message"), "Messaggio d'errore vuoto"
            assert "Traceback" not in data.get("message", ""), "Stack trace esposto"
        finally:
            client.delete(f"/connections/{conn_id}")
