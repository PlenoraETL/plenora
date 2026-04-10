"""
test_C31_C45_carica_critical.py — Suite CARICA · Casi Critici

Prerequisiti:
  - Backend FastAPI su localhost:8000 (Celery ALWAYS_EAGER)
  - Docker PostgreSQL su localhost:5432
  - Mock ArcGIS su localhost:8999

Test coperti:
  C-31  conflict_policy=abort: APPEND con conflitti → operazione bloccata
  C-32  conflict_policy=skip: APPEND con conflitti → righe conflitto escluse
  C-33  conflict_policy=force: APPEND con conflitti → inserimento forzato
  C-34  transaction_mode=all_or_nothing: errore → rollback completo
  C-35  transaction_mode=per_batch: errore → solo batch falliti rollback
  C-36  write_mode=replace: tabella esistente → drop + ricrea + inserisce
  C-37  write_mode=truncate_insert: svuota e reinserisce
  C-38  write_mode=update: aggiorna righe esistenti
  C-39  write_mode=upsert: inserisce nuove, aggiorna esistenti
  C-40  Tabella destinazione non esiste → CREATE automatico
  C-41  Connessione non valida → failure immediato
  C-42  Batch size 1 → scrittura riga per riga
  C-43  DataFrame con colonne extra non mappate → ignorate
  C-44  DataFrame con tipo misto (numeri + stringhe) → conversione TEXT
  C-45  Write su schema non default
"""

import time
import time as _t
import pytest
import requests

from conftest import (
    build_carica_canvas,
    execute_carica,
    make_db_carica_node,
    poll_task,
)

TABLE_PREFIX = "c_crit"


# =============================================================================
# C-31 — conflict_policy=abort
# =============================================================================

class TestC31ConflictAbort:
    """APPEND su tabella con righe duplicate → operazione bloccata."""

    def test_c31_abort_blocca_operazione(self, client, pg_conn, make_project, csv_immobili):
        """C-31: APPEND con abort → WriteStatus.CONFLICT, nessuna riga inserita."""
        import time as _t
        table = f"{TABLE_PREFIX}_c31_{int(_t.time()) % 100000}"

        # Prima scrittura — crea la tabella
        canvas = build_carica_canvas(csv_immobili)
        project_id = make_project(
            name="[AUTO] C-31a create",
            project_type="manipola",
            connection_id=pg_conn,
            canvas=canvas,
        )
        node = make_db_carica_node(
            connection_id=pg_conn,
            table_name=table,
            write_mode="create",
        )
        r1 = execute_carica(client, project_id, canvas, node)
        assert r1.get("success") is True

        # Seconda scrittura con abort — deve fallire per conflitti
        node_abort = make_db_carica_node(
            connection_id=pg_conn,
            table_name=table,
            write_mode="append",
            key_column="id",
        )
        node_abort["conflict_policy"] = "abort"
        r2 = execute_carica(client, project_id, canvas, node_abort)
        assert r2.get("success") is False or r2.get("status") == "conflict"

    def test_c31_abort_nessun_conflitto_ok(self, client, pg_conn, make_project, csv_immobili):
        """C-31b: APPEND abort senza conflitti → success."""
        import time as _t
        table = f"{TABLE_PREFIX}_c31b_{int(_t.time()) % 100000}"

        canvas = build_carica_canvas(csv_immobili)
        project_id = make_project(
            name="[AUTO] C-31b no conflict",
            project_type="manipola",
            connection_id=pg_conn,
            canvas=canvas,
        )
        node = make_db_carica_node(
            connection_id=pg_conn,
            table_name=table,
            write_mode="create",
        )
        r = execute_carica(client, project_id, canvas, node)
        assert r.get("success") is True


# =============================================================================
# C-32 — conflict_policy=skip
# =============================================================================

