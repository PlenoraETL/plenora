"""Contratti condivisi di selezione colonne per runtime e inferenza schema.

Stanno in shared/execution perche' li usano sia il runtime Manipola
(transforms) sia l'inferenza schema (projects): nessuno dei due domini
deve dipendere dall'altro per questo.
"""

import logging

from typing import Any

JsonRows = list[dict[str, Any]]
StringMap = dict[str, str]


def select_column_plan(names: list[str], columns: JsonRows, *, skip_missing: bool = False) -> list[tuple[str, str]]:
    if not isinstance(columns, list):
        raise ValueError("Select columns: columns deve essere una lista.")
    plan: list[tuple[str, str]] = []
    seen: set[str] = set()
    for item in columns:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not item["name"].strip():
            raise ValueError("Select columns: name deve essere un nome non vuoto.")
        name = item["name"]
        if skip_missing and name not in names:
            logging.getLogger(__name__).warning("Select columns: colonna non trovata, ignorata: %s", name)
            continue
        if name in seen:
            raise ValueError(f"Select columns: colonna duplicata '{name}'.")
        seen.add(name)
        if not isinstance(item.get("keep", True), bool):
            raise ValueError("Select columns: keep deve essere booleano.")
        label = item.get("label")
        if label is None or label == "":
            label = name
        if not isinstance(label, str) or (item.get("keep", True) and not label.strip()):
            raise ValueError("Select columns: label deve essere un nome non vuoto.")
        if item.get("keep", True):
            plan.append((name, label))
    plan.extend((name, name) for name in names if name not in seen)
    outputs = [label for _, label in plan]
    if len(outputs) != len(set(outputs)):
        raise ValueError("Select columns: i nomi di output devono essere univoci.")
    missing = [item["name"] for item in columns if item["name"] not in names]
    if missing and not skip_missing:
        raise ValueError(f"Select columns: colonna non trovata: {missing}.")
    return [(name, label) for name, label in plan if name in names]


def join_column_plan(
    left: list[str], right: list[str], left_keys: list[str], right_keys: list[str],
    left_columns: list[str] | None, right_columns: list[str] | None,
    name_conflict: str | None,
) -> tuple[StringMap, StringMap]:
    mode = "suffix" if name_conflict is None else name_conflict
    if mode not in {"suffix", "keep_left", "keep_right"}:
        raise ValueError("Join: name_conflict deve essere keep_left, keep_right o suffix.")
    sides = []
    for names, keys, selected in ((left, left_keys, left_columns), (right, right_keys, right_columns)):
        if selected is not None and (not isinstance(selected, list) or any(name not in names for name in selected)):
            raise ValueError("Join: selezione colonne non valida o colonna non trovata.")
        if any(key not in names for key in keys):
            raise ValueError("Join: colonna chiave non trovata.")
        sides.append([name for name in names if selected is None or name in selected or name in keys])
    for key in set(left_keys) & set(right_keys):
        if left_keys.index(key) != right_keys.index(key):
            raise ValueError("Join: chiavi omonime associate a coppie diverse; rinominare prima del join.")
    common = set(sides[0]) & set(sides[1])
    keys = set(left_keys) | set(right_keys)
    maps = []
    for idx, (names, own_keys) in enumerate(zip(sides, (left_keys, right_keys))):
        mapping = {}
        for name in names:
            if name in own_keys:
                mapping[name] = name
            elif name in keys:
                mapping[name] = name + ("_L" if idx == 0 else "_R")
            elif mode == "suffix":
                mapping[name] = name + ("_L" if idx == 0 else "_R")
            elif name not in common or (mode == "keep_left") == (idx == 0):
                mapping[name] = name
        maps.append(mapping)
    outputs = list(maps[0].values()) + [v for k, v in maps[1].items() if k not in set(left_keys) & set(right_keys)]
    if len(outputs) != len(set(outputs)):
        raise ValueError("Join: collisione nei nomi di output; rinominare le colonne prima del join.")
    return maps[0], maps[1]
