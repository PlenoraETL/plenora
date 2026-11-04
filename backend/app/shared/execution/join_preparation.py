"""Preparazione dei nomi dei Join, senza dipendenze dai DataFrame."""


from typing import Any, List
from collections.abc import Sequence


def deduplicate_join_names(names: Sequence[str], keys: Sequence[str]) -> list[str]:
    """Riproduce la rinomina storica, incluse le collisioni residue."""
    seen, key_seen = {}, {}
    result = []
    for name in names:
        if name in keys:
            key_seen[name] = key_seen.get(name, 0) + 1
            result.append(name if key_seen[name] == 1 else f"{name}_dup{key_seen[name]}")
        else:
            seen[name] = seen.get(name, -1) + 1
            result.append(name if seen[name] == 0 else f"{name}_{seen[name]}")
    return result


def multi_join_renames(left: Sequence[str], right: Sequence[str], left_keys: Sequence[str], right_keys: Sequence[str], index: int, *, paired: bool = False) -> dict[str, str]:
    """Mappa delle collisioni di un passo del multi-join."""
    left_names = set(left) if paired else set(left) - set(left_keys)
    duplicates = left_names & (set(right) - set(right_keys))
    return {name: f"{name}_{index}" for name in duplicates}


def suffix_join_names(names: Sequence[str], keys: Sequence[str], suffix: str) -> list[str]:
    """Applica i suffissi legacy prima dell'eliminazione dei duplicati residui."""
    done = {}
    result = []
    for name in names:
        if name in keys:
            done[name] = done.get(name, 0) + 1
            result.append(name if done[name] == 1 else f"{name}_{suffix}_dup{done[name]}")
        else:
            result.append(f"{name}_{suffix}")
    return result


def prepared_join_plan(left: list[str], right: list[str], left_keys: list[str], right_keys: list[str], maps: tuple[dict[str, str], dict[str, str]] | None = None) -> list[tuple[int, int, str]]:
    """Associa posizione sorgente e nome finale dopo preparazione e merge."""
    sides = []
    for side, names, keys, suffix in ((0, left, left_keys, "L"), (1, right, right_keys, "R")):
        indices = (list(range(len(names))) if maps is None else
                   [i for selected in maps[side] for i, name in enumerate(names) if name == selected])
        deduped = deduplicate_join_names([names[i] for i in indices], keys)
        renamed = (suffix_join_names(deduped, keys, suffix) if maps is None
                   else [maps[side].get(name, name) for name in deduped])
        unique = {}
        for index, name in zip(indices, renamed):
            unique.setdefault(name, index)
        sides.append(unique)
    common = set(sides[0]) & set(sides[1])
    shared_keys = {lk for lk, rk in zip(left_keys, right_keys) if lk == rk}
    drop_right = {rk for lk, rk in zip(left_keys, right_keys) if lk != rk and rk not in left_keys}
    result = [(0, index, name) for name, index in sides[0].items()]
    for name, index in sides[1].items():
        if name in shared_keys:
            continue
        output = f"{name}_dup" if name in common else name
        if maps is None and output in drop_right:
            continue
        result.append((1, index, output))
    return result


def normalize_key_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


def normalize_join_pairs(value: Any) -> tuple[List[str], List[str]]:
    if not isinstance(value, list):
        return [], []
    left_keys: List[str] = []
    right_keys: List[str] = []
    for pair in value:
        if not isinstance(pair, dict):
            continue
        left_key = str(pair.get("left") or "").strip()
        right_key = str(pair.get("right") or "").strip()
        if left_key and right_key:
            left_keys.append(left_key)
            right_keys.append(right_key)
    return left_keys, right_keys

