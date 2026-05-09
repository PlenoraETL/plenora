"""Canvas builder condivisi per integration test multi-DB.

Estratti da `_estrai_<db>_canvas` ripetuti identicamente nei 5 file
`test_<db>_connection_and_estrai_preview.py` (mysql / postgres /
oracle / sqlserver / mariadb). Le uniche varianti erano `fileName` e
- per Oracle - column names UPPERCASE.

Non vengono estratti i canvas CARICA / pipeline_chain perche' usano
varianti backend-specific (DDL clauses, write_mode, conflict policy)
che li rendono meno duplicati.
"""

from __future__ import annotations


def build_estrai_preview_canvas(
    table_name: str,
    file_name: str,
    *,
    uppercase_columns: bool = False,
) -> dict:
    """Canvas ESTRAI minimale: source -> result, 3 colonne (id/city/score).

    Args:
        table_name: nome tabella sorgente (auto-seedato dai test).
        file_name: prefisso del result file (es. "mysql_preview").
        uppercase_columns: True per Oracle (ID/CITY/SCORE), False altrove.
    """
    if uppercase_columns:
        columns = [
            {"name": "ID", "type": "int", "visible": True},
            {"name": "CITY", "type": "str", "visible": True},
            {"name": "SCORE", "type": "int", "visible": True},
        ]
    else:
        columns = [
            {"name": "id", "type": "int", "visible": True},
            {"name": "city", "type": "str", "visible": True},
            {"name": "score", "type": "int", "visible": True},
        ]
    return {
        "nodes": [
            {
                "id": "src-1",
                "type": "source",
                "position": {"x": 100, "y": 100},
                "table_name": table_name,
                "alias": "src",
                "columns": columns,
                "filters": [],
                "calculated_columns": [],
            },
            {
                "id": "result-1",
                "type": "result",
                "position": {"x": 350, "y": 100},
                "fileName": file_name,
                "format": "csv",
                "columns": [],
            },
        ],
        "edges": [
            {"id": "edge-1", "source": "src-1", "target": "result-1", "join_type": "output"}
        ],
    }
