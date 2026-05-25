"""Pandas-backed helpers for conditional frame split and parallel merge."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from app.core.dataframes.frame_copy import copy_frame_for_update


def execute_condition_mask(
    input_df: Any,
    parsed_condition: Optional[Tuple[str, str, bool]],
) -> Any:
    """Return a per-row execution mask for a parsed execute_if condition."""
    if parsed_condition is None:
        return pd.Series(True, index=input_df.index) if isinstance(input_df, pd.DataFrame) else True

    col, expected, negate = parsed_condition
    if not isinstance(input_df, pd.DataFrame) or col not in input_df.columns:
        return pd.Series(negate, index=input_df.index) if isinstance(input_df, pd.DataFrame) else negate

    match = input_df[col].astype(str).str.strip().str.lower() == expected
    return (~match) if negate else match


def should_execute_first_row(
    input_df: Any,
    parsed_condition: Optional[Tuple[str, str, bool]],
) -> bool:
    """Evaluate a parsed execute_if condition against the first frame row."""
    if parsed_condition is None:
        return True

    col, expected, negate = parsed_condition
    if not isinstance(input_df, pd.DataFrame) or col not in input_df.columns or len(input_df) == 0:
        return negate

    actual = str(input_df[col].iloc[0]).strip().lower()
    match = actual == expected
    return (not match) if negate else match


def mask_all(mask: Any) -> bool:
    """Return True when all selected rows should execute."""
    return bool(mask.all()) if isinstance(mask, pd.Series) else bool(mask)


def mask_any(mask: Any) -> bool:
    """Return True when at least one selected row should execute."""
    return bool(mask.any()) if isinstance(mask, pd.Series) else bool(mask)


def mask_count(mask: Any) -> int:
    """Count True values in a pandas boolean mask."""
    return int(mask.sum()) if isinstance(mask, pd.Series) else int(bool(mask))


def merge_split_execution_result(input_df: Any, mask: Any, result_exec: Any) -> Any:
    """Merge a conditionally executed frame subset back into the input frame."""
    if not isinstance(mask, pd.Series):
        return result_exec if isinstance(result_exec, pd.DataFrame) else input_df

    df_skip = input_df[~mask].copy()
    if isinstance(result_exec, pd.DataFrame):
        new_cols = [c for c in result_exec.columns if c not in df_skip.columns]
        for c in new_cols:
            df_skip[c] = None
        return pd.concat([result_exec, df_skip]).sort_index()
    return input_df


def split_execution_frame(input_df: Any, mask: Any) -> Any:
    """Return the frame subset selected for execution."""
    return input_df[mask].copy() if isinstance(mask, pd.Series) else input_df


def positional_row_slice(input_df: Any, start: int, end: int) -> Any:
    """Return rows selected by pandas positional slicing semantics."""
    return input_df.iloc[start:end]


def true_mask_for_frame(input_df: Any) -> Any:
    """Return a True boolean mask aligned to a DataFrame index."""
    return pd.Series(True, index=input_df.index)


def validate_and_merge_parallel_columns(
    input_df: Any,
    node_ids: List[str],
    node_results: Dict[str, Any],
) -> Any:
    """Merge new columns from parallel node result frames into a copied input frame."""
    merged = copy_frame_for_update(input_df)
    for nid in node_ids:
        result_df = node_results.get(nid)
        if result_df is None or not isinstance(result_df, pd.DataFrame):
            continue

        if not result_df.index.equals(merged.index):
            same_len = len(result_df) == len(merged)
            default_range = pd.RangeIndex(len(merged))
            both_default_range = (
                result_df.index.equals(default_range)
                and merged.index.equals(default_range)
            )
            if not (same_len and both_default_range):
                raise RuntimeError(
                    f"[parallel-merge] mismatch index/cardinalita' su nodo "
                    f"{nid}: result_df ha {len(result_df)} righe vs "
                    f"merged {len(merged)}; index uguali={result_df.index.equals(merged.index)}. "
                    f"Il merge positional .values produrrebbe silent "
                    f"corruption riga<->valore. L'executor del nodo deve "
                    f"preservare l'index di input_df, oppure produrre il "
                    f"RangeIndex default (0..N-1) coerente con un input "
                    f"a RangeIndex default."
                )

        new_cols = [c for c in result_df.columns if c not in merged.columns]
        for c in new_cols:
            merged[c] = result_df[c].values

    return merged


__all__ = [
    "execute_condition_mask",
    "mask_all",
    "mask_any",
    "mask_count",
    "merge_split_execution_result",
    "positional_row_slice",
    "should_execute_first_row",
    "split_execution_frame",
    "true_mask_for_frame",
    "validate_and_merge_parallel_columns",
]
