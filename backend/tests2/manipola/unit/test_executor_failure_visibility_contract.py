"""
Contract test parametrizzato per l'invariante "executor failure visibility"
del modulo MANIPOLA.

Cluster bug coperti (vedi `.claude/bug-reports/plenora-bug-etl-executors.md`):

  1. **`_applica_group_by` non swallowa piu' eccezioni**
     Pre-fix: `try/except Exception: return df` mascherava failures
     (config invalida, pandas errors) e persisteva i dati raw come output
     ufficiale del progetto. Post-fix: solleva `ValueError` su config
     utente invalida, lascia propagare eccezioni runtime.

  2. **`execute()` cattura `MemoryLimitExceededError` esplicitamente**
     Pre-fix: `except ValueError` troppo stretto, `MemoryLimitExceededError`
     sfuggiva al worker Celery senza `append_step`. Post-fix: handler
     dedicato prima di ValueError, con stage info nel messaggio.

  3. **Early-return paths emettono `append_step`**
     Pre-fix: due rami ("Nessun dato prodotto", "Nodo risultato non
     trovato") ritornavano senza tracciare l'evento. Post-fix: tutti i
     branch di failure passano per `_failure_step()` helper.

  4. **`preview()` separa user-error da bug imprevisto**
     Pre-fix: `logger.warning(... no exc_info)` mascherava i bug come
     warning. Post-fix: ValueError → warning controllato, Exception →
     error+exc_info (triage rapido in log aggregato).

Test paradigma Go-friendly: table-driven via `pytest.parametrize`, helper
`_failure_step` testato come funzione pura, integration tests usano
monkeypatch mirato invece di build-up complesso di fixture.
"""

from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

from app.core.manipola.executor import ManipolaExecutor, _failure_step
from app.core.resources.exceptions import MemoryLimitExceededError


pytestmark = [
    pytest.mark.unit,
    pytest.mark.manipola,
    pytest.mark.contract,
    pytest.mark.regression,
]


# ---------------------------------------------------------------------------
# Test helper builders (Go-style: dati piatti, no fixture pesanti)
# ---------------------------------------------------------------------------

def _result_node(
    *,
    group_by_enabled: bool = True,
    group_by_columns: list[str] | None = None,
    aggregations: list[dict] | None = None,
) -> SimpleNamespace:
    """Result node minimale per testare `_applica_group_by`."""
    return SimpleNamespace(
        id="result-1",
        type="result",
        data={
            "group_by_enabled": group_by_enabled,
            "group_by_columns": group_by_columns or [],
            "aggregations": aggregations or [],
        },
    )


def _canvas_with_result_node() -> SimpleNamespace:
    return SimpleNamespace(nodes=[_result_node()])


def _canvas_without_result_node() -> SimpleNamespace:
    return SimpleNamespace(nodes=[
        SimpleNamespace(id="src-1", type="project_source", data={}),
    ])


# ---------------------------------------------------------------------------
# _failure_step helper — funzione pura
# ---------------------------------------------------------------------------

class TestFailureStepHelper:
    """Lock dello shape del `failure step` manipola. Una sola funzione
    modulo, single-source dell'invariante "ogni branch di errore deve
    avere lo stesso shape osservabile".
    """

    def test_returns_failure_dict_with_message(self):
        result = _failure_step("boom", step_start=0.0)
        assert result == {"success": False, "message": "boom"}

    def test_emits_append_step_failure_with_module_manipola(self, monkeypatch):
        captured: list[dict] = []
        monkeypatch.setattr(
            "app.core.manipola.executor.append_step",
            lambda step: captured.append(step),
        )
        _failure_step("memoria insufficiente", step_start=0.0)
        assert len(captured) == 1
        step = captured[0]
        assert step["module"] == "manipola"
        assert step["status"] == "failure"
        assert step["rows_out"] == 0
        assert step["error"] == "memoria insufficiente"
        assert isinstance(step["duration_ms"], int)
        assert step["duration_ms"] >= 0


# ---------------------------------------------------------------------------
# _applica_group_by — no silent fallback su config invalida
# ---------------------------------------------------------------------------