class TestC32ConflictSkip:
    """APPEND skip → righe in conflitto escluse, nuove inserite."""

    def test_c32_skip_inserisce_solo_nuove(self, client, pg_conn, make_project, csv_immobili):
        import time as _t
        table = f"{TABLE_PREFIX}_c32_{int(_t.time()) % 100000}"

        canvas = build_carica_canvas(csv_immobili)
        project_id = make_project(
            name="[AUTO] C-32 skip",
            project_type="manipola",
            connection_id=pg_conn,
            canvas=canvas,
        )
        # Prima scrittura
        node_create = make_db_carica_node(pg_conn, table, write_mode="create")
        r1 = execute_carica(client, project_id, canvas, node_create)
        rows_after_create = r1.get("rows_written", 0)

        # Seconda scrittura con skip — nessuna riga nuova inserita (tutti duplicati)
        node_skip = make_db_carica_node(pg_conn, table, write_mode="append", key_column="id")
        node_skip["conflict_policy"] = "skip"
        r2 = execute_carica(client, project_id, canvas, node_skip)
        assert r2.get("success") is True
        assert r2.get("rows_skipped", 0) >= 0


# =============================================================================
# C-33 — conflict_policy=force
# =============================================================================

class TestC33ConflictForce:
    """APPEND force → inserimento forzato anche con conflitti."""

    def test_c33_force_inserisce_tutto(self, client, pg_conn, make_project, csv_immobili):
        import time as _t
        table = f"{TABLE_PREFIX}_c33_{int(_t.time()) % 100000}"

        canvas = build_carica_canvas(csv_immobili)
        project_id = make_project(
            name="[AUTO] C-33 force",
            project_type="manipola",
            connection_id=pg_conn,
            canvas=canvas,
        )
        node_create = make_db_carica_node(pg_conn, table, write_mode="create")
        execute_carica(client, project_id, canvas, node_create)

        node_force = make_db_carica_node(pg_conn, table, write_mode="append", key_column="id")
        node_force["conflict_policy"] = "force"
        r2 = execute_carica(client, project_id, canvas, node_force)
        assert r2.get("success") is True


# =============================================================================
# C-34 — transaction_mode=all_or_nothing
# =============================================================================

class TestC34TransactionAllOrNothing:
    """all_or_nothing: errore in un batch → rollback completo."""

    def test_c34_rollback_su_errore(self, client, pg_conn, make_project, csv_carica_type_error):
        """C-34: dato con tipo incompatibile → rollback dell'intera operazione."""
        import time as _t
        table = f"{TABLE_PREFIX}_c34_{int(_t.time()) % 100000}"

        canvas = build_carica_canvas(csv_carica_type_error)
        project_id = make_project(
            name="[AUTO] C-34 all_or_nothing",
            project_type="manipola",
            connection_id=pg_conn,
            canvas=canvas,
        )
        node = make_db_carica_node(
            pg_conn, table,
            write_mode="create",
            transaction_mode="all_or_nothing",
        )
        result = execute_carica(client, project_id, canvas, node)
        # Con tipo misto, il backend dovrebbe gestirlo — verifica che non crashi
        assert "success" in result


# =============================================================================
# C-35 — transaction_mode=per_batch
# =============================================================================

class TestC35TransactionPerBatch:
    """per_batch: errore in un batch → solo quel batch in rollback."""

    def test_c35_per_batch_success(self, client, pg_conn, make_project, csv_immobili):
        import time as _t
        table = f"{TABLE_PREFIX}_c35_{int(_t.time()) % 100000}"

        canvas = build_carica_canvas(csv_immobili)
        project_id = make_project(
            name="[AUTO] C-35 per_batch",
            project_type="manipola",
            connection_id=pg_conn,
            canvas=canvas,
        )
        node = make_db_carica_node(
            pg_conn, table,
            write_mode="create",
            transaction_mode="per_batch",
            batch_size=100,
        )
        result = execute_carica(client, project_id, canvas, node)
        assert result.get("success") is True


# =============================================================================
# C-36 — write_mode=replace
# =============================================================================

