"""
Logica di parallelizzazione e conditional execution per api_enrich nodes.

Questo modulo contiene le funzioni helper utilizzate da:
- api_connector.py (execute_enrichment, sync)
- enrichment_task.py (Celery task, async)
- executor.py (preview, sync)

Due feature:
1. execute_if: skip condizionale di un nodo in base a una colonna del DataFrame
2. parallel groups: raggruppamento di nodi api_enrich indipendenti per
   esecuzione concorrente con ThreadPoolExecutor

Usato internamente — non esposto come API.
"""
import re
from typing import Any, Dict, List, Optional, Set, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd

from app.utils.logger import get_logger

logger = get_logger(__name__)


# ============================================================
# EXECUTE_IF — conditional skip
# ============================================================

def _parse_execute_if(node_data: Dict):
    """Parsa la condizione execute_if e ritorna (col, expected, negate) o None se assente."""
    condition = node_data.get("execute_if")
    if not condition:
        condition = (node_data.get("data") or {}).get("execute_if")
    if not condition or not isinstance(condition, str):
        return None
    negate = False
    if "!=" in condition:
        col, _, expected = condition.partition("!=")
        negate = True
    elif "=" in condition:
        col, _, expected = condition.partition("=")
    else:
        return None
    return col.strip(), expected.strip().lower(), negate


def get_execute_mask(node_data: Dict, input_df: Any) -> pd.Series:
    """Ritorna una maschera booleana per-riga del DataFrame.

    Per ogni riga: True se il nodo deve essere eseguito su quella riga,
    False se deve essere skippato. Se non c'e condizione execute_if,
    tutte le righe sono True.

    Usato per split-execute-merge: le righe True vengono arricchite,
    le False passano inalterate (colonne output = None).
    """
    parsed = _parse_execute_if(node_data)
    if parsed is None:
        # Nessuna condizione → tutte le righe
        return pd.Series(True, index=input_df.index) if isinstance(input_df, pd.DataFrame) else True

    col, expected, negate = parsed
    if not isinstance(input_df, pd.DataFrame) or col not in input_df.columns:
        return pd.Series(negate, index=input_df.index) if isinstance(input_df, pd.DataFrame) else negate

    match = input_df[col].astype(str).str.strip().str.lower() == expected
    return (~match) if negate else match


def should_execute_node(node_data: Dict, input_df: Any) -> bool:
    """Valuta la condizione `execute_if` di un nodo api_enrich.

    Sintassi di execute_if:
      - "colonna=valore"   → True se input_df[colonna].iloc[0] == valore
      - "colonna!=valore"  → True se input_df[colonna].iloc[0] != valore
      - None / assente     → True (sempre eseguire)

    Per decisione per-riga usare `get_execute_mask()` che ritorna un
    pd.Series booleano. Questa funzione controlla solo la prima riga
    (backward compat per preview e Celery task dove il check e globale).
    """
    parsed = _parse_execute_if(node_data)
    if parsed is None:
        return True

    col, expected, negate = parsed

    if not isinstance(input_df, pd.DataFrame) or col not in input_df.columns or len(input_df) == 0:
        return negate  # colonna assente o df vuoto: condizione = falsa (o vera se negata)

    actual = str(input_df[col].iloc[0]).strip().lower()
    match = actual == expected
    return (not match) if negate else match


# ============================================================
# PARALLEL GROUPS — raggruppamento nodi indipendenti
# ============================================================

def _extract_consumed_columns(node_data: Dict) -> Set[str]:
    """Estrae le colonne del DataFrame che un nodo api_enrich effettivamente legge.

    Analizza param_mapping:
      - "X"       → colonna "X"
      - "{X},{Y}" → colonne "X", "Y" (template syntax)
    """
    pm = node_data.get("param_mapping") or (node_data.get("data") or {}).get("param_mapping") or {}
    cols = set()
    for _, mapping_value in pm.items():
        if not mapping_value or not isinstance(mapping_value, str):
            continue
        templates = re.findall(r"\{(\w+)\}", mapping_value)
        if templates:
            cols.update(templates)
        else:
            cols.add(mapping_value.strip())
    return cols


def _extract_produced_columns(node_data: Dict) -> Set[str]:
    """Estrae le colonne che un nodo api_enrich aggiunge al DataFrame."""
    om = node_data.get("output_mapping") or (node_data.get("data") or {}).get("output_mapping") or []
    return {m.get("column", "") for m in om if m.get("column")}


