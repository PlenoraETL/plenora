"""
Mixin SUPPORT — FIELD_INTROSPECTION + RESULT_BUILDING per

`ApiConnectionService`.
"""

from __future__ import annotations

import shutil

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional

from app.core.dataframes.values import is_nonempty_dataframe
from app.utils.logger import get_logger
from app.core.results.blob_writer import save_result_blob
from app.core.results.pipeline_result import save_pipeline_result_outcome
from app.core.arricchisci.types import JsonObject, JsonRows

# Stesso logger name di `api_connector.py` (`__name__` valuta a
# `"app.core.arricchisci.api_connector"`). Singleton stdlib per nome →
# record indistinguibili dai consumer che attaccano handler / filtrano
# per nome canonico (identico al pattern B3-B.2b
logger = get_logger("app.core.arricchisci.api_connector")


class ApiConnectionSupportMixin:
    """Mixin per `ApiConnectionService`: FIELD_INTROSPECTION (analisi
    JSON response) + RESULT_BUILDING (orchestration helpers per
    `execute_enrichment`).
    """

    # FIELD_INTROSPECTION — analisi struttura JSON response per UI output mapping

    def _extract_available_fields(
        self,
        data: Any,
        prefix: str = "",
        max_depth: int = 3,
    ) -> JsonRows:
        """
        Estrae i campi disponibili da una risposta JSON.
        
        Analizza ricorsivamente la struttura JSON fino a max_depth livelli,
        generando un elenco piatto di campi con path, tipo e valore di esempio.
        Gestisce casi speciali: GeoJSON FeatureCollection, array di oggetti,
        geometrie (non esplorate ricorsivamente).
        
        Returns:
            Lista di {name, path, type, sample_value} per ogni campo trovato
        """
        fields = []

        if max_depth <= 0:
            return fields

        # Caso: array - analizza il primo elemento
        if isinstance(data, list):
            if len(data) > 0:
                first_item = data[0]
                # Se è un array alla root, indica che ogni elemento ha questi campi
                if not prefix:
                    # GeoJSON FeatureCollection check
                    if isinstance(first_item, dict) and first_item.get('type') == 'Feature':
                        return self._extract_geojson_fields(data)
                    # Path senza prefisso [0]: `_extract_rows_from_response`
                    # itera gia' su ogni item, quindi i path devono
                    # essere relativi all'item
                    fields.extend(self._extract_available_fields(first_item, "", max_depth - 1))
                else:
                    fields.extend(self._extract_available_fields(first_item, f"{prefix}[0]", max_depth - 1))
            return fields

        # Caso: GeoJSON FeatureCollection
        if isinstance(data, dict) and data.get('type') == 'FeatureCollection':
            return self._extract_geojson_fields(data)

        # Caso: oggetto
        if isinstance(data, dict):
            for key, value in data.items():
                field_path = f"{prefix}.{key}" if prefix else key
                field_type = self._get_field_type(value)

                # Aggiungi il campo
                fields.append({
                    "name": key,
                    "path": field_path,
                    "type": field_type,
                    "sample_value": self._get_sample_value(value)
                })

                # Se è un oggetto nested, estrai anche i suoi campi
                if isinstance(value, dict) and not self._is_geometry(value):
                    fields.extend(self._extract_available_fields(value, field_path, max_depth - 1))
                elif isinstance(value, list) and len(value) > 0:
                    if isinstance(value[0], dict):
                        fields.extend(self._extract_available_fields(value[0], f"{field_path}[0]", max_depth - 1))
                    else:
                        # Array di primitivi (es. latlng: [42.83, 12.83]) —
                        # espandi tutti gli indici presenti (max 10
                        for idx, item in enumerate(value[:10]):
                            item_path = f"{field_path}[{idx}]"
                            fields.append({
                                "name": f"{field_path}[{idx}]",
                                "path": item_path,
                                "type": self._get_field_type(item),
                                "sample_value": self._get_sample_value(item)
                            })

        return fields

    def _extract_geojson_fields(self, data: Any) -> JsonRows:
        """Estrae campi da GeoJSON FeatureCollection."""
        fields = []

        features = data.get('features', []) if isinstance(data, dict) else data
        if not features or not isinstance(features, list):
            return fields

        first_feature = features[0]
        if not isinstance(first_feature, dict):
            return fields

        # Geometry
        if first_feature.get('geometry'):
            fields.append({
                "name": "geometry",
                "path": "features[].geometry",
                "type": "geojson",
                "sample_value": first_feature['geometry'].get('type', 'Unknown')
            })

        # Properties
        properties = first_feature.get('properties', {})
        if isinstance(properties, dict):
            for key, value in properties.items():
                fields.append({
                    "name": key,
                    "path": f"features[].properties.{key}",
                    "type": self._get_field_type(value),
                    "sample_value": self._get_sample_value(value)
                })

        # ID
        if 'id' in first_feature:
            fields.insert(0, {
                "name": "id",
                "path": "features[].id",
                "type": self._get_field_type(first_feature['id']),
                "sample_value": self._get_sample_value(first_feature['id'])
            })

        return fields

    def _is_geometry(self, value: Any) -> bool:
        """Verifica se un valore è una geometria GeoJSON."""
        if not isinstance(value, dict):
            return False
        return value.get('type') in ['Point', 'LineString', 'Polygon',
                                      'MultiPoint', 'MultiLineString', 'MultiPolygon',
                                      'GeometryCollection']

    def _get_field_type(self, value: Any) -> str:
        """Determina il tipo di un campo."""
        if value is None:
            return "null"
        if isinstance(value, bool):
            return "boolean"
        if isinstance(value, int):
            return "integer"
        if isinstance(value, float):
            return "number"
        if isinstance(value, str):
            return "string"
        if isinstance(value, list):
            return "array"
        if isinstance(value, dict):
            if self._is_geometry(value):
                return "geojson"
            return "object"
        return "unknown"

    def _get_sample_value(self, value: Any, max_len: int = 50) -> str:
        """Restituisce un valore di esempio come stringa."""
        if value is None:
            return "null"
        if isinstance(value, (dict, list)):
            if self._is_geometry(value):
                return value.get('type', 'Geometry')
            return f"[{len(value)} items]" if isinstance(value, list) else "{...}"
        sample = str(value)
        if len(sample) > max_len:
            return sample[:max_len] + "..."
        return sample

    # RESULT_BUILDING — orchestration helpers per `execute_enrichment`
    # Pipeline: carica sorgenti → esegui nodi in ordine topologico →
    # arricchisci via API → salva output finale
    # Supporta nodi: project_source, file_source, staticValues, api_enrich, result

    def _find_final_result(
        self,
        node_results: JsonObject,
        result_node: Optional[JsonObject],
        execution_order: List[str],
    ) -> Any:
        """Risolve il risultato finale della pipeline.

        Priorita':
          1. Output del nodo `result` se presente e ha prodotto qualcosa
          2. Fallback: ultimo nodo in execution_order con un risultato
             non-None e non-marker passthrough file_source

        Ritorna `None` se nessun nodo ha prodotto output.
        """
        if result_node is not None:
            candidate = node_results.get(result_node['id'])
            if candidate is not None:
                return candidate

        for node_id in reversed(execution_order):
            r = node_results.get(node_id)
            if r is None:
                continue
            if isinstance(r, dict) and r.get('_passthrough_file_source'):
                continue
            return r
        return None

    def _build_file_result_response(
        self, final_result: JsonObject, project_id: str
    ) -> JsonObject:
        """Costruisce la response dict per un risultato file-based
        (tipicamente `_execute_rest_file` con `is_file_result=True`).

        Persiste il blob + pulisce la dir temporanea `plenora_exec_*` se
        presente (best-effort).

        Il dict ritornato espone `result_id` (riferimento canonico al
        blob nel DB results) e `output_filename` per display, ma NON
        `output_path`/`output_file`: dopo `save_result_blob` il file
        temp non esiste piu' (vedi `app/core/results/blob_writer.py`),
        quindi qualunque path emesso sarebbe stale. I consumer
        downstream usano `result_id` via `PipelineArtifact.artifact_kind
        == "blob"` e l'ArtifactResolver per materializzare dal DB.
        """
        src_path = Path(final_result['output_path'])
        src_name = src_path.name
        # Leggi la size PRIMA del save: save_result_blob cancella il
        # file temp nel finally su success, quindi src_path.stat
        # dopo la chiamata solleverebbe FileNotFoundError
        size_bytes = src_path.stat().st_size

        result_id = save_result_blob(
            project_id, src_path, src_path.suffix.lstrip('.') or 'bin', 0
        )

        response = {
            "success": True,
            "result_id": result_id,
            "output_type": "file",
            "output_filename": src_name,
            "output_size_bytes": size_bytes,
            "rows": 0,
            "rows_count": 0,
            "rows_processed": 0,
            "columns": [],
            "columns_count": 0,
            "execution_time_ms": final_result.get('execution_time_ms', 0),
            "metadata": final_result.get('metadata', {}),
            "message": f"Processamento completato: {src_name} ({size_bytes // 1024} KB)",
        }

        # Cleanup dir tmp dell'executable (solo se il nome combacia con
        # il pattern `plenora_exec_*`: niente rimozioni di cartelle altrui
        try:
            tmp_dir = src_path.parent
            if tmp_dir.name.startswith("plenora_exec_"):
                shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception as _cleanup_err:
            logger.debug(
                f"cleanup plenora_exec_ dir best-effort fallito: {_cleanup_err}"
            )

        return response

    def _save_and_build_dataframe_response(
        self,
        final_df: Any,
        output_name: str,
        output_format: str,
        project_id: str,
    ) -> JsonObject:
        """Salva il DataFrame finale nel formato richiesto, persiste il
        blob, costruisce la response dict standard.

        Il dict ritornato espone `result_id` (riferimento canonico al
        blob nel DB results) e `output_filename` per display, ma NON
        `output_path`/`output_file`: dopo `save_result_blob` il file
        temp non esiste piu' (vedi `app/core/results/blob_writer.py`),
        quindi qualunque path emesso sarebbe stale. I consumer
        downstream usano `result_id` via `PipelineArtifact.artifact_kind
        == "blob"` e l'ArtifactResolver per materializzare dal DB.

        Invariante caller-responsibility (output_contract):
            La response include `output_contract` SOLO come hint
            descrittivo della shape colonne. La PERSISTENZA del contract
            (via `project_service.update_project_output_contract`) e'
            responsabilita' del caller, NON di questo metodo (il mixin
            non ha accesso a `project_service`). Tutti i caller in
            produzione passano da `EnrichExecutor.execute()` che esegue
            l'update con try/except + flag `contract_update_failed`
            (vedi `arricchisci/executor.py:137-170`). Se introduci un
            nuovo caller esterno a `EnrichExecutor`, DEVI persistere
            il contract a quel livello — altrimenti il progetto
            espone schema stale.
        """
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        filename_stem = f"{output_name}_{timestamp}"

        saved = save_pipeline_result_outcome(
            project_id=project_id,
            df=final_df,
            output_format=output_format,
            filename_stem=filename_stem,
            rows_count=len(final_df),
        )

        from app.core.output_contract import build_output_contract

        physical_columns = (
            saved.output_columns
            if saved.output_columns and len(saved.output_columns) == len(final_df.columns)
            else list(final_df.columns)
        )
        output_contract = build_output_contract(
            project_id=project_id,
            result_columns=[
                {
                    "source_name": str(c),
                    "name": str(physical_name),
                    "type": str(final_df[c].dtype),
                }
                for c, physical_name in zip(final_df.columns, physical_columns)
            ],
        ).model_dump()

        return {
            "success": True,
            "result_id": saved.result_id,
            "output_filename": saved.output_filename,
            "rows": len(final_df),
            "rows_count": len(final_df),
            "rows_processed": len(final_df),
            "columns": list(final_df.columns),
            "output_columns": saved.output_columns,
            "output_contract": output_contract,
            "columns_count": len(final_df.columns),
            "message": f"Arricchimento completato: {len(final_df)} righe processate",
        }

    def _write_intermediate_snapshot(
        self, run_id: str, node_id: str, node_type: str, df: Any,
        mode_override: Optional[str] = None,
    ) -> None:
        """Best-effort snapshot del DataFrame del nodo arricchisci.

        instradato attraverso la nuova
        `record_checkpoint_snapshot(module="arricchisci")`. La policy
        globale `ARRICCHISCI_INTERMEDIATE_CHECKPOINTS=final_only` (default)
        esclude i nodi `api_enrich` dagli snapshot intermedi: il
        resolver ritorna `recorded=False` PRIMA di toccare il DataFrame
        (no `df.to_dict`, no fingerprint, no DB).

        `mode_override` (R-25 PR-B2.b): override per-call della
        `INTERMEDIATE_PERSISTENCE_MODE` globale. None = bit-compat.
        Nota: se la policy decide skip (default `final_only` per
        arricchisci), l'override e' ignorato — la skip-decision e'
        governata da `ARRICCHISCI_INTERMEDIATE_CHECKPOINTS`,
        indipendente dalla mode.

        No-op se `df` non e' un DataFrame non vuoto.
        """
        if not is_nonempty_dataframe(df):
            return
        try:
            from app.core.intermediate.snapshot import record_checkpoint_snapshot
            record_checkpoint_snapshot(
                run_id=run_id,
                module="arricchisci",
                node_id=node_id,
                node_type=node_type,
                df=df,
                exec_time=0.0,
                status="ok",
                mode_override=mode_override,
            )
        except Exception as e:
            logger.warning(
                f"Intermediate write {node_id} fallita (non bloccante): {e}"
            )