class TestApplicaGroupByRaisesOnInvalidConfig:
    """Pre-fix bug P0 (`manipola/executor.py:926`): `except Exception:
    return df` swallowed errori e persisteva dati raw non aggregati come
    output ufficiale. Post-fix: solleva ValueError esplicito → execute()
    lo converte in failure result + append_step.
    """

    def _df(self) -> pd.DataFrame:
        return pd.DataFrame({"a": [1, 1, 2], "b": [10, 20, 30]})

    def test_raises_when_all_group_columns_are_missing(self):
        executor = ManipolaExecutor(project_service=object())
        node = _result_node(
            group_by_columns=["does_not_exist"],
            aggregations=[{"column": "b", "function": "sum", "alias": "tot"}],
        )
        with pytest.raises(ValueError, match="GROUP BY.*colonne configurate"):
            executor._applica_group_by(self._df(), node)

    def test_raises_when_aggregations_are_all_invalid(self):
        executor = ManipolaExecutor(project_service=object())
        node = _result_node(
            group_by_columns=["a"],
            aggregations=[
                {"column": "missing_col", "function": "sum", "alias": "x"},
                {"function": "median", "column": "b", "alias": "y"},  # funz non supportata
            ],
        )
        with pytest.raises(ValueError, match="nessuna aggregazione valida"):
            executor._applica_group_by(self._df(), node)

    def test_noop_when_group_by_not_enabled(self):
        """Failure visibility NON e' regression sui no-op intenzionali."""
        executor = ManipolaExecutor(project_service=object())
        node = _result_node(group_by_enabled=False)
        df = self._df()
        result = executor._applica_group_by(df, node)
        assert result is df

    def test_noop_when_neither_cols_nor_aggs_configured(self):
        executor = ManipolaExecutor(project_service=object())
        node = _result_node(group_by_columns=[], aggregations=[])
        df = self._df()
        result = executor._applica_group_by(df, node)
        assert result is df


# ---------------------------------------------------------------------------
# execute() routes ogni failure path attraverso _failure_step
# ---------------------------------------------------------------------------

class _CapturingAppendStep:
    """Cattura tutti gli `append_step` emessi durante un execute()."""

    def __init__(self) -> None:
        self.steps: list[dict] = []

    def __call__(self, step: dict) -> None:
        self.steps.append(step)


@pytest.fixture
def capture_steps(monkeypatch):
    cap = _CapturingAppendStep()
    monkeypatch.setattr("app.core.manipola.executor.append_step", cap)
    return cap


def _make_executor_with_stubbed_loaders(monkeypatch) -> ManipolaExecutor:
    """ManipolaExecutor con loaders e security context stubbati a no-op
    success. Isola il fix-target (failure handling di execute) dalle
    dipendenze di setup.
    """
    executor = ManipolaExecutor(project_service=object())
    monkeypatch.setattr(
        executor, "_init_security_context", lambda *_args, **_kw: "grp-1",
    )
    monkeypatch.setattr(
        executor, "_carica_sorgenti_progetto", lambda *_a, **_kw: None,
    )
    monkeypatch.setattr(
        executor, "_carica_sorgenti_file", lambda *_a, **_kw: None,
    )
    monkeypatch.setattr(
        executor, "_carica_sorgenti_datasource", lambda *_a, **_kw: None,
    )
    monkeypatch.setattr(
        executor, "_carica_sorgenti_statiche", lambda *_a, **_kw: None,
    )
    return executor


class TestExecuteFailureVisibility:
    """Ogni failure path in `execute()` deve passare per `_failure_step`
    (oppure equivalente shape) e ritornare `{success: False, message: ...}`.
    """

    @pytest.mark.parametrize("raised,expected_substring", [
        (ValueError("trasformazione invalida"), "trasformazione invalida"),
        (
            MemoryLimitExceededError(
                observed_bytes=200_000_000,
                limit_bytes=100_000_000,
                path_basename="upstream.parquet",
                stage="multi_join",
            ),
            "Memoria insufficiente",
        ),
    ])
    def test_execute_catches_transform_errors_with_append_step(
        self, monkeypatch, capture_steps, raised, expected_substring,
    ):
        executor = _make_executor_with_stubbed_loaders(monkeypatch)

        def _raise(*_args, **_kwargs):
            raise raised

        monkeypatch.setattr(executor, "_applica_trasformazioni", _raise)

        result = executor.execute("proj-1", _canvas_with_result_node())

        assert result == {"success": False, "message": pytest.approx(
            result["message"], abs=0
        )}
        # Caso ValueError: message identico. Caso MemoryLimit: prefisso noto.
        assert expected_substring in result["message"]
        assert len(capture_steps.steps) == 1
        step = capture_steps.steps[0]
        assert step["module"] == "manipola"
        assert step["status"] == "failure"
        assert step["error"] == result["message"]

    def test_execute_emits_append_step_when_transformations_return_none(
        self, monkeypatch, capture_steps,
    ):
        """Pre-fix bug P1: `if current_df is None: return {...}` saltava
        l'append_step. Post-fix: passa per `_failure_step`.
        """
        executor = _make_executor_with_stubbed_loaders(monkeypatch)
        monkeypatch.setattr(
            executor, "_applica_trasformazioni",
            lambda *_a, **_kw: None,
        )

        result = executor.execute("proj-1", _canvas_with_result_node())

        assert result["success"] is False
        assert "Nessun dato prodotto" in result["message"]
        assert capture_steps.steps[0]["status"] == "failure"

    def test_execute_emits_append_step_when_result_node_missing(
        self, monkeypatch, capture_steps,
    ):
        """Pre-fix bug P1: l'altro early-return (no result node) saltava
        l'append_step. Post-fix: `_failure_step` qui pure.
        """
        executor = _make_executor_with_stubbed_loaders(monkeypatch)
        monkeypatch.setattr(
            executor, "_applica_trasformazioni",
            lambda *_a, **_kw: pd.DataFrame({"a": [1]}),
        )

        result = executor.execute("proj-1", _canvas_without_result_node())

        assert result["success"] is False
        assert "Nodo risultato non trovato" in result["message"]
        assert capture_steps.steps[0]["status"] == "failure"

    def test_execute_catches_group_by_value_error_with_append_step(
        self, monkeypatch, capture_steps,
    ):
        """Integrazione end-to-end del fix bug 1: `_applica_group_by`
        raise → `execute()` cattura e ritorna failure. Pre-fix il raw
        df veniva persistito.
        """
        executor = _make_executor_with_stubbed_loaders(monkeypatch)
        monkeypatch.setattr(
            executor, "_applica_trasformazioni",
            lambda *_a, **_kw: pd.DataFrame({"a": [1]}),
        )

        def _raise_group_by(*_a, **_kw):
            raise ValueError("GROUP BY: colonne non trovate")

        monkeypatch.setattr(executor, "_applica_group_by", _raise_group_by)

        result = executor.execute("proj-1", _canvas_with_result_node())

        assert result["success"] is False
        assert "GROUP BY" in result["message"]
        assert capture_steps.steps[0]["status"] == "failure"


