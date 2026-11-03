"""Selezione finale condivisa dai motori canvas."""

from app.core.dataframes.ports import FrameLike
from app.shared.execution.column_selection import select_column_plan
from app.shared.execution.config_column_refs import resolve_config_column_refs


def apply_output_columns(df: FrameLike, columns: list[dict] | None, input_columns: list[dict] | None = None) -> FrameLike:
    if columns is None or columns == []:
        return df
    id_to_name = {
        str(col.get("id") or col.get("column_id")): col["name"]
        for col in (input_columns or [])
        if isinstance(col, dict) and (col.get("id") or col.get("column_id")) and col.get("name")
    }
    columns = resolve_config_column_refs({"columns": columns}, id_to_name)["columns"]
    plan = select_column_plan(list(df.columns), columns, skip_missing=True)
    return df[[name for name, _ in plan]].rename(columns=dict(plan))
