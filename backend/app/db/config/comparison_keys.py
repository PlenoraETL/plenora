"""
Comparison keys DAL — estratto da `config_db_service.py` (scope micro Batch G #9,

invariants §6 commit `58d98a0`).
"""

from app.db.pg_connection import execute


def delete_comparison_keys(canvas_id: str, node_id: str = None) -> None:
    if node_id:
        execute("DELETE FROM comparison_keys WHERE canvas_id = ? AND node_id = ?",
                (canvas_id, node_id))
    else:
        execute("DELETE FROM comparison_keys WHERE canvas_id = ?", (canvas_id,))
