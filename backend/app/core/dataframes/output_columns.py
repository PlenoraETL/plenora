"""Selezione finale condivisa dai motori canvas."""

from app.core.dataframes.ports import FrameLike
from app.shared.execution.column_selection import select_column_plan
from app.shared.execution.output_column_refs import resolve_output_column_refs, unique_column_names


def apply_output_columns(df: FrameLike, columns: list[dict] | None, input_columns: list[dict] | None = None) -> FrameLike:
    if columns is None or columns == []:
        return df
    id_to_name = unique_column_names(input_columns or [])
    columns = resolve_output_column_refs(columns, id_to_name)
    plan = select_column_plan(list(df.columns), columns, skip_missing=True)
    return df[[name for name, _ in plan]].rename(columns=dict(plan))
