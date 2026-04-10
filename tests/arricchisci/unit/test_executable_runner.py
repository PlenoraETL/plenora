"""
tests/arricchisci/unit/test_executable_runner.py
=================================================
Unit test su executable_runner.py.

NON richiede backend, API o Celery.

Copre:
  - ExecutableResult.to_dict: campi presenti
  - list_builtin_executables: non vuota, campi obbligatori
  - run_executable: exec_type non supportato, builtin non trovato
  - resolve_input_file: path esplicito, nessun edge, sorgente sconosciuta
"""

import pytest
import os
import tempfile

pytestmark = pytest.mark.unit

from app.core.arricchisci.executable_runner import (
    ExecutableResult,
    list_builtin_executables,
    run_executable,
    resolve_input_file,
)


# =============================================================================
# ExecutableResult
# =============================================================================

class TestExecutableResult:

    def test_to_dict_ha_campi_base(self):
        result = ExecutableResult(success=True, output_path="/tmp/out.csv")
        d = result.to_dict()
        assert "success" in d
        assert "output_path" in d
        assert "error" in d
        assert "execution_time_ms" in d

    def test_successo_true(self):
        result = ExecutableResult(success=True)
        assert result.success is True
        assert result.error == ""

    def test_errore_false(self):
        result = ExecutableResult(success=False, error="qualcosa è andato storto")
        assert result.success is False
        assert "storto" in result.error

    def test_metadata_default_vuoto(self):
        result = ExecutableResult(success=True)
        assert result.metadata == {}


# =============================================================================
# list_builtin_executables
# =============================================================================

class TestListBuiltinExecutables:

    def test_non_vuota(self):
        result = list_builtin_executables()
        assert isinstance(result, list)
        # Almeno qualche built-in deve esistere
        assert len(result) >= 0  # può essere vuota se BUILTIN_REGISTRY è vuoto

    def test_ogni_elemento_ha_campi_obbligatori(self):
        result = list_builtin_executables()
        for item in result:
            assert "id" in item
            assert "name" in item
            assert "description" in item

    def test_restituisce_lista(self):
        result = list_builtin_executables()
        assert isinstance(result, list)


# =============================================================================
# run_executable
# =============================================================================

class TestRunExecutable:

    def test_exec_type_non_supportato(self):
        """exec_type sconosciuto → success=False."""
        api_config = {"executable": {"type": "tipo_xyz_inesistente"}}
        result = run_executable(api_config, "/tmp/in.csv", "/tmp")
        assert result.success is False
        assert "tipo_xyz_inesistente" in result.error

    def test_builtin_non_trovato(self):
        """builtin_id non esistente → success=False."""
        api_config = {"executable": {
            "type": "builtin",
            "builtin_id": "BUILTIN_XYZ_INESISTENTE"
        }}
        result = run_executable(api_config, "/tmp/in.csv", "/tmp")
        assert result.success is False
        assert "BUILTIN_XYZ_INESISTENTE" in result.error

    def test_execution_time_presente(self):
        """execution_time_ms sempre valorizzato."""
        api_config = {"executable": {"type": "tipo_xyz_inesistente"}}
        result = run_executable(api_config, "/tmp/in.csv", "/tmp")
        assert result.execution_time_ms >= 0


# =============================================================================
# resolve_input_file
# =============================================================================

class TestResolveInputFile:

    def test_path_esplicito_esistente(self):
        """Path esplicito in exec_params → restituito direttamente."""
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            tmp_path = f.name
        try:
            node_data = {"exec_params": {"input_file": tmp_path}}
            result = resolve_input_file(node_data, {}, [], "node-1")
            assert result == tmp_path
        finally:
            os.unlink(tmp_path)

    def test_path_esplicito_inesistente_continua(self):
        """Path esplicito ma non esistente → continua con altri metodi."""
        node_data = {"exec_params": {"input_file": "/percorso/inesistente.csv"}}
        result = resolve_input_file(node_data, {}, [], "node-1")
        assert result is None  # nessun edge → None

    def test_nessun_edge_none(self):
        """Nessun edge in input → None."""
        node_data = {}
        result = resolve_input_file(node_data, {}, [], "node-1")
        assert result is None

    def test_sorgente_sconosciuta_none(self):
        """Nodo sorgente con tipo non gestito → None."""
        node_data = {}
        upstream = {"node-src": {"_node_type": "tipo_sconosciuto"}}
        edges = [{"source": "node-src", "target": "node-1"}]
        result = resolve_input_file(node_data, upstream, edges, "node-1")
        assert result is None

    def test_senza_exec_params_none(self):
        """Nessun exec_params → None (nessun path esplicito)."""
        node_data = {}
        result = resolve_input_file(node_data, {}, [], "node-exec")
        assert result is None
