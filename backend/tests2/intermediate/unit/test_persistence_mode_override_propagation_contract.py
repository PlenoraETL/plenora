"""
Contract test R-25 propagation contract — propagation `intermediate_mode_override`.

Verifica end-to-end che il kwarg `intermediate_mode_override` propagato
"""

from __future__ import annotations

from unittest.mock import patch, MagicMock

import pandas as pd
import pytest


pytestmark = [pytest.mark.unit, pytest.mark.intermediate, pytest.mark.contract]


# Fixture comuni

def _make_cfg(mode: str = "metadata_only", **overrides) -> dict:
    """Settings dict minimale per `_read_settings` mockato."""
    cfg = {
        "mode": mode,
        "preview_rows": 500,
        "artifact_format": "parquet",
        "manipola_checkpoints": "critical",
        "estrai_checkpoints": "final_only",
        "arricchisci_checkpoints": "final_only",
        "artifact_bucket": None,
        "artifact_prefix": "intermediate",
        "fingerprint_mode": "sample",
        "fingerprint_sample_rows": 1000,
    }
    cfg.update(overrides)
    return cfg


def _patch_settings(cfg: dict):
    from app.core.intermediate import policy as _policy
    return patch.object(_policy, "_read_settings", return_value=cfg)


@pytest.fixture(autouse=True)
def _reset_policy_cache():
    from app.core.intermediate.policy import reset_validation_cache
    reset_validation_cache()
    yield
    reset_validation_cache()


@pytest.fixture
def small_df():
    """DataFrame piccolo deterministico, abbastanza per esercitare
    metadata_only / preview_only / full_pg senza overhead."""
    return pd.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]})


# A. policy.resolve_intermediate_policy con override

class TestResolvePolicyOverride:
    """L'override per-call sostituisce la mode globale al boundary
    `resolve_intermediate_policy`. Nessun altro side-effect."""

    def test_override_none_usa_default_globale(self):
        from app.core.intermediate import policy as _policy

        with _patch_settings(_make_cfg(mode="metadata_only")):
            result = _policy.resolve_intermediate_policy(
                module="estrai", snapshot_kind="final",
                node_type="estrai", mode_override=None,
            )
        assert result.persistence_mode == "metadata_only"

    def test_override_full_pg_su_default_metadata_only(self):
        from app.core.intermediate import policy as _policy

        with _patch_settings(_make_cfg(mode="metadata_only")):
            result = _policy.resolve_intermediate_policy(
                module="estrai", snapshot_kind="final",
                node_type="estrai", mode_override="full_pg",
            )
        assert result.persistence_mode == "full_pg"
        assert result.should_record_meta is True
        assert result.should_record_rows is True

    def test_override_preview_only_su_default_metadata_only(self):
        from app.core.intermediate import policy as _policy

        with _patch_settings(_make_cfg(mode="metadata_only")):
            result = _policy.resolve_intermediate_policy(
                module="estrai", snapshot_kind="final",
                node_type="estrai", mode_override="preview_only",
            )
        assert result.persistence_mode == "preview_only"
        assert result.preview_rows == 500
        assert result.should_record_rows is True

    def test_override_metadata_only_su_default_full_pg(self):
        """Override puo' anche RIDURRE la persistenza, non solo aumentarla."""
        from app.core.intermediate import policy as _policy

        with _patch_settings(_make_cfg(mode="full_pg")):
            result = _policy.resolve_intermediate_policy(
                module="manipola", snapshot_kind="final",
                node_type="result", mode_override="metadata_only",
            )
        assert result.persistence_mode == "metadata_only"
        assert result.should_record_rows is False

    def test_override_invalido_solleva(self):
        """Fail-loud che propaga al chiamante. Nessun fallback al default."""
        from app.core.intermediate import policy as _policy

        with _patch_settings(_make_cfg(mode="metadata_only")):
            with pytest.raises(_policy.IntermediateSettingsError) as exc_info:
                _policy.resolve_intermediate_policy(
                    module="estrai", snapshot_kind="final",
                    node_type="estrai", mode_override="DOES_NOT_EXIST",
                )
        assert "DOES_NOT_EXIST" in str(exc_info.value)

    def test_override_non_intacca_skip_decision_per_checkpoint_arricchisci(self):
        """Arricchisci checkpoint resta skipped anche con override pesante:
        l'override sostituisce la MODE, non la decisione di registrare.

        Garantisce che propagation contract non rompa la policy di scope (skip checkpoint
        per arricchisci e' da `ARRICCHISCI_INTERMEDIATE_CHECKPOINTS=final_only`,
        indipendente da `mode_override`)."""
        from app.core.intermediate import policy as _policy

        with _patch_settings(_make_cfg(mode="metadata_only")):
            result = _policy.resolve_intermediate_policy(
                module="arricchisci", snapshot_kind="checkpoint",
                node_type="api_enrich", mode_override="full_pg",
            )
        # Skip non e' alterato dall'override.
        assert result.should_record_meta is False
        assert result.should_record_rows is False


