"""Stable output names shared by schema and serialization."""
from typing import Any, Iterable
from app.shared.contracts.payloads import IntMap

def make_unique_column_names(
    columns: Iterable[Any],
) -> tuple[list[str], list[tuple[str, str]]]:
    """Return stable, public-safe column names.

    Duplicate names are suffixed with ``_2``, ``_3``, ... while preserving
    order. Existing names are reserved up front, so ``["id", "id", "id_2"]``
    becomes ``["id", "id_3", "id_2"]`` instead of reintroducing a collision.
    """
    original = [str(col) for col in columns]
    reserved = set(original)
    used: set[str] = set()
    counters: IntMap = {}
    unique: list[str] = []
    renames: list[tuple[str, str]] = []

    for name in original:
        if name not in used:
            used.add(name)
            counters.setdefault(name, 1)
            unique.append(name)
            continue

        suffix = counters.get(name, 1)
        while True:
            suffix += 1
            candidate = f"{name}_{suffix}"
            if candidate not in used and candidate not in reserved:
                break
        counters[name] = suffix
        used.add(candidate)
        unique.append(candidate)
        renames.append((name, candidate))

    return unique, renames

