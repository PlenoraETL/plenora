"""
Comparison Keys DAL helpers.

Keeps config-table persistence isolated behind a narrow database module.
"""

from app.db.pg_connection import execute


def delete_comparison_keys(canvas_id: str, node_id: str = None) -> None:
    if node_id:
        execute("DELETE FROM comparison_keys WHERE canvas_id = ? AND node_id = ?",
                (canvas_id, node_id))
    else:
        execute("DELETE FROM comparison_keys WHERE canvas_id = ?", (canvas_id,))
