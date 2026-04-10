"""
tests/manipola/unit/test_executor_pipeline.py
==============================================
Unit test sul percorso execute() e preview() di ManipolaExecutor
e su _applica_trasformazioni.

Usa mock per i loader di sorgenti e il salvataggio — testa solo
la logica di orchestrazione della pipeline.
"""

import pytest
import pandas as pd
from unittest.mock import MagicMock, patch

pytestmark = pytest.mark.unit

from app.core.manipola.executor import ManipolaExecutor
from app.shared.models import Node, Edge, Column, Position, CanvasState


# ─── Helper ───────────────────────────────────────────────────────────────────

_POS = Position(x=0, y=0)


def make_executor():
    return ManipolaExecutor(project_service=MagicMock())


def make_node(node_id, node_type, transform_op=None, **kwargs):
    n = Node(id=node_id, type=node_type, position=_POS, columns=[])
    if transform_op:
        n.transform_op = transform_op
    for k, v in kwargs.items():
        setattr(n, k, v)
    return n


def make_edge(src, tgt, handle=None):
    e = Edge(id=f"{src}-{tgt}", source=src, target=tgt)
    if handle:
        e.targetHandle = handle
    return e


def make_canvas(nodes, edges=None):
    return CanvasState(nodes=nodes, edges=edges or [])


def make_df(n=5):
    return pd.DataFrame({
        "id":    range(n),
        "nome":  [f"item_{i}" for i in range(n)],
        "valore":[float(i*10) for i in range(n)],
    })


# =============================================================================
# execute() — branch di errore e percorso principale
# =============================================================================

class TestExecute:

    def _mock_execute(self, ex, dataframes_to_load, canvas,
                      salva_result=None, trasformazioni_result=None):
        """Helper: mocka i loader e il salvataggio."""
        def mock_file(c, d):
            d.update(dataframes_to_load)
            return None

        if salva_result is None:
            salva_result = {"success": True, "rows_count": 5, "message": "ok"}
        if trasformazioni_result is None:
            trasformazioni_result = make_df()

        with patch.object(ex, "_carica_sorgenti_progetto", return_value=None), \
             patch.object(ex, "_carica_sorgenti_file", side_effect=mock_file), \
             patch.object(ex, "_carica_sorgenti_statiche", return_value=None), \
             patch.object(ex, "_applica_trasformazioni", return_value=trasformazioni_result), \
             patch.object(ex, "_salva_risultato", return_value=salva_result):
            return ex.execute("proj-1", canvas)

    def test_execute_successo(self):
        ex = make_executor()
        result_node = make_node("result", "result")
        result_node.fileName = "output"
        result_node.format = "csv"
        canvas = make_canvas([result_node])

        df = make_df()
        result = self._mock_execute(ex, {"src": df}, canvas)
        assert result["success"] is True

    def test_execute_errore_loader(self):
        """Loader restituisce errore → propagato subito."""
        ex = make_executor()
        canvas = make_canvas([make_node("result", "result")])

        with patch.object(ex, "_carica_sorgenti_progetto",
                          return_value={"success": False, "message": "errore upstream"}), \
             patch.object(ex, "_carica_sorgenti_file", return_value=None), \
             patch.object(ex, "_carica_sorgenti_statiche", return_value=None):
            result = ex.execute("proj-1", canvas)

        assert result["success"] is False
        assert "errore upstream" in result["message"]

    def test_execute_no_result_node(self):
        """Canvas senza nodo result → success=False."""
        ex = make_executor()
        canvas = make_canvas([make_node("src", "file_source")])

        df = make_df()
        result = self._mock_execute(ex, {"src": df}, canvas)
        assert result["success"] is False
        assert "risultato" in result["message"].lower()

    def test_execute_trasformazioni_none(self):
        """_applica_trasformazioni restituisce None → success=False."""
        ex = make_executor()
        result_node = make_node("result", "result")
        canvas = make_canvas([result_node])
        df = make_df()

        with patch.object(ex, "_carica_sorgenti_progetto", return_value=None),              patch.object(ex, "_carica_sorgenti_file",
                          side_effect=lambda c, d: d.update({"src": df})),              patch.object(ex, "_carica_sorgenti_statiche", return_value=None),              patch.object(ex, "_applica_trasformazioni", return_value=None):
            result = ex.execute("proj-1", canvas)

        assert result["success"] is False
        assert "Nessun dato" in result["message"]

    def test_execute_trasformazioni_value_error(self):
        """ValueError in _applica_trasformazioni → success=False."""
        ex = make_executor()
        result_node = make_node("result", "result")
        canvas = make_canvas([result_node])

        with patch.object(ex, "_carica_sorgenti_progetto", return_value=None), \
             patch.object(ex, "_carica_sorgenti_file", return_value=None), \
             patch.object(ex, "_carica_sorgenti_statiche", return_value=None), \
             patch.object(ex, "_applica_trasformazioni",
                          side_effect=ValueError("ciclo nel canvas")):
            result = ex.execute("proj-1", canvas)

        assert result["success"] is False
        assert "ciclo" in result["message"]


