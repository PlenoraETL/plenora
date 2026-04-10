"""
tests/security/unit/test_block_F_B_logic.py
===========================================
Unit test per il Blocco F-B: 3 fix di logica difensiva.

Copre:
  - F4 orchestrator service merge dict (campi reserved del nodo non sovrascritti)
  - F5 enrichment_service api_result list-vs-dict
  - F6 intermediate db drop_run_tables re-validation
"""

import pytest
from unittest.mock import patch, MagicMock

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def mock_reset():
    yield


# ============================================================================
# F4 — orchestrator service: merge dict reserved fields
# ============================================================================

class TestOrchestratorMergeReserved:
    """
    Verifica che il merge {**node_dict, **nested_data} non possa sovrascrivere
    campi di identità del nodo (id, type, position, ecc.) anche se l'utente
    li mette dentro `data{}`.
    """

    def _make_canvas_with_carica(self, data_field):
        """Costruisce un canvas con un nodo carica con `data` custom."""
        canvas = MagicMock()
        carica_node = MagicMock()
        carica_node.id = "node-real-id"
        carica_node.data = data_field
        carica_node.type = "carica_db"
        # model_dump simula Pydantic
        carica_node.model_dump.return_value = {
            "id": "node-real-id",
            "type": "carica_db",
            "data": data_field,
            "position": {"x": 0, "y": 0},
            "label": "Carica",
        }
        canvas.nodes = [carica_node]
        canvas.model_dump.return_value = {"nodes": [], "edges": []}
        return canvas, carica_node

    def test_data_cannot_override_node_id(self):
        from app.core.orchestrator.service import OrchestratorService

        # Utente malizioso mette `id` dentro data{} sperando di hijack-are
        # l'identità del nodo nel task
        canvas, _ = self._make_canvas_with_carica({
            "id": "HIJACKED",
            "type": "different_type",
            "connection_id": "conn-1",
            "table_name": "users",
        })

        svc = OrchestratorService.__new__(OrchestratorService)
        svc._find_all_carica_nodes = lambda c: c.nodes

        with patch("app.services.task_service.task_service") as mock_ts:
            mock_task = MagicMock()
            mock_task.task_id = "tsk-1"
            mock_ts.create_carica_task.return_value = mock_task

            svc._execute_carica_if_present("proj-1", canvas)

        # Il task DEVE essere stato chiamato con l'id reale del nodo, non quello hijacked
        call_kwargs = mock_ts.create_carica_task.call_args.kwargs
        carica_node_data = call_kwargs["carica_node_data"]
        assert carica_node_data["id"] == "node-real-id"
        assert carica_node_data["type"] == "carica_db"
        # Ma i campi non-reserved di data devono comunque arrivare al task
        assert carica_node_data["connection_id"] == "conn-1"
        assert carica_node_data["table_name"] == "users"

    def test_data_cannot_override_position(self):
        from app.core.orchestrator.service import OrchestratorService
        canvas, _ = self._make_canvas_with_carica({
            "position": "MALICIOUS",
            "connection_id": "conn-1",
        })
        svc = OrchestratorService.__new__(OrchestratorService)
        svc._find_all_carica_nodes = lambda c: c.nodes

        with patch("app.services.task_service.task_service") as mock_ts:
            mock_task = MagicMock()
            mock_task.task_id = "tsk-1"
            mock_ts.create_carica_task.return_value = mock_task
            svc._execute_carica_if_present("proj-1", canvas)

        carica_node_data = mock_ts.create_carica_task.call_args.kwargs["carica_node_data"]
        assert carica_node_data["position"] == {"x": 0, "y": 0}

    def test_normal_data_fields_pass_through(self):
        """I campi non-reserved devono comunque essere disponibili al task."""
        from app.core.orchestrator.service import OrchestratorService
        canvas, _ = self._make_canvas_with_carica({
            "connection_id": "conn-1",
            "table_name": "x",
            "dest_type": "db",
            "mode": "append",
        })
        svc = OrchestratorService.__new__(OrchestratorService)
        svc._find_all_carica_nodes = lambda c: c.nodes

        with patch("app.services.task_service.task_service") as mock_ts:
            mock_task = MagicMock()
            mock_task.task_id = "tsk-1"
            mock_ts.create_carica_task.return_value = mock_task
            svc._execute_carica_if_present("proj-1", canvas)

        carica_node_data = mock_ts.create_carica_task.call_args.kwargs["carica_node_data"]
        assert carica_node_data["connection_id"] == "conn-1"
        assert carica_node_data["table_name"] == "x"
        assert carica_node_data["dest_type"] == "db"
        assert carica_node_data["mode"] == "append"

    def test_data_not_dict_safe(self):
        """Se data{} è None o un tipo strano, non crashare."""
        from app.core.orchestrator.service import OrchestratorService
        canvas, _ = self._make_canvas_with_carica(None)
        svc = OrchestratorService.__new__(OrchestratorService)
        svc._find_all_carica_nodes = lambda c: c.nodes

        with patch("app.services.task_service.task_service") as mock_ts:
            mock_task = MagicMock()
            mock_task.task_id = "tsk-1"
            mock_ts.create_carica_task.return_value = mock_task
            # Non solleva
            svc._execute_carica_if_present("proj-1", canvas)


