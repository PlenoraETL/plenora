from typing import List, Dict, Any, Optional


def build_estrai_canvas(
    table_name: str,
    columns: List[Dict],
    result_filename: str = "output",
    result_format: str = "csv",
    filters: Optional[List[Dict]] = None,
    limit: Optional[int] = None,
) -> Dict[str, Any]:
    source_id = "node-source-1"
    result_id = "node-result-1"

    return {
        "nodes": [
            {
                "id": source_id,
                "type": "source",
                "position": {"x": 100, "y": 200},
                "table_name": table_name,
                "columns": columns,
                "filters": filters or [],
            },
            {
                "id": result_id,
                "type": "result",
                "position": {"x": 500, "y": 200},
                "fileName": result_filename,
                "format": result_format,
                "limit": limit,
            },
        ],
        "edges": [
            {
                "id": "edge-1",
                "source": source_id,
                "target": result_id,
            }
        ],
    }
