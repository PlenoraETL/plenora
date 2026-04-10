"""
Executor del modulo MANIPOLA - pipeline di trasformazione dati.

Architettura
============
Orchestra la pipeline di trasformazione: carica sorgenti (progetti, file,
valori statici), applica trasformazioni definite nel canvas tramite
PluginExecutor, e salva il risultato nel formato richiesto.

Flusso: Carica sorgenti → Applica trasformazioni → GROUP BY → Salva output
"""

import traceback
import re
import time as _time
import pandas as pd
from pathlib import Path
from typing import Dict, Any, List, Optional

from app.models import CanvasState, Node
from app.config import settings
from app.utils.logger import get_logger, append_step
from app.utils.result_store import get_temp_output_path, save_result_blob, get_result_as_tempfile
from app.utils.dataframe import (
    load_dataframe_from_file, save_dataframe, filter_visible_columns,
    sanitize_for_json, find_latest_output_file
)
from app.utils.geo import detect_geometry_column, extract_srid_from_column_type
from app.utils.template_variables import process_static_fields
from .plugin_executor import get_executor

logger = get_logger(__name__)

def _nf(node, key, default=None):
    """Legge un campo flat dal nodo Pydantic, con fallback su node.data (backward compat)."""
    val = getattr(node, key, None)
    if val is not None and val != [] and val != {}:
        return val
    if node.data and isinstance(node.data, dict):
        return node.data.get(key, default)
    return default


def _applica_crs_da_colonne(df, node_columns):
    """
    Se il DataFrame contiene una colonna geometrica e node_columns ha il CRS
    (formato geometry:subtype:SRID), converte il DataFrame in GeoDataFrame
    con il CRS corretto. Evita che le trasformazioni spaziali downstream
    perdano il CRS perché il CSV non lo preserva.
    """
    import geopandas as gpd
    from app.utils.geo import extract_srid_from_column_type, to_geodataframe, detect_geometry_column

    if isinstance(df, gpd.GeoDataFrame):
        return df  # già GeoDataFrame, non toccare

    # Cerca colonna geometrica con SRID nei metadati del nodo
    geom_col = None
    srid = None
    for col in node_columns:
        col_name = col.get('alias') or col.get('name', '')
        col_type = col.get('type', '')
        if col_type.startswith('geometry') and col_name in df.columns:
            extracted = extract_srid_from_column_type(col_type)
            if extracted:
                geom_col = col_name
                srid = extracted
                break

    if geom_col and srid:
        try:
            gdf = to_geodataframe(df, geom_col, crs=f"EPSG:{srid}")
            # Override esplicito nel caso extract_srid_from_ewkb abbia letto diversamente
            if gdf.crs and str(gdf.crs.to_epsg()) != str(srid):
                gdf = gdf.set_crs(f"EPSG:{srid}", allow_override=True)
            logger.info(f"CRS applicato da metadati nodo: EPSG:{srid} su colonna '{geom_col}'")
            return gdf
        except Exception as e:
            logger.warning(f"Impossibile applicare CRS da metadati: {e}")

    return df


