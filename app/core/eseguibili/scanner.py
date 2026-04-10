"""
Scanner per estrarre le colonne attese da un canvas arricchisci.

Scansiona tutti i nodi `api_enrich` del canvas e raccoglie le colonne input
richieste dai param_mapping. Risolve la required-ness dalla config dell'API
(se disponibile).

Phase 1: supporta solo arricchisci. I nodi sorgente (project_source,
file_source, staticValues) sono "punti di ingresso" e non contribuiscono alle
colonne attese — verranno sostituiti a runtime dal file dell'utente.
"""

import re
from typing import Any, Dict, List, Set
from app.utils.logger import get_logger

logger = get_logger(__name__)


def _expand_mapping_columns(mapping_value: Any) -> List[str]:
    """Espande il valore di un param_mapping nelle colonne df che richiede.

    - "nome_colonna"   → ["nome_colonna"]
    - "{X},{Y}"        → ["X", "Y"]
    - ""/None/non str  → []
    """
    if not mapping_value or not isinstance(mapping_value, str):
        return []
    placeholders = re.findall(r"\{(\w+)\}", mapping_value)
    if placeholders:
        return placeholders
    return [mapping_value]


def extract_expected_columns(canvas: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Analizza un canvas arricchisci e ritorna la lista delle colonne attese.

    Args:
        canvas: canvas dict con 'nodes' e 'edges'

    Returns:
        Lista di dict con chiavi: name, type, required, used_by, description.
        Le colonne duplicate (usate da più nodi) sono unificate e marcate required
        se almeno un nodo le richiede come obbligatorie.

    Logica di esclusione:
        Una colonna usata in input da un nodo viene esclusa dalle expected_columns
        SOLO se è prodotta da un nodo *upstream* (che la precede nel DAG).
        Se è prodotta solo dal nodo stesso o da nodi downstream/laterali, è
        comunque attesa come input dell'utente (es. anno opzionale del nodo OMI).
    """
    from app.core.arricchisci.api_connector import api_connection_service

    nodes = canvas.get("nodes", []) or []
    edges = canvas.get("edges", []) or []

    # Carica tutte le config API una volta (cache locale)
    api_configs: Dict[str, Dict[str, Any]] = {}
    try:
        for api in api_connection_service.list_api_connections():
            full = api_connection_service.get_api_connection(api["id"], include_token=False)
            if full:
                api_configs[api["id"]] = full
    except Exception as e:
        logger.warning(f"Scanner: impossibile caricare API configs: {e}")

    # Calcola quali colonne sono "prodotte" da ogni api_enrich (output)
    produced_by_nodes: Dict[str, Set[str]] = {}
    for node in nodes:
        if node.get("type") == "api_enrich":
            nid = node.get("id")
            data = node.get("data") or {}
            raw_om = data.get("output_mapping") or node.get("output_mapping") or []
            produced_cols = {
                (m.get("column") or m.get("path", "")).split(".")[-1]
                for m in (raw_om or [])
                if m.get("column") or m.get("path")
            }
            produced_by_nodes[nid] = produced_cols

    # Costruisci grafo di adiacenza inversa: per ogni nodo, l'insieme dei nodi
    # da cui è raggiungibile (cioè i suoi antenati nel DAG).
    # adj_in[target] = lista di source_id collegati a target
    adj_in: Dict[str, List[str]] = {}
    for e in edges:
        src = e.get("source")
        tgt = e.get("target")
        if src and tgt:
            adj_in.setdefault(tgt, []).append(src)

    # Memo per ancestors via DFS
    _ancestors_cache: Dict[str, Set[str]] = {}

    def _ancestors(node_id: str) -> Set[str]:
        if node_id in _ancestors_cache:
            return _ancestors_cache[node_id]
        result: Set[str] = set()
        stack = list(adj_in.get(node_id, []))
        while stack:
            cur = stack.pop()
            if cur in result:
                continue
            result.add(cur)
            stack.extend(adj_in.get(cur, []))
        _ancestors_cache[node_id] = result
        return result

    # Dict di accumulo — {col_name: {type, required, used_by}}
    expected: Dict[str, Dict[str, Any]] = {}

    for node in nodes:
        if node.get("type") != "api_enrich":
            continue

        nid = node.get("id")
        data = node.get("data") or {}
        param_mapping = data.get("param_mapping") or node.get("param_mapping") or {}

        if not param_mapping:
            continue

        # Config API: per la required-ness e tipi
        api_id = data.get("api_id") or node.get("api_id")
        api_cfg = api_configs.get(api_id, {}) if api_id else {}
        api_params = api_cfg.get("parameters", []) or []
        param_cfg_map = {p.get("name"): p for p in api_params if isinstance(p, dict) and p.get("name")}

        # Colonne prodotte dagli ANTENATI di questo nodo (=upstream nel DAG)
        upstream_produced: Set[str] = set()
        for anc_id in _ancestors(nid):
            upstream_produced.update(produced_by_nodes.get(anc_id, set()))

        # Per ogni (param, mapping_value) nel mapping
        for param_name, mapping_value in param_mapping.items():
            if not mapping_value:  # skip unmapped
                continue

            # Espandi mapping in colonne effettive
            # - "X" → ["X"]
            # - "{X},{Y}" → ["X", "Y"]
            cols = _expand_mapping_columns(mapping_value)
            if not cols:
                continue

            param_cfg = param_cfg_map.get(param_name, {})
            param_type = param_cfg.get("type", "str")
            param_required = bool(param_cfg.get("required", True))

            for file_col in cols:
                # Se la colonna viene prodotta da un nodo upstream è un "collegamento
                # interno" della catena → non è una colonna che l'utente deve fornire.
                if file_col in upstream_produced:
                    continue

                if file_col in expected:
                    # Unione: required diventa True se almeno un uso è required
                    expected[file_col]["required"] = expected[file_col]["required"] or param_required
                    existing_used = expected[file_col].get("used_by", "")
                    if nid and nid not in existing_used:
                        expected[file_col]["used_by"] = (
                            f"{existing_used},{nid}" if existing_used else nid
                        )
                else:
                    expected[file_col] = {
                        "name": file_col,
                        # Per colonne da template (multiple) usiamo str come tipo
                        # generico — il tipo del parametro API non corrisponde
                        # 1:1 al tipo della singola colonna.
                        "type": param_type if len(cols) == 1 else "str",
                        "required": param_required,
                        "used_by": nid,
                        "description": param_cfg.get("description", "") or None,
                    }

    return list(expected.values())