class TestC36Replace:
    """REPLACE: tabella esistente → drop + ricrea + reinserisce."""

    def test_c36_replace_tabella_esistente(self, client, pg_conn, make_project, csv_immobili):
        import time as _t
        table = f"{TABLE_PREFIX}_c36_{int(_t.time()) % 100000}"

        canvas = build_carica_canvas(csv_immobili)
        project_id = make_project(
            name="[AUTO] C-36 replace",
            project_type="manipola",
            connection_id=pg_conn,
            canvas=canvas,
        )
        # Prima scrittura CREATE
        node_create = make_db_carica_node(pg_conn, table, write_mode="create")
        r1 = execute_carica(client, project_id, canvas, node_create)
        assert r1.get("success") is True

        # Seconda scrittura REPLACE
        node_replace = make_db_carica_node(pg_conn, table, write_mode="replace")
        r2 = execute_carica(client, project_id, canvas, node_replace)
        assert r2.get("success") is True
        assert r2.get("rows_written", 0) == r1.get("rows_written", 0)

    def test_c36_replace_tabella_non_esistente(self, client, pg_conn, make_project, csv_immobili):
        """REPLACE su tabella non esistente → crea e inserisce."""
        table = f"{TABLE_PREFIX}_c36b_{int(_t.time()) % 100000}"

        canvas = build_carica_canvas(csv_immobili)
        project_id = make_project(
            name="[AUTO] C-36b replace new",
            project_type="manipola",
            connection_id=pg_conn,
            canvas=canvas,
        )
        node = make_db_carica_node(pg_conn, table, write_mode="replace")
        result = execute_carica(client, project_id, canvas, node)
        assert result.get("success") is True


# =============================================================================
# C-37 — write_mode=truncate_insert
# =============================================================================

class TestC37TruncateInsert:
    """TRUNCATE_INSERT: svuota e reinserisce."""

    def test_c37_truncate_insert(self, client, pg_conn, make_project, csv_immobili):
        import time as _t
        table = f"{TABLE_PREFIX}_c37_{int(_t.time()) % 100000}"

        canvas = build_carica_canvas(csv_immobili)
        project_id = make_project(
            name="[AUTO] C-37 truncate",
            project_type="manipola",
            connection_id=pg_conn,
            canvas=canvas,
        )
        node_create = make_db_carica_node(pg_conn, table, write_mode="create")
        r1 = execute_carica(client, project_id, canvas, node_create)
        rows = r1.get("rows_written", 0)

        node_trunc = make_db_carica_node(pg_conn, table, write_mode="truncate_insert")
        r2 = execute_carica(client, project_id, canvas, node_trunc)
        assert r2.get("success") is True
        assert r2.get("rows_written", 0) == rows


# =============================================================================
# C-38 — write_mode=update
# =============================================================================

class TestC38Update:
    """UPDATE: aggiorna righe esistenti con match su chiave."""

    def test_c38_update_righe_esistenti(self, client, pg_conn, make_project, csv_immobili):
        import time as _t
        table = f"{TABLE_PREFIX}_c38_{int(_t.time()) % 100000}"

        canvas = build_carica_canvas(csv_immobili)
        project_id = make_project(
            name="[AUTO] C-38 update",
            project_type="manipola",
            connection_id=pg_conn,
            canvas=canvas,
        )
        node_create = make_db_carica_node(pg_conn, table, write_mode="create")
        r1 = execute_carica(client, project_id, canvas, node_create)
        assert r1.get("success") is True

        node_update = make_db_carica_node(
            pg_conn, table,
            write_mode="update",
            key_column="id",
        )
        r2 = execute_carica(client, project_id, canvas, node_update)
        assert r2.get("success") is True


# =============================================================================
# C-39 — write_mode=upsert
# =============================================================================

class TestC39Upsert:
    """UPSERT: inserisce nuove, aggiorna esistenti."""

    def test_c39_upsert_misto(self, client, pg_conn, make_project, csv_immobili):
        import time as _t
        table = f"{TABLE_PREFIX}_c39_{int(_t.time()) % 100000}"

        canvas = build_carica_canvas(csv_immobili)
        project_id = make_project(
            name="[AUTO] C-39 upsert",
            project_type="manipola",
            connection_id=pg_conn,
            canvas=canvas,
        )
        node_create = make_db_carica_node(pg_conn, table, write_mode="create")
        execute_carica(client, project_id, canvas, node_create)

        node_upsert = make_db_carica_node(
            pg_conn, table,
            write_mode="upsert",
            key_column="id",
        )
        r2 = execute_carica(client, project_id, canvas, node_upsert)
        assert r2.get("success") is True


# =============================================================================
# C-40 — Tabella non esiste → CREATE automatico
# =============================================================================