# B. snapshot wrappers propagano il kwarg

class TestSnapshotWrappersPropagation:
    """`record_final_snapshot` e `record_checkpoint_snapshot` accettano
    `mode_override` e lo passano a `_record_snapshot` -> resolve."""

    def test_record_final_snapshot_propaga_a_resolve(self, small_df):
        from app.core.intermediate import snapshot as _snap

        captured = {}
        original = _snap._policy.resolve_intermediate_policy

        def spy(**kwargs):
            captured.update(kwargs)
            return original(**kwargs)

        with _patch_settings(_make_cfg(mode="metadata_only")):
            with patch.object(_snap._policy, "resolve_intermediate_policy", side_effect=spy):
                # Mock del DB writer + fingerprint per evitare side-effect
                # reali (qui ci interessa solo il kwarg propagato).
                with patch.object(_snap._node_snapshots_db, "write_node_snapshot_atomic"):
                    _snap.record_final_snapshot(
                        run_id="run-1", module="estrai",
                        node_id="n-1", node_type="estrai",
                        df=small_df, mode_override="full_pg",
                    )

        assert captured.get("mode_override") == "full_pg"

    def test_record_checkpoint_snapshot_propaga_a_resolve(self, small_df):
        from app.core.intermediate import snapshot as _snap

        captured = {}
        original = _snap._policy.resolve_intermediate_policy

        def spy(**kwargs):
            captured.update(kwargs)
            return original(**kwargs)

        with _patch_settings(_make_cfg(mode="metadata_only")):
            with patch.object(_snap._policy, "resolve_intermediate_policy", side_effect=spy):
                with patch.object(_snap._node_snapshots_db, "write_node_snapshot_atomic"):
                    _snap.record_checkpoint_snapshot(
                        run_id="run-1", module="manipola",
                        node_id="n-1", node_type="multi_join",
                        df=small_df, mode_override="preview_only",
                    )

        assert captured.get("mode_override") == "preview_only"

    def test_record_final_snapshot_default_kwarg_e_none(self, small_df):
        """Senza passare mode_override, deve arrivare None a resolve
        (bit-compat col default-path pre-PR)."""
        from app.core.intermediate import snapshot as _snap

        captured = {}
        original = _snap._policy.resolve_intermediate_policy

        def spy(**kwargs):
            captured.update(kwargs)
            return original(**kwargs)

        with _patch_settings(_make_cfg(mode="metadata_only")):
            with patch.object(_snap._policy, "resolve_intermediate_policy", side_effect=spy):
                with patch.object(_snap._node_snapshots_db, "write_node_snapshot_atomic"):
                    _snap.record_final_snapshot(
                        run_id="run-1", module="estrai",
                        node_id="n-1", node_type="estrai",
                        df=small_df,
                    )

        assert captured.get("mode_override") is None

    def test_record_final_snapshot_invalid_override_solleva(self, small_df):
        """Override invalido propaga IntermediateSettingsError fino al
        chiamante (no swallow nel wrapper)."""
        from app.core.intermediate import snapshot as _snap
        from app.core.intermediate.policy import IntermediateSettingsError

        with _patch_settings(_make_cfg(mode="metadata_only")):
            with patch.object(_snap._node_snapshots_db, "write_node_snapshot_atomic"):
                with pytest.raises(IntermediateSettingsError):
                    _snap.record_final_snapshot(
                        run_id="run-1", module="estrai",
                        node_id="n-1", node_type="estrai",
                        df=small_df, mode_override="not-a-real-mode",
                    )

    def test_persistence_mode_su_node_meta_riflette_override(self, small_df):
        """End-to-end al layer DB writer: il `persistence_mode` passato a
        `write_node_snapshot_atomic` deve riflettere l'override, non il
        default globale."""
        from app.core.intermediate import snapshot as _snap

        with _patch_settings(_make_cfg(mode="metadata_only")):
            mock_writer = MagicMock()
            with patch.object(_snap._node_snapshots_db, "write_node_snapshot_atomic", mock_writer):
                _snap.record_final_snapshot(
                    run_id="run-1", module="estrai",
                    node_id="n-1", node_type="estrai",
                    df=small_df, mode_override="full_pg",
                )

        assert mock_writer.called
        call_kwargs = mock_writer.call_args.kwargs
        assert call_kwargs["persistence_mode"] == "full_pg"


