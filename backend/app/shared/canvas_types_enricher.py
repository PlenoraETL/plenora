"""N11: bridge projects.columns_metadata -> carica_node_data.canvas_types.

Risolve il "Type inference loss" tra ESTRAI -> MANIPOLA -> CARICA:
se MANIPOLA promuove silenziosamente int->float per un LEFT JOIN con
NaN, CARICA senza canvas_types cadeva sul dtype post-manipolazione ->
il tipo semantico originale (INTEGER dal DB sorgente) andava perso
e la tabella target veniva scritta come DOUBLE.

Sorgenti di verita' per il tipo di colonna, in ordine di priorita':
  1. node_data["canvas_types"] — se il frontend ha settato esplicitamente
  2. projects.columns_metadata — mantenuto da ESTRAI/ARRICCHISCI dopo ogni run
  3. ColumnMapper._infer_type_from_pandas — fallback euristico (N11 mirato)

Perche' projects.columns_metadata e NON intermediate.node_meta.col_schema:
  L'audit originale suggeriva "propagare via intermediate metadata". Ma
  `node_meta.col_schema` salva il dtype pandas POST-scrittura del nodo
  (vedi es. api_connector.py:869 -> `col_schema=df.dtypes.astype(str).to_dict()`).
  Quindi contiene GIA' il tipo promoted int->float — lo stesso bug che
  stiamo cercando di evitare. `columns_metadata` invece tiene tipi
  canvas semantici puliti ({"name":"id","type":"int"}) popolati da
  ESTRAI (executor.py:526) e ARRICCHISCI (executor.py:100) quando hanno
  ancora accesso ai tipi DB originali / schema API originali.

  In sintesi: il fix chiude il problema pratico con un bridge diverso
  dall'alternativa proposta. E' una scelta consapevole, non un'omissione.

Questo helper popola (1) dalle sorgenti (2)+(1), cosi' il ColumnMapper
trova il tipo canvas prima di dover euristicare dal dtype pandas.
Best-effort: errori di lookup loggano warning ma non bloccano.
"""

from __future__ import annotations

from typing import Any, Dict


def enrich_canvas_types_from_metadata(
    carica_node_data: Dict[str, Any],
    project_id: str,
) -> Dict[str, Any]:
    """Arricchisce in-place `carica_node_data["canvas_types"]` con i tipi
    canvas del project da `projects.columns_metadata`.

    Regola di merge: i canvas_types gia' presenti in node_data hanno
    priorita' (il frontend puo' averli impostati esplicitamente). Le
    colonne mancanti vengono fillate dai columns_metadata del project.

    Args:
        carica_node_data: dict del nodo CARICA (come arriva al task).
            Modificato in-place aggiungendo/sovrascrivendo la chiave
            "canvas_types".
        project_id: ID del project upstream (per lookup columns_metadata).

    Returns:
        Lo stesso dict (per chainability).
    """
    from app.utils.logger import get_logger
    logger = get_logger(__name__)

    from app.shared.column_types import metadata_to_dict

    node_column_types = carica_node_data.get("canvas_types", {}) or {}

    project_column_types: Dict[str, str] = {}
    try:
        from app.services.project_service import ProjectService
        cols_md = ProjectService().get_project_columns_metadata(project_id)
        project_column_types = metadata_to_dict(cols_md)
    except Exception as e:
        logger.warning(
            f"[N11] Lettura columns_metadata fallita per {project_id}: {e}. "
            f"CARICA cadra' sul fallback _infer_type_from_pandas."
        )

    # Merge: project fornisce il default, node_data sovrascrive
    # (frontend autorevole se ha settato canvas_types espliciti).
    merged = {**project_column_types, **node_column_types}
    if merged:
        carica_node_data["canvas_types"] = merged
    return carica_node_data
