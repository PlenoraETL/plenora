"""
Builder canvas minimi per i contract test behavior-bound del task

`execute_enrichment_task`.
"""

from __future__ import annotations

from typing import Any, Dict


def make_minimal_source_canvas() -> Dict[str, Any]:
    """Canvas con UN solo nodo `project_source`.

    Sufficiente per esercitare:
      - istanziamento ApiConnectionService locale
      - setattr di `_current_group_id`
      - prima call a `_load_source_project` (che il fake api_cs solleva
        come `_HarnessAbort`)
    """
    return {
        "nodes": [
            {
                "id": "src1",
                "type": "project_source",
                "data": {"project_id": "test-proj"},
            },
        ],
        "edges": [],
    }


def make_minimal_file_source_canvas() -> Dict[str, Any]:
    """Canvas con UN solo nodo `file_source`."""
    return {
        "nodes": [
            {
                "id": "fs1",
                "type": "file_source",
                "data": {"file_source_id": "fs-id"},
            },
        ],
        "edges": [],
    }


def make_minimal_rest_file_canvas() -> Dict[str, Any]:
    """Canvas con `project_source` → `api_enrich` (rest_file).

    Sufficiente per il test che vuole arrivare al ramo
    `_execute_rest_file` (il fake api_cs solleva _HarnessAbort).
    """
    return {
        "nodes": [
            {
                "id": "src1",
                "type": "project_source",
                "data": {"project_id": "test-proj"},
            },
            {
                "id": "api1",
                "type": "api_enrich",
                "data": {"api_id": "api-x", "type": "rest_file"},
            },
        ],
        "edges": [
            {"source": "src1", "target": "api1"},
        ],
    }