# ---------------------------------------------------------------------------
# preview() severity gating
# ---------------------------------------------------------------------------

class TestPreviewLogSeverity:
    """Pre-fix bug P1: `preview()` usava `logger.warning` (senza exc_info,
    senza project_id) per QUALSIASI eccezione. Post-fix: ValueError ->
    warning controllato, Exception -> error + exc_info per triage rapido.
    """

    def _run_preview_with_failure(self, monkeypatch, exception_to_raise):
        executor = _make_executor_with_stubbed_loaders(monkeypatch)
        monkeypatch.setattr(
            executor, "_carica_sorgenti_progetto",
            lambda *_a, **_kw: None,
        )

        def _raise(*_a, **_kw):
            raise exception_to_raise

        monkeypatch.setattr(executor, "_applica_trasformazioni", _raise)

        log_calls: list[tuple[str, dict]] = []

        def _spy_factory(level: str):
            def _spy(msg, *args, **kwargs):
                log_calls.append((level, {"msg": msg, "kwargs": kwargs}))
            return _spy

        # Spy direttamente sui metodi (StructuredLogger propagate=False)
        monkeypatch.setattr(
            "app.core.manipola.executor.logger.warning",
            _spy_factory("warning"),
        )
        monkeypatch.setattr(
            "app.core.manipola.executor.logger.error",
            _spy_factory("error"),
        )

        # Simula stato minimo per arrivare al try/except della preview.
        # NB: `preview` chiama loaders -> trasformazioni: il monkeypatch
        # del loader fa sì che dataframes resti vuoto, ma dobbiamo
        # iniettare almeno un df cosi' il path arriva a `_applica_trasformazioni`.
        # Workaround: monkeypatch loader per iniettare in dataframes.
        def _inject_df(_canvas, dataframes, *_args, **_kwargs):
            dataframes["src-1"] = pd.DataFrame({"a": [1]})

        monkeypatch.setattr(
            executor, "_carica_sorgenti_progetto", _inject_df,
        )

        canvas = SimpleNamespace(nodes=[
            SimpleNamespace(id="src-1", type="project_source", data={}),
            _result_node(group_by_enabled=False),
        ], edges=[])
        result = executor.preview("proj-1", canvas, limit=10)
        return result, log_calls

    def test_value_error_uses_warning_level(self, monkeypatch):
        result, log_calls = self._run_preview_with_failure(
            monkeypatch, ValueError("config utente errata"),
        )
        assert result["success"] is False
        warnings = [c for c in log_calls if c[0] == "warning"]
        errors = [c for c in log_calls if c[0] == "error"]
        assert warnings, "ValueError deve loggare a WARNING"
        # No exc_info richiesto sul user-error
        assert not errors, (
            f"Nessun log.error atteso su ValueError, trovati: {errors!r}"
        )

    def test_unexpected_exception_uses_error_level_with_exc_info(
        self, monkeypatch,
    ):
        result, log_calls = self._run_preview_with_failure(
            monkeypatch, KeyError("internal bug"),
        )
        assert result["success"] is False
        errors = [c for c in log_calls if c[0] == "error"]
        assert errors, "Eccezione imprevista deve loggare a ERROR"
        assert errors[0][1]["kwargs"].get("exc_info") is True, (
            "exc_info=True richiesto per il triage del bug imprevisto"
        )