# C. QueryExecutor.execute_query propaga (kwarg esplicito, no stato)

class TestQueryExecutorPropagation:
    """Il kwarg `intermediate_mode_override` su `execute_query` arriva
    a `record_final_snapshot(mode_override=...)`. Niente stato d'istanza:
    due chiamate consecutive con override diversi non si contaminano.

    Strategia: forziamo il path **legacy** (output_format=json) che
    bypassa lo streaming e produce un DataFrame. Mockiamo i 4 punti
    esterni:
      - QueryExecutor.execute_raw -> (rows, columns)
      - save_pipeline_result_outcome -> typed result
      - record_final_snapshot -> spy che cattura mode_override
      - settings via _read_settings (default metadata_only)

    Tutto il resto del flusso e' codice reale di execute_query.
    """

    def _build_executor(self):
        """QueryExecutor con engine_factory mock (non usato perche'
        execute_raw e' mockato a sua volta)."""
        from app.db.query_executor import QueryExecutor
        return QueryExecutor(
            engine_factory=MagicMock(),
            save_function=MagicMock(return_value={
                "result_id": "r-1", "output_filename": "out.json",
            }),
        )

    def _run_with_spy(self, executor, *, override, settings_mode="metadata_only"):
        """Esegue execute_query con spy completo del chain. Ritorna
        (result_dict, captured_kwargs). Forza output_format=json per
        ottenere il path legacy (df != None) e quindi il branch
        record_final_snapshot."""
        from types import SimpleNamespace

        captured = {}

        def spy_record_final_snapshot(**kwargs):
            captured.update(kwargs)
            from app.core.intermediate.snapshot import SnapshotHandle
            return SnapshotHandle(
                recorded=True, reason="estrai_final",
                run_id=kwargs["run_id"], node_id=kwargs["node_id"],
            )

        # execute_raw: ritorna rows + columns deterministici, salta SQL.
        executor.execute_raw = MagicMock(
            return_value=([(1, "x"), (2, "y")], ["a", "b"])
        )

        fake_conn = SimpleNamespace(
            type="postgresql", id="conn-1", db_schema=None,
        )

        # Patch chain esterno:
        #   - save_pipeline_result_outcome -> typed result atteso
        #   - record_final_snapshot (snapshot) -> spy
        #   - _read_settings (policy) -> mode dal test
        kwargs = {"run_id": "r-1", "node_id": "n-1"}
        if override is not _UNSET:
            kwargs["intermediate_mode_override"] = override

        with _patch_settings(_make_cfg(mode=settings_mode)), \
             patch("app.db.query_result_persistence.save_pipeline_result_outcome",
                   return_value=SimpleNamespace(
                       result_id="r-1",
                       output_filename="out.json",
                   )), \
             patch("app.core.intermediate.snapshot.record_final_snapshot",
                   side_effect=spy_record_final_snapshot):
            result = executor.execute_query(
                connection=fake_conn,
                sql="SELECT 1",
                project_id="proj-1",
                output_filename="out",
                output_format="json",  # NOTE: legacy path
                **kwargs,
            )
        return result, captured

    def test_execute_query_signature_accetta_intermediate_mode_override(self):
        import inspect
        from app.db.query_executor import QueryExecutor

        sig = inspect.signature(QueryExecutor.execute_query)
        assert "intermediate_mode_override" in sig.parameters
        assert sig.parameters["intermediate_mode_override"].default is None

    def test_execute_query_signature_kwarg_dopo_node_id(self):
        """Il nuovo kwarg deve seguire node_id nella firma."""
        import inspect
        from app.db.query_executor import QueryExecutor

        sig = inspect.signature(QueryExecutor.execute_query)
        params = list(sig.parameters.keys())
        idx_node = params.index("node_id")
        idx_override = params.index("intermediate_mode_override")
        assert idx_override == idx_node + 1, (
            f"intermediate_mode_override deve seguire node_id. "
            f"Order: {params}"
        )

    def test_execute_query_propaga_full_pg(self):
        executor = self._build_executor()
        result, captured = self._run_with_spy(
            executor, override="full_pg", settings_mode="metadata_only",
        )
        assert captured.get("mode_override") == "full_pg", (
            f"Override non propagato. Captured: {captured}"
        )
        # La query stessa deve essere riuscita.
        assert result.get("success") is True or "result_id" in result

    def test_execute_query_propaga_preview_only(self):
        executor = self._build_executor()
        _, captured = self._run_with_spy(
            executor, override="preview_only", settings_mode="metadata_only",
        )
        assert captured.get("mode_override") == "preview_only"

    def test_execute_query_default_kwarg_propaga_none(self):
        """Bit-compat: nessun kwarg passato => mode_override=None a record."""
        executor = self._build_executor()
        _, captured = self._run_with_spy(
            executor, override=_UNSET, settings_mode="metadata_only",
        )
        assert "mode_override" in captured, (
            "record_final_snapshot deve ricevere mode_override come kwarg "
            "anche nel default-path (con valore None)."
        )
        assert captured["mode_override"] is None

    def test_execute_query_no_stato_dincombe_su_chiamate_consecutive(self):
        """Garanzia chiave: l'istanza QueryExecutor riusata da chiamanti
        diversi NON deve avere stato d'istanza per l'override. Tre call
        consecutive con override diversi propagano valori distinti
        nell'ordine atteso."""
        from types import SimpleNamespace

        captured_log = []

        def spy(**kwargs):
            captured_log.append(kwargs.get("mode_override"))
            from app.core.intermediate.snapshot import SnapshotHandle
            return SnapshotHandle(
                recorded=True, reason="estrai_final",
                run_id=kwargs["run_id"], node_id=kwargs["node_id"],
            )

        executor = self._build_executor()
        executor.execute_raw = MagicMock(
            return_value=([(1, "x")], ["a", "b"])
        )
        fake_conn = SimpleNamespace(
            type="postgresql", id="conn-1", db_schema=None,
        )

        with _patch_settings(_make_cfg(mode="metadata_only")), \
             patch("app.db.query_result_persistence.save_pipeline_result_outcome",
                   return_value=SimpleNamespace(
                       result_id="r-1",
                       output_filename="out.json",
                   )), \
             patch("app.core.intermediate.snapshot.record_final_snapshot",
                   side_effect=spy):
            executor.execute_query(
                connection=fake_conn, sql="S1",
                project_id="p", output_filename="o1",
                output_format="json",
                run_id="r-1", node_id="n-1",
                intermediate_mode_override="full_pg",
            )
            executor.execute_query(
                connection=fake_conn, sql="S2",
                project_id="p", output_filename="o2",
                output_format="json",
                run_id="r-2", node_id="n-2",
                intermediate_mode_override="preview_only",
            )
            executor.execute_query(
                connection=fake_conn, sql="S3",
                project_id="p", output_filename="o3",
                output_format="json",
                run_id="r-3", node_id="n-3",
                # NO override: deve essere None
            )

        assert captured_log == ["full_pg", "preview_only", None], (
            f"Stato d'istanza fragile rilevato. Log: {captured_log}. "
            "Una delle 3 chiamate ha contaminato lo stato delle altre."
        )

    def test_execute_query_invalid_override_query_non_rotta(self):
        """Override invalido NON deve rompere il salvataggio del file
        utente. Il chain produzione cattura `IntermediateSettingsError`
        nel try/except non-bloccante intorno al record_final_snapshot
        (snapshot e' best-effort: il file utente arriva, lo snapshot
        viene loggato come warning).

        Comportamento atteso:
          - result.success == True (file salvato).
          - record_final_snapshot solleva (fail-loud), ma il caller
            inghiotte con logger.warning. Quindi NIENTE fallback al
            default mode: lo snapshot semplicemente non viene scritto."""
        from types import SimpleNamespace

        executor = self._build_executor()
        executor.execute_raw = MagicMock(
            return_value=([(1, "x")], ["a", "b"])
        )
        fake_conn = SimpleNamespace(
            type="postgresql", id="conn-1", db_schema=None,
        )

        # In questo test record_final_snapshot e' il REALE: deve
        # sollevare IntermediateSettingsError perche' override "INVALID"
        # non passa la validation di resolve_intermediate_mode.
        with _patch_settings(_make_cfg(mode="metadata_only")), \
             patch("app.db.query_result_persistence.save_pipeline_result_outcome",
                   return_value=SimpleNamespace(
                       result_id="r-1",
                       output_filename="out.json",
                   )):
            result = executor.execute_query(
                connection=fake_conn, sql="S",
                project_id="p", output_filename="o",
                output_format="json",
                run_id="r-1", node_id="n-1",
                intermediate_mode_override="DEFINITELY_INVALID",
            )

        # File utente salvato (snapshot fail e' best-effort).
        assert result.get("success") is True, (
            f"Query rotta da fail snapshot (non doveva): {result}"
        )

    def test_execute_query_invalid_override_no_fallback_silenzioso(self):
        """L'override invalido NON deve diventare metadata_only di
        nascosto: il record_final_snapshot deve fallire prima di
        scrivere DB. Verifica via spy sul DB writer atomic che non
        venga chiamato con persistence_mode=metadata_only (perche' il
        chain ha sollevato prima)."""
        from app.core.intermediate import node_snapshots_db as _node_snapshots_db
        from types import SimpleNamespace

        executor = self._build_executor()
        executor.execute_raw = MagicMock(
            return_value=([(1, "x")], ["a", "b"])
        )
        fake_conn = SimpleNamespace(
            type="postgresql", id="conn-1", db_schema=None,
        )

        writer_spy = MagicMock()
        with _patch_settings(_make_cfg(mode="metadata_only")), \
             patch("app.db.query_result_persistence.save_pipeline_result_outcome",
                   return_value=SimpleNamespace(
                       result_id="r-1",
                       output_filename="out.json",
                   )), \
             patch.object(_node_snapshots_db, "write_node_snapshot_atomic", writer_spy):
            executor.execute_query(
                connection=fake_conn, sql="S",
                project_id="p", output_filename="o",
                output_format="json",
                run_id="r-1", node_id="n-1",
                intermediate_mode_override="DEFINITELY_INVALID",
            )

        # write_node_snapshot_atomic NON deve essere stato chiamato:
        # l'override invalido e' fallito in resolve_intermediate_mode,
        # propagato a resolve_intermediate_policy, sollevato dentro
        # _record_snapshot, catturato dal try/except non-bloccante in
        # query_executor.execute_query. Niente fallback al default,
        # niente record DB con mode "default".
        assert not writer_spy.called, (
            "Override invalido ha generato un record DB silenziosamente. "
            "Stack hint: chain dovrebbe sollevare IntermediateSettingsError "
            "prima di chiamare write_node_snapshot_atomic. "
            f"call_args_list: {writer_spy.call_args_list}"
        )


# Sentinel per distinguere "kwarg non passato" da "passato come None".
_UNSET = object()