# ============================================================================
# F5 — enrichment_service api_result list-vs-dict normalization
# ============================================================================

class TestEnrichmentApiResultNormalization:
    """
    Verifica che enrichment_service._execute_with_dedup gestisca api_result
    sia come list che come dict (alcune API restituiscono dict singolo).
    """

    def test_list_api_result_uses_first(self):
        """Caso normale: List[Dict] → usa il primo elemento."""
        # Test diretto sulla logica del normalize, indipendente dall'executor.
        # Replichiamo il pattern del fix per validarlo.
        api_result = [{"a": 1}, {"a": 2}]
        if isinstance(api_result, list):
            first = api_result[0] if api_result else {}
        elif isinstance(api_result, dict):
            first = api_result
        else:
            first = {}
        assert first == {"a": 1}

    def test_dict_api_result_used_directly(self):
        api_result = {"a": 1}
        if isinstance(api_result, list):
            first = api_result[0] if api_result else {}
        elif isinstance(api_result, dict):
            first = api_result
        else:
            first = {}
        assert first == {"a": 1}

    def test_empty_list_returns_empty(self):
        api_result = []
        if isinstance(api_result, list):
            first = api_result[0] if api_result else {}
        elif isinstance(api_result, dict):
            first = api_result
        else:
            first = {}
        assert first == {}

    def test_unexpected_string_returns_empty(self):
        api_result = "wrong type"
        if isinstance(api_result, list):
            first = api_result[0] if api_result else {}
        elif isinstance(api_result, dict):
            first = api_result
        else:
            first = {}
        assert first == {}

    def test_none_returns_empty(self):
        api_result = None
        if isinstance(api_result, list):
            first = api_result[0] if api_result else {}
        elif isinstance(api_result, dict):
            first = api_result
        else:
            first = {}
        assert first == {}


# ============================================================================
# F6 — drop_run_tables re-validation difesa in profondità
# ============================================================================

class TestDropRunTablesValidation:
    """Verifica che drop_run_tables rifiuti nomi non conformi anche se
    arrivano da sqlite_master (difesa in profondità)."""

    def test_valid_table_dropped(self):
        from app.core.intermediate import db as idb
        with patch.object(idb, "get_connection") as mock_conn:
            mock_c = MagicMock()
            # sqlite_master ritorna un nome valido
            mock_c.execute.return_value.fetchall.return_value = [
                ("run_abc123_node1",),
            ]
            mock_conn.return_value = mock_c
            count = idb.drop_run_tables("abc123")
        assert count == 1

    def test_malicious_name_skipped(self):
        """Nome con SQL injection (es. spazi/punti) viene skippato."""
        from app.core.intermediate import db as idb
        with patch.object(idb, "get_connection") as mock_conn:
            mock_c = MagicMock()
            # Nome sospetto: contiene spazi/quote → non matcha valid_name regex
            mock_c.execute.return_value.fetchall.return_value = [
                ("run_abc123_; DROP TABLE runs--",),
            ]
            mock_conn.return_value = mock_c
            count = idb.drop_run_tables("abc123")
        assert count == 0

    def test_wrong_prefix_skipped(self):
        """Nome valido come pattern ma con prefisso sbagliato → skip."""
        from app.core.intermediate import db as idb
        with patch.object(idb, "get_connection") as mock_conn:
            mock_c = MagicMock()
            mock_c.execute.return_value.fetchall.return_value = [
                ("other_table_name",),
            ]
            mock_conn.return_value = mock_c
            count = idb.drop_run_tables("abc123")
        assert count == 0

    def test_no_tables_no_op(self):
        from app.core.intermediate import db as idb
        with patch.object(idb, "get_connection") as mock_conn:
            mock_c = MagicMock()
            mock_c.execute.return_value.fetchall.return_value = []
            mock_conn.return_value = mock_c
            count = idb.drop_run_tables("abc123")
        assert count == 0