def compute_parallel_groups(
    execution_order: List[str],
    nodes: List[Dict],
    edges: List[Dict],
    api_connections: Optional[Dict[str, Dict]] = None,
) -> List[Tuple[str, List[str]]]:
    """Raggruppa i nodi dell'execution_order in step sequenziali o paralleli.

    Ritorna una lista di (step_type, [node_ids]):
      - ("single", [node_id])      → nodo sorgente/result, eseguire singolarmente
      - ("parallel", [node_ids])   → gruppo di api_enrich eseguibili in parallelo

    **Check di raggruppamento**: un nodo api_enrich B va nel batch corrente
    se NESSUNA delle sue colonne lette (param_mapping) e' prodotta da un
    nodo gia' nel batch. Il check e' solo sulle COLONNE, non sul grafo.

    Motivazione: in molti canvas la topologia e' una catena lineare
    (static → A → B → C → ...) anche quando logicamente i nodi api_enrich
    sono indipendenti — ciascuno aggiunge colonne disgiunte leggendo sempre
    dallo stesso set iniziale (es. X, Y dal catasto). Se rispettassimo il
    DAG, questi pattern finirebbero in batch da 1 e perderemmo qualunque
    parallelismo. La semantica "tutti i nodi del batch condividono lo
    stesso input_df" e' implementata dal caller (vedi task Celery:
    _run_single_node con input_override).

    **File-processor** (`connection_type == 'rest_file'`):
    non parallelizzabili col modello fan-out degli enricher. Quando
    `api_connections` e' fornito, i nodi `api_enrich` con connector di tipo
    file-processor vengono emessi come step ("single", [nid]) separato:
    flush del batch corrente, emissione del nodo singolo, ripartenza
    eventuale di un nuovo batch dopo. Questo garantisce che il
    file-processor veda il `node_results[upstream]` gia' popolato col
    DataFrame mergiato del batch precedente — condizione necessaria per
    la materializzazione upstream DataFrame→file fatta in
    `_resolve_input_path_for_file_processor` (api_connector.py).

    Se `api_connections` non e' passato, il comportamento e' quello legacy
    (file-processor trattati come gli altri enricher — fa crashare il
    chaining enricher→rest_file ma resta compat per i call site non ancora
    aggiornati).

    Il parametro `edges` e' accettato per compat ma non usato — potrebbe
    servire in futuro per check di safety opzionali.
    """
    node_map = {n["id"]: n for n in nodes}

    # Pre-compute produced columns per nodo
    produced = {}
    for n in nodes:
        if n.get("type") == "api_enrich":
            produced[n["id"]] = _extract_produced_columns(n)

    # Pre-compute consumed columns per nodo
    consumed = {}
    for n in nodes:
        if n.get("type") == "api_enrich":
            consumed[n["id"]] = _extract_consumed_columns(n)

    groups: List[Tuple[str, List[str]]] = []
    current_batch: List[str] = []
    current_batch_produces: Set[str] = set()

    def _is_file_processor(n: Dict) -> bool:
        """True se il nodo e' api_enrich con connector rest_file.

        Richiede `api_connections` nel closure: senza, ritorna sempre False
        (compat legacy). `api_id` puo' stare al root del nodo o dentro `data`.
        """
        if api_connections is None or n.get("type") != "api_enrich":
            return False
        api_id = n.get("api_id") or (n.get("data") or {}).get("api_id")
        if not api_id:
            return False
        cfg = api_connections.get(api_id) or {}
        return cfg.get("connection_type") == "rest_file"

    for node_id in execution_order:
        node = node_map.get(node_id)
        if not node:
            continue
        ntype = node.get("type")

        # File-processor: non batchabile. Flush del batch corrente e
        # emissione come single, cosi' a runtime riceve il DataFrame
        # mergiato del batch precedente via node_results[upstream].
        if _is_file_processor(node):
            if current_batch:
                groups.append(("parallel", list(current_batch)))
                current_batch = []
                current_batch_produces = set()
            groups.append(("single", [node_id]))
            continue

        if ntype != "api_enrich":
            # Flush batch corrente
            if current_batch:
                groups.append(("parallel", list(current_batch)))
                current_batch = []
                current_batch_produces = set()
            groups.append(("single", [node_id]))
        else:
            # api_enrich: check solo su colonne prodotte dal batch corrente
            node_consumes = consumed.get(node_id, set())
            depends_on_batch = bool(node_consumes & current_batch_produces)

            if depends_on_batch:
                # Flush batch, start nuovo
                groups.append(("parallel", list(current_batch)))
                current_batch = [node_id]
                current_batch_produces = produced.get(node_id, set())
            else:
                current_batch.append(node_id)
                current_batch_produces |= produced.get(node_id, set())

    if current_batch:
        groups.append(("parallel", list(current_batch)))

    return groups