class ManipolaExecutor:
    """Executor MANIPOLA: orchestra la pipeline di trasformazione dati."""

    def __init__(self, project_service, orchestrator_callback=None):
        self.project_service = project_service
        self.orchestrator_callback = orchestrator_callback
        self.results_base_dir = settings.RESULTS_DIR

    def set_orchestrator_callback(self, callback):
        """Imposta callback per esecuzione ricorsiva progetti upstream."""
        self.orchestrator_callback = callback

    # =========================================================================
    # ESECUZIONE COMPLETA
    # =========================================================================

    def execute(self, project_id: str, canvas: CanvasState, force_recalculate: bool = False, run_id: str = None) -> Dict[str, Any]:
        """Esegue la pipeline completa: carica sorgenti, trasforma, salva."""
        logger.info(f"Esecuzione MANIPOLA: {project_id}", op="execute", project_id=project_id)

        # SECURITY: memorizza il group_id del progetto corrente per i check
        # cross-tenant in _carica_sorgenti_progetto / _carica_sorgenti_file.
        try:
            import app.db.db_service as _db
            _proj_row = _db.get_project(project_id)
            self._current_group_id = _proj_row.get("group_id") if _proj_row else None
        except Exception:
            self._current_group_id = None

        _step_start = _time.time()
        dataframes = {}

        for loader in [
            lambda: self._carica_sorgenti_progetto(canvas, dataframes, force_recalculate, run_id=run_id),
            lambda: self._carica_sorgenti_file(canvas, dataframes),
            lambda: self._carica_sorgenti_statiche(canvas, dataframes),
        ]:
            err = loader()
            if err:
                append_step({
                    "module":      "manipola",
                    "status":      "failure",
                    "rows_out":    0,
                    "duration_ms": int((_time.time() - _step_start) * 1000),
                    "error":       err.get("message"),
                })
                return err

        logger.info(f"Caricati {len(dataframes)} DataFrame", op="execute", project_id=project_id)
        try:
            current_df = self._applica_trasformazioni(canvas, dataframes, run_id=run_id)
        except ValueError as e:
            append_step({
                "module":      "manipola",
                "status":      "failure",
                "rows_out":    0,
                "duration_ms": int((_time.time() - _step_start) * 1000),
                "error":       str(e),
            })
            return {"success": False, "message": str(e)}
        if current_df is None:
            return {"success": False, "message": "Nessun dato prodotto dalla manipolazione"}

        result_node = next((n for n in canvas.nodes if n.type == "result"), None)
        if not result_node:
            return {"success": False, "message": "Nodo risultato non trovato"}

        current_df = self._applica_group_by(current_df, result_node)

        # Snapshot intermediate.db per il nodo result (non bloccante).
        # Cap a 10.000 righe: lo snapshot serve per inspection/confronto run,
        # non per riprodurre il dataset intero. Senza cap, to_dict() su un
        # DataFrame con milioni di righe crea una lista di milioni di dict
        # in memoria → OOM.
        _MAX_INTERMEDIATE_SNAPSHOT_ROWS = 10_000
        if run_id and current_df is not None:
            try:
                from app.core.intermediate.writer import write_node as _write_node
                snapshot_df = current_df.head(_MAX_INTERMEDIATE_SNAPSHOT_ROWS)
                _col_schema = current_df.dtypes.astype(str).to_dict()
                _write_node(
                    run_id=run_id,
                    node_id=result_node.id,
                    node_type="result",
                    rows=snapshot_df.to_dict(orient="records"),
                    col_schema=_col_schema,
                    exec_time=0.0,
                    status="ok",
                )
            except Exception as _iex:
                logger.warning(f"Intermediate write result node fallita (non bloccante): {_iex}")

        res = self._salva_risultato(project_id, result_node, current_df, canvas)

        # Allinea columns_metadata del progetto allo schema reale del DataFrame finale.
        # Fix desincronizzazione UI/DB del nodo result — l'output file è corretto ma
        # columns_metadata restava stale e rompeva il riuso come project_source.
        if res.get("success"):
            try:
                _col_meta = [
                    {"name": c, "type": str(current_df[c].dtype)} for c in current_df.columns
                ]
                self.project_service.update_project_columns_metadata(project_id, _col_meta)
            except Exception as _cme:
                logger.warning(f"Impossibile salvare columns_metadata: {_cme}")

        # --- Step tracking ---
        append_step({
            "module":      "manipola",
            "status":      "success" if res.get("success") else "failure",
            "rows_out":    res.get("rows_count", 0) or 0,
            "duration_ms": int((_time.time() - _step_start) * 1000),
            "error":       res.get("message") if not res.get("success") else None,
        })
        return res

    # =========================================================================
    # ANTEPRIMA
    # =========================================================================

    def preview(self, project_id: str, canvas: CanvasState, limit: int) -> Dict[str, Any]:
        """Preview: stessa pipeline dell'execute ma senza salvataggio."""
        logger.info(f"Preview MANIPOLA: {project_id}", op="preview", project_id=project_id)
        try:
            dataframes = {}
            self._carica_sorgenti_progetto(canvas, dataframes, force_recalculate=False)
            self._carica_sorgenti_file(canvas, dataframes)
            self._carica_sorgenti_statiche(canvas, dataframes)

            if not dataframes:
                return {"success": False, "message": "Nessun dato sorgente disponibile"}

            current_df = self._applica_trasformazioni(canvas, dataframes)
            if current_df is None:
                return {"success": False, "message": "Nessun dato prodotto dalla pipeline"}

            result_node = next((n for n in canvas.nodes if n.type == "result"), None)
            if result_node:
                current_df = self._applica_group_by(current_df, result_node)

            total_count = len(current_df)
            truncated = total_count > limit
            preview_df = current_df.head(limit)

            canvas_type_map = self._costruisci_mappa_tipi_canvas(canvas)
            columns = self._costruisci_colonne_con_tipi(preview_df, canvas_type_map)
            rows = sanitize_for_json(preview_df.to_dict(orient='records'))

            return {
                "success": True, "columns": columns, "rows": rows,
                "total_count": total_count, "preview_count": len(rows),
                "truncated": truncated
            }
        except Exception as e:
            logger.warning(f"[GESTITO] Errore preview MANIPOLA: {e}", op="preview")
            return {"success": False, "message": str(e)}

    # =========================================================================
    # CARICAMENTO SORGENTI
    # =========================================================================

    def _carica_sorgenti_progetto(self, canvas, dataframes, force_recalculate, run_id: str = None):
        """Carica DataFrame dai progetti sorgente (type=project_source).

        SECURITY: verifica che ogni source_project_id appartenga allo stesso
        gruppo del progetto corrente. Senza questo check, un utente potrebbe
        inserire nel canvas un source_project_id di un altro tenant e
        esfiltrare i dati durante l'esecuzione della pipeline.
        """
        source_nodes = [n for n in canvas.nodes if n.type == "project_source"]

        for node in source_nodes:
            source_project_id = _nf(node, 'source_project_id')
            if not source_project_id:
                logger.warning(f"Nodo {node.id}: source_project_id mancante")
                continue

            # Cross-tenant isolation: il progetto sorgente deve appartenere
            # allo stesso gruppo del progetto corrente. Questa è la difesa
            # a livello executor (defense-in-depth) — il canvas potrebbe
            # contenere ID arbitrari inseriti da un attaccante.
            try:
                import app.db.db_service as _db
                # Recupera group_id del progetto corrente dal run_id context
                from app.utils.logger import get_run_id as _get_run_id
                _current_run_id = run_id or _get_run_id()
                # Fallback: cerca il progetto che sta eseguendo tramite il primo
                # nodo non-source nel canvas
                _executing_project_group = None
                if hasattr(self, '_executing_group_id'):
                    _executing_project_group = self._executing_group_id
                else:
                    # Cerca il progetto corrente dal canvas context
                    # Il project_id è passato a execute() → salvalo come attributo
                    _executing_project_group = getattr(self, '_current_group_id', None)

                if _executing_project_group:
                    _source_row = _db.get_project(source_project_id)
                    if _source_row:
                        _source_group = _source_row.get("group_id", "")
                        if _source_group != _executing_project_group:
                            logger.error(
                                f"[SECURITY] Cross-tenant access blocked: progetto {source_project_id} "
                                f"(group={_source_group}) referenziato da progetto di group={_executing_project_group}",
                                op="execute"
                            )
                            return {
                                "success": False,
                                "message": f"Accesso negato: il progetto sorgente '{source_project_id}' "
                                           f"appartiene a un gruppo diverso."
                            }
            except Exception as _sec_err:
                logger.warning(f"Cross-tenant check fallito (non bloccante): {_sec_err}")

            # Cerca risultato nel DB (preferito) o filesystem (fallback)
            if not force_recalculate:
                cached_file = get_result_as_tempfile(source_project_id)
                if not cached_file:
                    # fallback filesystem pre-migrazione
                    output_dir = self.results_base_dir / source_project_id
                    cached_file = find_latest_output_file(output_dir)
                if cached_file:
                    try:
                        df = load_dataframe_from_file(cached_file)
                        node_columns = _nf(node, 'columns', [])
                        df = filter_visible_columns(df, node_columns)
                        # Deduplicazione colonne: mantieni prima occorrenza
                        df = df.loc[:, ~df.columns.duplicated(keep='first')]
                        # Preserva CRS: se il nodo ha colonne geometriche con SRID,
                        # converte subito in GeoDataFrame con CRS corretto
                        # così le trasformazioni spaziali downstream non perdono il CRS
                        df = _applica_crs_da_colonne(df, node_columns)
                        dataframes[node.id] = df
                        try:
                            import os as _os
                            _os.unlink(str(cached_file))
                        except Exception as _e:
                            # Warning (non debug): se la pulizia fallisce
                            # ripetutamente accumula file temp sul disco e
                            # serve un alert visibile per gli admin.
                            logger.warning(
                                f"Pulizia file temp fallita per {cached_file}: {_e}"
                            )
                        continue
                    except Exception as e:
                        logger.warning(f"Errore cache: {e}, procedo con ricalcolo")

            if not self.orchestrator_callback:
                return {"success": False, "message": "Orchestrator callback non configurato"}

            # Genera un run_id fresco per il progetto upstream (ESTRAI),
            # così i suoi dati intermedi vengono tracciati in intermediate.db
            import uuid as _uuid
            upstream_run_id = str(_uuid.uuid4())
            try:
                from app.utils.logger import open_run
                from app.core.intermediate import db as idb
                import app.db.config_db_service as cfg_db
                upstream_meta = cfg_db.get_project(source_project_id) or {}
                open_run(
                    run_id=upstream_run_id,
                    project_id=source_project_id,
                    group_id=upstream_meta.get("group_id", ""),
                    module=upstream_meta.get("project_type", "estrai"),
                    triggered_by="cascade",
                )
                idb.create_run(
                    run_id=upstream_run_id,
                    canvas_id=source_project_id,
                    group_id=upstream_meta.get("group_id", ""),
                )
            except Exception as _oe:
                logger.warning(f"Apertura run upstream {source_project_id} fallita (non bloccante): {_oe}")
                upstream_run_id = None

            upstream = self.orchestrator_callback(source_project_id, force_recalculate=True, run_id=upstream_run_id)

            # Chiudi run upstream
            if upstream_run_id:
                try:
                    import time as _t
                    from app.utils.logger import close_run
                    from app.core.intermediate.writer import close_run as idb_close_run
                    status = "success" if upstream.get("success") else "failure"
                    close_run(
                        run_id=upstream_run_id,
                        status=status,
                        rows_out=upstream.get("rows_count", 0) or 0,
                        duration_ms=0,
                        steps=[],
                        error_message=upstream.get("message") if not upstream.get("success") else None,
                    )
                    idb_close_run(
                        run_id=upstream_run_id, input_hash="", output_hash="",
                        total_exec_time=0.0, success=upstream.get("success", False),
                    )
                except Exception as _ce:
                    logger.warning(f"Chiusura run upstream {source_project_id} fallita (non bloccante): {_ce}")
            if not upstream.get("success"):
                return upstream

            # Legge il risultato upstream: prima dal DB, poi dal path temporaneo restituito
            file_path = None
            raw_output_path = upstream.get("output_path", "")
            if raw_output_path and Path(raw_output_path).exists():
                file_path = Path(raw_output_path)
            
            if not file_path:
                # Fallback: leggi dal DB
                file_path = get_result_as_tempfile(source_project_id)

            if not file_path or not file_path.exists():
                return {"success": False, "message": f"Risultato upstream non trovato per: {source_project_id}"}

            try:
                df = load_dataframe_from_file(file_path)
                node_columns = _nf(node, 'columns', [])
                df = filter_visible_columns(df, node_columns)
                # Deduplicazione colonne: mantieni prima occorrenza
                df = df.loc[:, ~df.columns.duplicated(keep='first')]
                # Preserva CRS: se il nodo ha colonne geometriche con SRID,
                # converte subito in GeoDataFrame con CRS corretto
                df = _applica_crs_da_colonne(df, node_columns)
                dataframes[node.id] = df
                # Pulizia temp file se generato da DB
                try:
                    import os as _os
                    if "urby_cached_" in str(file_path):
                        _os.unlink(str(file_path))
                except Exception as _e:
                    logger.debug(f"Pulizia file temp fallita (non critico): {_e}")
            except Exception as e:
                return {"success": False, "message": f"Errore progetto {source_project_id}: {e}"}

        return None

    def _carica_sorgenti_file(self, canvas, dataframes):
        """Carica DataFrame dai nodi file_source. Preferisce GeoDataFrame se disponibile."""
        file_nodes = [n for n in canvas.nodes if n.type == "file_source"]
        for node in file_nodes:
            fid = _nf(node, 'file_source_id')
            if not fid:
                continue
            try:
                from app.services.file_service import file_source_service
                df, gdf = file_source_service.load_dataframe(fid)
                data = gdf if gdf is not None else df
                node_columns = _nf(node, 'columns', [])
                data = filter_visible_columns(data, node_columns)
                dataframes[node.id] = data
            except Exception as e:
                return {"success": False, "message": f"Errore file '{fid}': {e}"}
        return None

    def _carica_sorgenti_statiche(self, canvas, dataframes):
        """Carica DataFrame dai nodi StaticValues (1 riga con valori definiti dall'utente)."""
        static_nodes = [n for n in canvas.nodes if n.type == "staticValues"]
        for node in static_nodes:
            try:
                fields = _nf(node, 'fields', [])
                if not fields:
                    dataframes[node.id] = pd.DataFrame([{}])
                    continue
                processed_values = process_static_fields(fields)
                dataframes[node.id] = pd.DataFrame([processed_values])
            except Exception as e:
                return {"success": False, "message": f"Errore StaticValues: {e}"}
        return None

    # =========================================================================
    # TRASFORMAZIONI
    # =========================================================================

    def _applica_trasformazioni(self, canvas, dataframes, run_id: str = None):
        """
        Applica le trasformazioni del canvas in ordine topologico.
        Supporta sia nodi 'transform' che 'multi_join'.
        Se run_id è presente, scrive uno snapshot intermediate per ogni nodo.
        """
        # Prendi sia transform che multi_join
        process_nodes = [n for n in canvas.nodes if n.type in ("transform", "multi_join")]
        
        if not process_nodes:
            return self._gestisci_join_default(dataframes)
        
        # Ordina topologicamente i nodi
        sorted_nodes = self._ordina_topologicamente(process_nodes, canvas.edges, dataframes)
        
        current_df = None
        # Mappa nodi per accedere alle posizioni
        node_map_all = {n.id: n for n in canvas.nodes}
        
        # Helper per estrarre targetHandle dall'edge (da attributo o da ID)
        def get_target_handle(edge):
            # Prima prova attributo diretto
            handle = getattr(edge, 'targetHandle', None)
            if handle:
                return handle
            # Estrai da ID: "...targetNodeIdinput-0" → "input-0"
            edge_id = getattr(edge, 'id', '') or ''
            match = re.search(r'(input-\d+)$', edge_id)
            if match:
                return match.group(1)
            return None
        
        for node in sorted_nodes:
            # Trova input per questo nodo - ORDINA per targetHandle o posizione Y
            incoming = [e for e in canvas.edges if e.target == node.id]
            
            # Ordina edges per garantire ordine consistente left/right
            def edge_sort_key(edge):
                # Prima prova targetHandle (da attributo o ID)
                handle = get_target_handle(edge)
                if handle:
                    return (0, handle)
                # Fallback: posizione Y del nodo sorgente
                source_node = node_map_all.get(edge.source)
                if source_node and hasattr(source_node, 'position'):
                    pos = source_node.position
                    y = pos.get('y', 0) if isinstance(pos, dict) else getattr(pos, 'y', 0)
                    return (1, y)
                return (2, edge.source)
            
            incoming.sort(key=edge_sort_key)
            inputs = [dataframes[e.source] for e in incoming if e.source in dataframes]
            
            # Log dettagliato per operazioni multi-input
            if len(incoming) > 1:
                for idx, edge in enumerate(incoming):
                    handle = get_target_handle(edge)
                    source_cols = list(dataframes.get(edge.source, {}).columns)[:5] if edge.source in dataframes else []
            
            if not inputs:
                logger.warning(f"Nodo {node.id} senza input, skip")
                continue
            
            if node.type == "multi_join":
                # Esegui multi-join
                current_df = self._esegui_multi_join(node, inputs)
                logger.info(f"Multi-join: {len(inputs)} input -> {len(current_df)} righe", op="transform")
            else:
                # Trasformazione normale
                current_df = self._esegui_trasformazione(node, inputs)
            
            # Salva risultato per nodi successivi
            dataframes[node.id] = current_df

            # Snapshot intermediate.db (non bloccante)
            if run_id and current_df is not None:
                try:
                    import time as _t
                    from app.core.intermediate.writer import write_node as _write_node
                    _col_schema = current_df.dtypes.astype(str).to_dict()
                    _write_node(
                        run_id=run_id,
                        node_id=node.id,
                        node_type=node.type,
                        rows=current_df.to_dict(orient="records"),
                        col_schema=_col_schema,
                        exec_time=0.0,
                        status="ok",
                    )
                except Exception as _iex:
                    logger.warning(f"Intermediate write nodo {node.id} fallita (non bloccante): {_iex}")
        
        return current_df
    
    def _ordina_topologicamente(self, nodes, edges, dataframes):
        """Ordina i nodi in ordine topologico (Kahn's algorithm). Rileva cicli."""
        node_ids = {n.id for n in nodes}
        node_map = {n.id: n for n in nodes}

        # Costruisci grafo
        in_degree = {n.id: 0 for n in nodes}
        out_edges_map = {n.id: [] for n in nodes}

        for edge in edges:
            if edge.target in node_ids:
                if edge.source in node_ids:
                    in_degree[edge.target] += 1
                    out_edges_map[edge.source].append(edge.target)

        # Nodi pronti (in_degree == 0 significa che dipendono solo da sorgenti)
        queue = [nid for nid, deg in in_degree.items() if deg == 0]

        sorted_ids = []
        while queue:
            nid = queue.pop(0)
            sorted_ids.append(nid)

            for target_id in out_edges_map.get(nid, []):
                in_degree[target_id] -= 1
                if in_degree[target_id] == 0:
                    queue.append(target_id)

        # Ciclo rilevato: alcuni nodi non sono stati raggiunti
        if len(sorted_ids) != len(nodes):
            cycle_nodes = [nid for nid in node_ids if nid not in set(sorted_ids)]
            raise ValueError(
                f"Ciclo rilevato nel canvas tra i nodi: {cycle_nodes}. "
                "Rimuovere le connessioni circolari prima di eseguire la pipeline."
            )

        return [node_map[nid] for nid in sorted_ids]
    
    def _esegui_multi_join(self, node, inputs):
        """
        Esegue multi-join su N DataFrame.
        
        Strategia:
        1. Prende il primo DataFrame come base
        2. Fa merge sequenziale con gli altri
        3. Gestisce colonne duplicate
        """
        if len(inputs) < 2:
            logger.warning("Multi-join richiede almeno 2 input")
            return inputs[0] if inputs else pd.DataFrame()
        
        join_keys = _nf(node, 'join_keys') or (node.data or {}).get('config', {}).get('join_keys', [])
        join_type = _nf(node, 'multi_join_type') or (node.data or {}).get('config', {}).get('join_type', 'inner')
        
        if not join_keys:
            logger.warning("Multi-join senza chiavi specificate, cerco colonne comuni")
            # Trova colonne comuni a tutti i DataFrame
            common_cols = set(inputs[0].columns)
            for df in inputs[1:]:
                common_cols &= set(df.columns)
            join_keys = list(common_cols)
            
            if not join_keys:
                logger.warning("Nessuna colonna comune trovata per il join")
                return inputs[0]
        
        # Verifica che le chiavi esistano in tutti i DataFrame
        for idx, df in enumerate(inputs):
            missing = [k for k in join_keys if k not in df.columns]
            if missing:
                logger.warning(f"Input {idx}: chiavi mancanti {missing}")
                return inputs[0]
        
        
        # Inizia con il primo DataFrame
        result = inputs[0].copy()
        
        # Merge sequenziale con gli altri
        for idx, df in enumerate(inputs[1:], start=2):
            df_copy = df.copy()
            
            # Trova colonne duplicate (non-chiave)
            result_cols = set(result.columns) - set(join_keys)
            df_cols = set(df_copy.columns) - set(join_keys)
            duplicates = result_cols & df_cols
            
            if duplicates:
                # Rinomina nel df corrente
                rename_map = {col: f"{col}_{idx}" for col in duplicates}
                df_copy = df_copy.rename(columns=rename_map)
                logger.info(f"Rinominate colonne: {list(rename_map.keys())}")
            
            # Merge
            result = pd.merge(
                result,
                df_copy,
                on=join_keys,
                how=join_type
            )
            
            logger.info(f"Dopo merge #{idx}: {len(result)} righe, {len(result.columns)} col")
        
        return result

    def _gestisci_join_default(self, dataframes):
        """Singola sorgente: passthrough. Multiple sorgenti senza join: errore."""
        if not dataframes:
            raise ValueError("Nessun progetto sorgente configurato")
        if len(dataframes) == 1:
            return list(dataframes.values())[0]
        raise ValueError(f"{len(dataframes)} sorgenti ma nessun nodo Transform.")

    def _esegui_trasformazione(self, node, inputs):
        """Delega al PluginExecutor. Rilancia l'eccezione in caso di errore (no silent fail)."""
        transform_op = _nf(node, 'transform_op')
        if not transform_op:
            return inputs[0] if inputs else pd.DataFrame()
        return get_executor().execute(transform_op, inputs, node)

    # =========================================================================
    # GROUP BY
    # =========================================================================

    def _applica_group_by(self, df, result_node):
        """Applica GROUP BY dal nodo risultato (count, sum, mean, min, max, std)."""
        if not _nf(result_node, 'group_by_enabled', False):
            return df
        group_cols = _nf(result_node, 'group_by_columns', [])
        aggregations = _nf(result_node, 'aggregations', [])
        if not group_cols and not aggregations:
            return df

        try:
            group_cols = [c for c in group_cols if c in df.columns]
            if not group_cols:
                return df

            # Separa aggregazioni count da quelle su colonne specifiche
            count_aliases = []
            agg_dict, rename_dict = {}, {}

            for agg in aggregations:
                col  = agg.get('column')
                func = agg.get('function', 'count')
                alias = agg.get('alias', f"{col}_{func}")

                if func == 'count':
                    # FIX B-BE-MANIPOLA-01: COUNT(*) tramite size() — non
                    # dipende da nessuna colonna, non perde il gruppo
                    count_aliases.append(alias)
                elif func in ['sum', 'mean', 'min', 'max', 'std']:
                    if col in df.columns:
                        agg_dict[col] = func
                        rename_dict[col] = alias

            # Esegui aggregazioni colonna (solo se ci sono colonne valide)
            if agg_dict:
                result = df.groupby(group_cols, as_index=False).agg(agg_dict)
                result = result.rename(columns=rename_dict)
            elif count_aliases:
                # Solo COUNT(*) richiesti — parti da una base con i gruppi
                result = df.groupby(group_cols, as_index=False).size().drop(columns=['size'])
            else:
                # Nessuna aggregazione valida
                return df

            # Aggiungi COUNT(*) per ogni richiesta
            for alias in count_aliases:
                result[alias] = df.groupby(group_cols).size().values

            return result

        except Exception as e:
            logger.error(f"[CRASH] Errore GROUP BY: {e}", exc_info=True)
            return df

    # =========================================================================
    # SALVATAGGIO
    # =========================================================================

    def _salva_risultato(self, project_id, result_node, df, canvas=None):
        """Salva il risultato finale nel formato richiesto."""
        filename = _nf(result_node, 'fileName', 'output')
        fmt      = _nf(result_node, 'format',   'csv')
        geom_col = _nf(result_node, 'geomColumn')

        if geom_col is None:
            geom_col = detect_geometry_column(df)

        crs = "EPSG:4326"
        if canvas:
            srid = self._estrai_srid_da_canvas(canvas)
            if srid:
                crs = f"EPSG:{srid}"

        output_path = get_temp_output_path(f"{filename}.{fmt}")
        try:
            # Leggi group_id del progetto per isolamento corretto dei risultati
            group_id = None
            try:
                metadata = self.project_service.get_project_metadata(project_id)
                if metadata:
                    group_id = getattr(metadata, 'group_id', None)
            except Exception:
                pass

            # save_dataframe restituisce il path effettivo (es. .zip per shp)
            saved_path = save_dataframe(df, output_path, fmt, geom_col, crs)
            actual_fmt = saved_path.suffix.lstrip(".") if fmt == "shp" else fmt
            save_result_blob(project_id, saved_path, actual_fmt, len(df), group_id=group_id)
            return {
                "success": True, "output_path": str(saved_path), "format": actual_fmt,
                "rows_count": len(df), "columns_count": len(df.columns),
                "message": f"Salvato {len(df)} righe in {saved_path.name}"
            }
        except Exception as e:
            return {"success": False, "message": f"Errore salvataggio: {e}"}

    def _estrai_srid_da_canvas(self, canvas):
        """Estrae primo SRID da tipi geometry nel canvas (es. geometry:point:4326)."""
        for node in canvas.nodes:
            columns = getattr(node, 'columns', None) or (node.data.get('columns') if node.data else None)
            if not columns:
                continue
            for col in columns:
                col_type = col.get('type', '') if isinstance(col, dict) else getattr(col, 'type', '')
                srid = extract_srid_from_column_type(col_type)
                if srid:
                    return srid
        return None

    # =========================================================================
    # MAPPA TIPI CANVAS
    # =========================================================================

    def _costruisci_mappa_tipi_canvas(self, canvas):
        """Costruisce mappa colonna->tipo dal canvas. Include suffissi _L/_R per JOIN."""
        m = {}
        for node in canvas.nodes:
            columns = getattr(node, 'columns', None) or (node.data.get('columns') if node.data else None)
            if columns:
                for col in columns:
                    if isinstance(col, dict):
                        cn = col.get('alias') or col.get('name')
                        ct, on = col.get('type', 'str'), col.get('name')
                    else:
                        cn = col.alias if hasattr(col, 'alias') and col.alias else col.name
                        ct = col.type if hasattr(col, 'type') and col.type else "str"
                        on = col.name if hasattr(col, 'name') else None
                    if cn:
                        m[cn] = m[f"{cn}_L"] = m[f"{cn}_R"] = ct
                    if on and on != cn:
                        m[on] = m[f"{on}_L"] = m[f"{on}_R"] = ct

            calculated = getattr(node, 'calculated_columns', None)
            if calculated:
                for calc in calculated:
                    cn = calc.get("name") if isinstance(calc, dict) else getattr(calc, 'name', None)
                    ct = (calc.get("output_type", "str") if isinstance(calc, dict)
                          else getattr(calc, 'output_type', 'str'))
                    if cn:
                        m[cn] = m[f"{cn}_L"] = m[f"{cn}_R"] = ct
        return m

    def _costruisci_colonne_con_tipi(self, df, canvas_type_map):
        """Costruisce lista colonne con tipi. Lookup: esatto → lower → base _L/_R → dtype → WKT."""
        columns = []
        lower_map = {k.lower(): v for k, v in canvas_type_map.items()}
        for col in df.columns:
            ct = canvas_type_map.get(col) or lower_map.get(col.lower())
            if ct is None and (col.endswith('_L') or col.endswith('_R')):
                base = col[:-2]
                ct = canvas_type_map.get(base) or lower_map.get(base.lower())
            
            # Se il tipo è str ma il nome suggerisce geometry, verifica il contenuto
            if ct == 'str' and col.lower() in ('geometry', 'geom', 'the_geom', 'shape'):
                ct = self._rileva_geometria_da_contenuto(df, col)
            
            if ct is None:
                dtype = str(df[col].dtype)
                if 'int' in dtype: ct = 'int'
                elif 'float' in dtype: ct = 'real'
                elif 'bool' in dtype: ct = 'bool'
                elif 'datetime' in dtype: ct = 'date'
                elif 'geometry' in dtype.lower(): ct = 'geometry'  # GeoDataFrame dtype
                else: ct = self._rileva_geometria_da_contenuto(df, col)
            columns.append({'name': col, 'type': ct})
        return columns

    def _rileva_geometria_da_contenuto(self, df, col_name):
        """Rileva geometria WKT o oggetti Shapely dal contenuto di una colonna."""
        try:
            # Check nome colonna comune per geometry
            if col_name.lower() in ('geometry', 'geom', 'the_geom', 'shape'):
                sample = df[col_name].dropna().head(1)
                if not sample.empty:
                    val = sample.iloc[0]
                    # Se ha geom_type o è WKT, è geometry
                    if hasattr(val, 'geom_type'):
                        return 'geometry'
                    val_str = str(val).upper().strip()
                    if val_str and val_str[0] in 'PLMG':  # Quick check for WKT prefixes
                        return 'geometry'
            
            sample = df[col_name].dropna().head(1)
            if sample.empty:
                return 'str'
            val = sample.iloc[0]
            
            # Controlla se è un oggetto Shapely
            if hasattr(val, 'geom_type'):
                return 'geometry'
            
            # Controlla se è WKT string
            val_str = str(val).upper().strip()
            prefixes = ('POINT', 'LINESTRING', 'POLYGON', 'MULTIPOINT',
                        'MULTILINESTRING', 'MULTIPOLYGON', 'GEOMETRYCOLLECTION')
            return 'geometry' if any(val_str.startswith(p) for p in prefixes) else 'str'
        except Exception:
            return 'str'