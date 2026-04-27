"""Contract test RB.#14 (2026-04-27) — batch parallel merge fail-hard.

BUG.md #14:
    Worker enrichment_task.execute_enrichment_task esegue batch di nodi
    enrichment in ThreadPoolExecutor, poi mergia i loro risultati in un
    unico DataFrame `merged` per propagarlo ai nodi a valle.

    Pre-fix: il merge era avvolto da `try/except Exception` che catturava
    qualunque errore (incluso il ValueError pandas
    `Length of values (X) does not match length of index (Y)` causato da
    un nodo che esplodeva una FeatureCollection di N features extra),
    emetteva `logger.warning("Merge batch parallelo fallito ...
    Proseguo con node_results per-nodo")` e continuava.

    Risultato osservato in produzione: input 50 righe -> ramo
    `Returnomifrompoint` esplode a 56 righe -> merge solleva ValueError ->
    fallback silenzioso -> le colonne arricchite del nodo "esploso" vengono
    scartate, altri nodi a valle leggono un DataFrame mutilato -> task
    chiude `success=True` con `rows_processed=0` -> PDF generato senza
    arricchimenti. L'utente non si accorge del fallimento.

Fix RB.#14 (approach A, fail-hard):
    `_merge_batch_parallel_node_results` raise `RuntimeError` con messaggio
    diagnostico (task_id, node_id, len result_df, len common_input_df,
    cause probabili) se la cardinalita' diverge. Bubble-up al global
    handler dell'Celery task -> `close_run(success=False)`. Meglio task
    fallito visibile che success silente con dati persi.

Test scope:
    1. cardinality mismatch -> RuntimeError con node_id e cardinalita'
    2. same cardinality -> merge OK, colonne combinate propagate a tutti
       i node_results del batch (regression)
    3. anti-regression: nessun fallback silenzioso, nessun warning generico
       "Merge batch parallelo fallito" emesso
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from workers.tasks.enrichment_task import _merge_batch_parallel_node_results


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.regression]


def _make_input_df(n: int) -> pd.DataFrame:
    """DataFrame di input batch con N righe e una colonna `id`."""
    return pd.DataFrame({"id": list(range(n)), "x": [f"v{i}" for i in range(n)]})


# =============================================================================
# 1. Cardinality mismatch -> RuntimeError fail-hard
# =============================================================================


def test_merge_raises_runtime_error_on_cardinality_mismatch_explosion():
    """Caso BUG.md #14: nodo `B` esplode a 56 righe (FeatureCollection),
    nodo `A` resta a 50. Il merge non puo' procedere senza corruption ->
    RuntimeError con tutti i campi diagnostici."""
    common_input_df = _make_input_df(50)
    node_a_result = _make_input_df(50).assign(score_a=range(50))
    node_b_result = _make_input_df(56).assign(score_b=range(56))  # esploso

    node_results = {"A": node_a_result, "B": node_b_result}

    with pytest.raises(RuntimeError) as excinfo:
        _merge_batch_parallel_node_results(
            common_input_df=common_input_df,
            node_results=node_results,
            node_ids=["A", "B"],
            task_id="task-test-1",
        )

    msg = str(excinfo.value)
    # Campi diagnostici operativi
    assert "task-test-1" in msg, f"task_id mancante in: {msg}"
    assert "'B'" in msg, f"node_id mancante in: {msg}"
    assert "56 righe" in msg, f"len(result_df) mancante in: {msg}"
    assert "common_input_df 50" in msg, f"len(common_input_df) mancante in: {msg}"
    # Cause probabili citate per orientare l'operatore
    assert "FeatureCollection" in msg
    assert "deduplica interna" in msg
    # Marker di policy: task fallito esplicitamente, non silente
    assert "fallito esplicitamente" in msg.lower()


def test_merge_raises_runtime_error_on_cardinality_shrink_dedup():
    """Specularmente: se un nodo riduce le righe (dedup interno) -> stesso
    fail-hard. La policy `cardinality must match` blocca entrambe le
    direzioni di mismatch."""
    common_input_df = _make_input_df(50)
    node_a_result = _make_input_df(50).assign(score_a=range(50))
    node_b_result = _make_input_df(48).assign(score_b=range(48))  # ridotto

    with pytest.raises(RuntimeError, match=r"48 righe vs common_input_df 50"):
        _merge_batch_parallel_node_results(
            common_input_df=common_input_df,
            node_results={"A": node_a_result, "B": node_b_result},
            node_ids=["A", "B"],
            task_id="task-test-2",
        )


def test_merge_runtime_error_does_not_mutate_node_results_on_failure():
    """Quando il merge fallisce, NON deve aver gia' iniziato a sovrascrivere
    node_results: il global handler del task riceve lo stato pre-merge per
    il close_run(success=False)."""
    common_input_df = _make_input_df(50)
    node_a_result = _make_input_df(50).assign(score_a=range(50))
    node_b_result = _make_input_df(56).assign(score_b=range(56))

    node_results = {"A": node_a_result, "B": node_b_result}
    a_before = node_results["A"]
    b_before = node_results["B"]

    with pytest.raises(RuntimeError):
        _merge_batch_parallel_node_results(
            common_input_df=common_input_df,
            node_results=node_results,
            node_ids=["A", "B"],
            task_id="task-test-3",
        )

    # node_results NON e' stato sostituito col merged: i DataFrame originali
    # restano referenziabili (utili per debug post-mortem).
    assert node_results["A"] is a_before
    assert node_results["B"] is b_before


# =============================================================================
# 2. Same cardinality -> merge OK, colonne propagate a TUTTI i node_results
# =============================================================================


def test_merge_combines_columns_when_all_nodes_same_cardinality():
    """Regression: se tutti i nodi del batch ritornano cardinalita' uguale
    a common_input_df, il merge produce un DataFrame con tutte le colonne
    combinate (id+x da input, score_a da A, score_b da B). Il merged e'
    propagato come node_results[nid] di OGNI nodo del batch (cosi' il
    nodo successivo nel DAG legge tutte le colonne, indipendentemente da
    quale upstream tocchi)."""
    common_input_df = _make_input_df(50)
    node_a_result = _make_input_df(50).assign(score_a=list(range(100, 150)))
    node_b_result = _make_input_df(50).assign(score_b=list(range(200, 250)))

    node_results = {"A": node_a_result, "B": node_b_result}

    # Nessuna eccezione
    _merge_batch_parallel_node_results(
        common_input_df=common_input_df,
        node_results=node_results,
        node_ids=["A", "B"],
        task_id="task-test-ok",
    )

    # Stesso DataFrame merged riferito da entrambi i nid
    merged_a = node_results["A"]
    merged_b = node_results["B"]
    assert merged_a is merged_b, (
        "Atteso che node_results[A] e [B] puntino allo STESSO oggetto merged"
    )

    # Colonne combinate: id + x (input) + score_a + score_b (entrambi i nodi)
    assert set(merged_a.columns) == {"id", "x", "score_a", "score_b"}
    # Cardinalita' invariata
    assert len(merged_a) == 50
    # Valori preservati
    assert list(merged_a["score_a"]) == list(range(100, 150))
    assert list(merged_a["score_b"]) == list(range(200, 250))


def test_merge_skips_non_dataframe_node_results():
    """Se un nodo del batch non ha prodotto un DataFrame (es. ha sollevato
    e ha un dict di errore o None), viene saltato. Gli altri nodi del
    batch vengono mergiati regolarmente."""
    common_input_df = _make_input_df(10)
    node_a_result = _make_input_df(10).assign(score_a=range(10))
    node_b_result = {"error": "qualcosa"}  # non e' un DataFrame
    node_c_result = None  # missing

    node_results = {"A": node_a_result, "B": node_b_result, "C": node_c_result}

    _merge_batch_parallel_node_results(
        common_input_df=common_input_df,
        node_results=node_results,
        node_ids=["A", "B", "C"],
        task_id="task-test-skip",
    )

    # A propaga le sue colonne; B e C vengono assegnati al merged ma il
    # merged contiene solo le colonne aggiunte da A (gli altri sono stati
    # skippati nel for di assignment colonne).
    merged = node_results["A"]
    assert set(merged.columns) == {"id", "x", "score_a"}
    assert node_results["B"] is merged
    assert node_results["C"] is merged


# =============================================================================
# 3. Anti-regression: nessun fallback silenzioso
# =============================================================================


def test_merge_does_not_emit_silent_warning_on_failure():
    """Anti-regression: il vecchio pattern `try/except Exception ->
    logger.warning('Merge batch parallelo fallito')` e' stato RIMOSSO.
    Su mismatch, l'errore deve raise (verificato da test #1) e logger.warning
    NON deve essere stato chiamato col messaggio storico fuorviante.

    Questo test blinda che future regressioni che reintroducano un
    try/except + warning generico vengano scoperte."""
    import workers.tasks.enrichment_task as enrichment_task_mod

    common_input_df = _make_input_df(50)
    node_a_result = _make_input_df(50)
    node_b_result = _make_input_df(56)  # esploso -> mismatch

    node_results = {"A": node_a_result, "B": node_b_result}

    spy_warning = MagicMock()
    with patch.object(enrichment_task_mod.logger, "warning", spy_warning):
        with pytest.raises(RuntimeError):
            _merge_batch_parallel_node_results(
                common_input_df=common_input_df,
                node_results=node_results,
                node_ids=["A", "B"],
                task_id="task-test-no-silent",
            )

    # NESSUN warning generico "Merge batch parallelo fallito" / "Proseguo"
    # deve essere stato emesso.
    for call in spy_warning.call_args_list:
        msg = call.args[0] if call.args else ""
        assert "Merge batch parallelo fallito" not in msg, (
            f"Anti-regression failed: vecchio warning silenzioso emesso: {msg}"
        )
        assert "Proseguo con node_results per-nodo" not in msg, (
            f"Anti-regression failed: vecchio fallback silenzioso emesso: {msg}"
        )