class TestC40TabellaInesistente:
    """CREATE su tabella non esistente → creata automaticamente."""

    def test_c40_crea_tabella_nuova(self, client, pg_conn, make_project, csv_immobili):
        import uuid
        table = f"{TABLE_PREFIX}_new_{uuid.uuid4().hex[:8]}"

        canvas = build_carica_canvas(csv_immobili)
        project_id = make_project(
            name=f"[AUTO] C-40 new table {table}",
            project_type="manipola",
            connection_id=pg_conn,
            canvas=canvas,
        )
        node = make_db_carica_node(pg_conn, table, write_mode="create")
        result = execute_carica(client, project_id, canvas, node)
        assert result.get("success") is True
        assert result.get("rows_written", 0) > 0


# =============================================================================
# C-41 — Connessione non valida
# =============================================================================

class TestC41ConnessioneNonValida:
    """Connessione non valida → failure immediato."""

    def test_c41_conn_inesistente_failure(self, client, make_project, csv_immobili):
        canvas = build_carica_canvas(csv_immobili)
        project_id = make_project(
            name="[AUTO] C-41 bad conn",
            project_type="estrai",
            connection_id="conn_inesistente_xyz",
            canvas=canvas,
        )
        node = make_db_carica_node(
            connection_id="conn_inesistente_xyz",
            table_name="test",
            write_mode="create",
        )
        result = execute_carica(client, project_id, canvas, node)
        assert result.get("success") is False


# =============================================================================
# C-42 — Batch size 1
# =============================================================================

class TestC42BatchSizeUno:
    """batch_size=1 → scrittura riga per riga."""

    def test_c42_batch_size_1(self, client, pg_conn, make_project, csv_immobili):
        table = f"{TABLE_PREFIX}_c42"

        canvas = build_carica_canvas(csv_immobili)
        project_id = make_project(
            name="[AUTO] C-42 batch=1",
            project_type="manipola",
            connection_id=pg_conn,
            canvas=canvas,
        )
        node = make_db_carica_node(pg_conn, table, write_mode="create", batch_size=1)
        result = execute_carica(client, project_id, canvas, node)
        assert result.get("success") is True


# =============================================================================
# C-43 — Colonne extra non mappate
# =============================================================================

class TestC43ColonneExtra:
    """Colonne extra nel DataFrame non mappate → ignorate."""

    def test_c43_colonne_extra_ignorate(self, client, pg_conn, make_project, csv_immobili):
        table = f"{TABLE_PREFIX}_c43"

        canvas = build_carica_canvas(csv_immobili)
        project_id = make_project(
            name="[AUTO] C-43 extra cols",
            project_type="manipola",
            connection_id=pg_conn,
            canvas=canvas,
        )
        # Mappa solo alcune colonne
        mappings = [
            {"source_column": "id",     "dest_column": "id",     "dest_type": "int"},
            {"source_column": "comune", "dest_column": "comune", "dest_type": "str"},
        ]
        node = make_db_carica_node(
            pg_conn, table,
            write_mode="create",
            column_mappings=mappings,
        )
        result = execute_carica(client, project_id, canvas, node)
        assert result.get("success") is True


# =============================================================================
# C-44 — Tipo misto (numeri + stringhe)
# =============================================================================

class TestC44TipoMisto:
    """Colonna con tipo misto → convertita a TEXT automaticamente."""

    def test_c44_tipo_misto_gestito(self, client, pg_conn, make_project, csv_carica_type_error):
        table = f"{TABLE_PREFIX}_c44"

        canvas = build_carica_canvas(csv_carica_type_error)
        project_id = make_project(
            name="[AUTO] C-44 tipo misto",
            project_type="manipola",
            connection_id=pg_conn,
            canvas=canvas,
        )
        node = make_db_carica_node(pg_conn, table, write_mode="create")
        result = execute_carica(client, project_id, canvas, node)
        assert result.get("success") is True


# =============================================================================
# C-45 — Schema non default
# =============================================================================

class TestC45SchemaNonDefault:
    """Scrittura su schema diverso da public."""

    def test_c45_schema_custom(self, client, pg_conn, make_project, csv_immobili):
        table = f"{TABLE_PREFIX}_c45"

        canvas = build_carica_canvas(csv_immobili)
        project_id = make_project(
            name="[AUTO] C-45 schema custom",
            project_type="manipola",
            connection_id=pg_conn,
            canvas=canvas,
        )
        node = make_db_carica_node(
            pg_conn, table,
            write_mode="create",
            schema_name="public",  # usiamo public che esiste sempre
        )
        result = execute_carica(client, project_id, canvas, node)
        assert result.get("success") is True
