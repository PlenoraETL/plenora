"""Pure ordered column and type rules shared by schema inference and parity fixtures."""

from app.shared.execution.column_selection import join_column_plan


def legacy_join_plan(
    left: list[str], right: list[str], left_keys: list[str], right_keys: list[str],
) -> list[list[str]]:
    """Keep legacy suffixing, including duplicate names and positional key pairing."""
    left_key_set, right_key_set = set(left_keys), set(right_keys)
    shared = {rk for lk, rk in zip(left_keys, right_keys) if lk == rk}
    return [
        ["left", name, name if name in left_key_set else f"{name}_L"]
        for name in left
    ] + [
        ["right", name, name if name in right_key_set else f"{name}_R"]
        for name in right if name not in shared
    ]


def join_output_plan(
    left: list[str], right: list[str], left_keys: list[str], right_keys: list[str],
    left_columns: list[str] | None, right_columns: list[str] | None,
    name_conflict: str | None,
) -> list[list[str]]:
    """Apply join maps in source order, preserving duplicate source occurrences."""
    left_map, right_map = join_column_plan(
        left, right, left_keys, right_keys, left_columns, right_columns, name_conflict,
    )
    shared = set(left_keys) & set(right_keys)
    return [["left", name, left_map[name]] for name in left if name in left_map] + [
        ["right", name, right_map[name]] for name in right
        if name in right_map and name not in shared
    ]


def aggregation_output_type(func: str, source_type: str) -> str:
    """Return the grouped schema type without normalizing functions or source types."""
    if func == "count":
        return "int"
    if func in {"mean", "std"}:
        return "real"
    return source_type