# ============================================================
# ESECUZIONE PARALLELA DI UN GRUPPO
# ============================================================

def execute_parallel_enrichers(
    node_ids: List[str],
    input_df: pd.DataFrame,
    node_map: Dict[str, Dict],
    api_connections: Dict[str, Dict],
    execute_single_fn,
    max_workers: int = 10,
) -> Tuple[Dict[str, pd.DataFrame], Optional[str]]:
    """Esegue un gruppo di nodi api_enrich in parallelo sullo stesso input_df.

    Args:
        node_ids: lista di node_id da eseguire
        input_df: DataFrame di input comune a tutti i nodi
        node_map: {node_id: node_dict}
        api_connections: {api_id: api_config}
        execute_single_fn: callable(node_data, api_config, input_df) -> DataFrame
            Funzione che esegue un singolo nodo enricher (gia esistente nel caller).
        max_workers: max thread concorrenti

    Returns:
        (results_dict, error_msg)
        - results_dict: {node_id: merged_dataframe} per tutti i nodi del gruppo
        - error_msg: None se ok, stringa errore se fallito
    """
    def run_one(node_id: str) -> Tuple[str, Optional[pd.DataFrame], Optional[str]]:
        node = node_map[node_id]

        # Per-row execute_if: split → execute solo righe matching → merge
        mask = get_execute_mask(node, input_df)
        all_skip = not mask.any() if isinstance(mask, pd.Series) else not mask
        all_exec = mask.all() if isinstance(mask, pd.Series) else mask

        if all_skip:
            logger.info(f"Nodo {node_id} skippato per tutte le righe (execute_if)")
            return node_id, None, None

        try:
            if all_exec:
                # Tutte le righe matchano → esecuzione normale (no split)
                result_df = execute_single_fn(node, api_connections, input_df)
            else:
                # Split: esegui solo le righe che matchano, le altre passthrough
                df_exec = input_df[mask].copy()
                df_skip = input_df[~mask].copy()
                result_exec = execute_single_fn(node, api_connections, df_exec)
                # Aggiungi colonne mancanti alle righe skippate (None)
                if isinstance(result_exec, pd.DataFrame):
                    new_cols = [c for c in result_exec.columns if c not in df_skip.columns]
                    for c in new_cols:
                        df_skip[c] = None
                    result_df = pd.concat([result_exec, df_skip]).sort_index()
                else:
                    result_df = input_df  # fallback
                logger.info(f"Nodo {node_id}: {mask.sum()}/{len(mask)} righe eseguite (execute_if per-riga)")
            return node_id, result_df, None
        except Exception as e:
            logger.error(f"Errore parallelo nodo {node_id}: {e}", exc_info=True)
            return node_id, None, str(e)

    # Esecuzione
    node_results: Dict[str, Optional[pd.DataFrame]] = {}
    first_error = None

    if len(node_ids) <= 1:
        # Single node — niente threading overhead
        nid = node_ids[0]
        nid, res, err = run_one(nid)
        if err:
            first_error = err
        node_results[nid] = res
    else:
        import threading as _threading
        _lock = _threading.Lock()
        workers = min(len(node_ids), max_workers)
        logger.info(f"Esecuzione parallela: {len(node_ids)} nodi, {workers} workers")
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(run_one, nid): nid for nid in node_ids}
            for future in as_completed(futures):
                nid, res, err = future.result()
                with _lock:
                    if err and not first_error:
                        first_error = err
                    node_results[nid] = res

    # Non bloccare su errori singoli se continue_on_error e attivo
    # (il caller decide come gestire)

    # Merge: input_df + colonne nuove da ciascun nodo
    merged = input_df.copy()
    for nid in node_ids:
        result_df = node_results.get(nid)
        if result_df is None or not isinstance(result_df, pd.DataFrame):
            continue
        new_cols = [c for c in result_df.columns if c not in merged.columns]
        for c in new_cols:
            merged[c] = result_df[c].values

    # Tutti i nodi del gruppo puntano al merged (cosi il nodo successivo
    # nel DAG, che ha input_edge verso l'ultimo nodo del gruppo, trova
    # tutte le colonne accumulate)
    final_results = {nid: merged for nid in node_ids}

    return final_results, first_error