# =============================================================================
# preview() — percorso principale e casi limite
# =============================================================================

class TestPreview:

    def test_preview_successo(self):
        ex = make_executor()
        result_node = make_node("result", "result")
        canvas = make_canvas([result_node])
        df = make_df(10)

        with patch.object(ex, "_carica_sorgenti_progetto", return_value=None), \
             patch.object(ex, "_carica_sorgenti_file",
                          side_effect=lambda c, d: d.update({"src": df})), \
             patch.object(ex, "_carica_sorgenti_statiche", return_value=None), \
             patch.object(ex, "_applica_trasformazioni", return_value=df):
            result = ex.preview("proj-1", canvas, limit=5)

        assert result["success"] is True
        assert result["preview_count"] == 5
        assert result["total_count"] == 10
        assert result["truncated"] is True

    def test_preview_no_dataframes(self):
        """Nessuna sorgente → success=False."""
        ex = make_executor()
        canvas = make_canvas([make_node("result", "result")])

        with patch.object(ex, "_carica_sorgenti_progetto", return_value=None), \
             patch.object(ex, "_carica_sorgenti_file", return_value=None), \
             patch.object(ex, "_carica_sorgenti_statiche", return_value=None):
            result = ex.preview("proj-1", canvas, limit=10)

        assert result["success"] is False
        assert "sorgente" in result["message"].lower()

    def test_preview_trasformazioni_none(self):
        ex = make_executor()
        canvas = make_canvas([make_node("result", "result")])
        df = make_df()

        with patch.object(ex, "_carica_sorgenti_progetto", return_value=None), \
             patch.object(ex, "_carica_sorgenti_file",
                          side_effect=lambda c, d: d.update({"src": df})), \
             patch.object(ex, "_carica_sorgenti_statiche", return_value=None), \
             patch.object(ex, "_applica_trasformazioni", return_value=None):
            result = ex.preview("proj-1", canvas, limit=10)

        assert result["success"] is False

    def test_preview_non_troncato(self):
        """total_count <= limit → truncated=False."""
        ex = make_executor()
        canvas = make_canvas([make_node("result", "result")])
        df = make_df(3)

        with patch.object(ex, "_carica_sorgenti_progetto", return_value=None), \
             patch.object(ex, "_carica_sorgenti_file",
                          side_effect=lambda c, d: d.update({"src": df})), \
             patch.object(ex, "_carica_sorgenti_statiche", return_value=None), \
             patch.object(ex, "_applica_trasformazioni", return_value=df):
            result = ex.preview("proj-1", canvas, limit=10)

        assert result["success"] is True
        assert result["truncated"] is False
        assert result["preview_count"] == 3

    def test_preview_eccezione_gestita(self):
        """Eccezione generica → success=False, no crash."""
        ex = make_executor()
        canvas = make_canvas([])

        with patch.object(ex, "_carica_sorgenti_progetto",
                          side_effect=RuntimeError("errore imprevisto")):
            result = ex.preview("proj-1", canvas, limit=10)

        assert result["success"] is False


# =============================================================================
# _applica_trasformazioni
# =============================================================================

class TestApplicaTrasformazioni:

    def test_nessun_nodo_transform_passthrough(self):
        """Canvas senza transform → _gestisci_join_default."""
        ex = make_executor()
        df = make_df()
        src = make_node("src", "file_source")
        canvas = make_canvas([src])
        dataframes = {"src": df}

        with patch.object(ex, "_gestisci_join_default", return_value=df) as mock_gj:
            result = ex._applica_trasformazioni(canvas, dataframes)
            mock_gj.assert_called_once()

    def test_singolo_transform(self):
        """Canvas con un transform → eseguito correttamente."""
        ex = make_executor()
        df_in = make_df(10)
        df_out = make_df(5)

        src = make_node("src", "file_source")
        t = make_node("t1", "transform", transform_op="distinct")
        res = make_node("result", "result")
        canvas = make_canvas([src, t, res], [
            make_edge("src", "t1"),
            make_edge("t1", "result"),
        ])
        dataframes = {"src": df_in}

        with patch.object(ex, "_esegui_trasformazione", return_value=df_out):
            result = ex._applica_trasformazioni(canvas, dataframes)

        assert result is df_out

    def test_nodo_senza_input_skippato(self):
        """Nodo transform senza sorgente in dataframes → skippato."""
        ex = make_executor()
        t = make_node("t1", "transform", transform_op="distinct")
        canvas = make_canvas([t])
        dataframes = {}  # nessun df caricato

        result = ex._applica_trasformazioni(canvas, dataframes)
        assert result is None

    def test_multi_join_chiamato(self):
        """Nodo multi_join → chiama _esegui_multi_join."""
        ex = make_executor()
        df1 = make_df(5)
        df2 = make_df(5)
        df_joined = make_df(5)

        src1 = make_node("src1", "file_source")
        src2 = make_node("src2", "file_source")
        mj = make_node("mj", "multi_join")
        mj.join_keys = ["id"]
        mj.multi_join_type = "inner"
        canvas = make_canvas([src1, src2, mj], [
            make_edge("src1", "mj"),
            make_edge("src2", "mj"),
        ])
        dataframes = {"src1": df1, "src2": df2}

        with patch.object(ex, "_esegui_multi_join", return_value=df_joined) as mock_mj:
            result = ex._applica_trasformazioni(canvas, dataframes)
            mock_mj.assert_called_once()
        assert result is df_joined

    def test_ciclo_raise_value_error(self):
        """Ciclo nel canvas → ValueError."""
        ex = make_executor()
        t1 = make_node("t1", "transform")
        t2 = make_node("t2", "transform")
        canvas = make_canvas([t1, t2], [
            make_edge("t1", "t2"),
            make_edge("t2", "t1"),
        ])
        dataframes = {"t1": make_df(), "t2": make_df()}

        with pytest.raises(ValueError, match="Ciclo"):
            ex._applica_trasformazioni(canvas, dataframes)

    def test_pipeline_due_transform_in_sequenza(self):
        """A → B → C: le trasformazioni vengono applicate in ordine."""
        ex = make_executor()
        df_a = make_df(10)
        df_b = make_df(8)
        df_c = make_df(5)

        src = make_node("src", "file_source")
        t1 = make_node("t1", "transform")
        t2 = make_node("t2", "transform")
        canvas = make_canvas([src, t1, t2], [
            make_edge("src", "t1"),
            make_edge("t1", "t2"),
        ])
        dataframes = {"src": df_a}
        call_order = []

        def mock_transform(node, inputs):
            call_order.append(node.id)
            return df_b if node.id == "t1" else df_c

        with patch.object(ex, "_esegui_trasformazione", side_effect=mock_transform):
            result = ex._applica_trasformazioni(canvas, dataframes)

        assert call_order == ["t1", "t2"]
        assert result is df_c
