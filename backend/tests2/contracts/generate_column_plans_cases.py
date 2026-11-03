"""Generate the language-neutral oracle: python backend/tests2/contracts/generate_column_plans_cases.py.

Inputs alone are authored here. Expectations always come from the runtime rules.
Case IDs and object keys are sorted; array order preserves the rules' output order.
"""
from __future__ import annotations

from itertools import product
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.shared.execution.column_selection import join_column_plan, select_column_plan
from app.shared.execution.result_grouping import result_group_plan
from app.shared.execution.schema_column_plans import (
    legacy_join_plan, join_output_plan, aggregation_output_type,
)

FIXTURE_PATH = Path(__file__).with_name("column_plans_cases.json")


def evaluate(kind, inputs):
    """Use JSON arrays for Python tuples, and a portable error sentinel."""
    try:
        if kind == "select":
            result = {"plan": select_column_plan(**inputs)}
        elif kind == "join":
            result = {"plan": join_column_plan(**inputs), "columns": join_output_plan(**inputs)}
        elif kind == "join_legacy":
            result = {"columns": legacy_join_plan(**inputs)}
        elif kind == "aggregation_types":
            result = {"type": aggregation_output_type(**inputs)}
        elif kind == "grouping":
            groups, aggregations = result_group_plan(**inputs)
            result = {"groups": groups, "aggregations": aggregations}
        else:
            raise AssertionError(kind)
    except Exception:
        return {"error": True}
    return json.loads(json.dumps(result))


def build_cases():
    cases = {"select": [], "join": [], "grouping": [], "join_legacy": [], "aggregation_types": []}

    def add(kind, case_id, inputs):
        cases[kind].append({"id": case_id, "input": inputs, "expected": evaluate(kind, inputs)})

    def select(case_id, columns, skip=False, names=None):
        add("select", case_id, {"names": ["a", "b", "c"] if names is None else names,
                                "columns": columns, "skip_missing": skip})

    labels = ["", None, 0, False, 5, "  ", "\t\n", "\u00a0", "renamed", " padded "]
    for skip, name, keep, (idx, label) in product(
        [False, True], ["a", "missing"], [False, True], enumerate(labels)
    ):
        select(f"label-{skip}-{name}-{keep}-{idx:02}", [{"name": name, "keep": keep, "label": label}], skip)
    for skip, name, (idx, keep) in product(
        [False, True], ["a", "missing"], enumerate([None, 0, 1, "false", "true", [], {}])
    ):
        select(f"nonbool-keep-{skip}-{name}-{idx}", [{"name": name, "keep": keep}], skip)
    for skip, (idx, entry) in product(
        [False, True], enumerate([None, "a", {}, {"name": None}, {"name": 5}, {"name": False},
                                 {"name": "  "}, {"name": ""}, {"name": []}, {"name": "\t"}])
    ):
        select(f"malformed-entry-{skip}-{idx}", [entry], skip)
    for idx, columns in enumerate([None, "a", {}, 0]):
        select(f"malformed-columns-{idx}", columns)
    for skip in [False, True]:
        for name in ["a", "missing"]:
            for keep in [False, True]:
                select(f"duplicate-name-{skip}-{name}-{keep}", [{"name": name, "keep": keep}, {"name": name}], skip)
        select(f"missing-default-{skip}", [{"name": "missing"}], skip)
        select(f"id-is-not-resolved-{skip}", [{"name": "stale", "name_column_id": "a"}], skip)
    select("id-carried-through", [{"name": "b", "name_column_id": "ignored", "label": "B"}])
    select("duplicate-output-explicit", [{"name": "a", "label": "same"}, {"name": "b", "label": "same"}])
    select("duplicate-output-unlisted", [{"name": "a", "label": "b"}])
    select("hidden-output-does-not-collide", [{"name": "a", "label": "b", "keep": False}])
    select("unlisted-order", [{"name": "c", "label": "C"}, {"name": "a", "keep": False}], names=["d", "b", "a", "c", "e"])
    select("empty-selection", [])
    select("empty-input", [], names=[])
    select("duplicate-upstream", [], names=["a", "a"])

    scenarios = {
        "single": (["id", "a", "shared"], ["rid", "b", "shared"], ["id"], ["rid"]),
        "same-key": (["id", "a"], ["id", "b"], ["id"], ["id"]),
        "multiple": (["id", "k", "a"], ["rid", "rk", "b"], ["id", "k"], ["rid", "rk"]),
        "multiple-same": (["id", "k", "a"], ["id", "k", "b"], ["id", "k"], ["id", "k"]),
        "mismatched-same": (["id", "k", "a"], ["id", "k", "b"], ["id", "k"], ["k", "id"]),
        "opposite-key": (["id", "rid", "a"], ["rid", "id", "b"], ["id"], ["rid"]),
        "suffix-collision": (["id", "a"], ["a_L", "a"], ["id"], ["a_L"]),
        "opposite-key-collision": (["id", "rid", "rid_L"], ["rid", "b"], ["id"], ["rid"]),
        "missing-key": (["id", "a"], ["rid", "b"], ["gone"], ["rid"]),
        "no-keys": (["a", "shared"], ["b", "shared"], [], []),
        "empty": ([], [], [], []),
    }
    scenarios.update({
        "all-shared": (["a", "b"], ["a", "b"], ["a", "b"], ["a", "b"]),
        "no-shared": (["a"], ["b"], [], []),
        "left-empty": ([], ["a"], [], []),
        "right-empty": (["a"], [], [], []),
        "duplicate-left": (["a", "a", "b"], ["c"], [], []),
        "duplicate-right": (["c"], ["a", "a", "b"], [], []),
        "duplicate-shared": (["a", "a"], ["a", "a"], ["a"], ["a"]),
        "duplicate-keys": (["a", "b"], ["a", "b"], ["a", "a"], ["a", "b"]),
        "unequal-keys": (["a", "b"], ["a", "b"], ["a", "b"], ["a"]),
        "right-only-keys": (["a"], ["a"], [], ["a"]),
        "left-only-keys": (["a"], ["a"], ["a"], []),
        "missing-right-key": (["a"], ["b"], ["a"], ["gone"]),
        "empty-name": (["", "a"], ["", "b"], [""], [""]),
    })
    for scenario, (left, right, left_keys, right_keys) in scenarios.items():
        add("join_legacy", scenario, dict(
            left=left, right=right, left_keys=left_keys, right_keys=right_keys))
        selections = lambda names: [None, [], names[-1:], ["unknown"]]
        for mode, (li, lc), (ri, rc) in product(
            [None, "suffix", "keep_left", "keep_right"], enumerate(selections(left)), enumerate(selections(right))
        ):
            add("join", f"{scenario}-{mode}-{li}-{ri}", dict(
                left=left, right=right, left_keys=left_keys, right_keys=right_keys,
                left_columns=lc, right_columns=rc, name_conflict=mode))
    for idx, overrides in enumerate([{"name_conflict": "invalid"}, {"left_columns": "a"}, {"right_columns": {"b": True}}]):
        add("join", f"invalid-{idx}", dict(left=["a"], right=["b"], left_keys=[], right_keys=[],
            **({"left_columns": None, "right_columns": None, "name_conflict": None} | overrides)))

    # Exhaust the small name/key alphabet, including duplicates and positional mismatches.
    name_lists = [[], ["a"], ["b"], ["a", "b"], ["b", "a"], ["a", "a"]]
    for idx, (left, right, left_keys, right_keys) in enumerate(product(name_lists, repeat=4)):
        add("join_legacy", f"matrix-{idx:04}", dict(
            left=left, right=right, left_keys=left_keys, right_keys=right_keys))
    for idx, overrides in enumerate([
        {"left": None}, {"right": None}, {"left_keys": None}, {"right_keys": None},
        {"left_keys": [[]]}, {"right_keys": [{}]}, {"left": [[]]}, {"right": [{}]},
    ]):
        add("join_legacy", f"invalid-{idx}",
            dict(left=["a"], right=["b"], left_keys=[], right_keys=[]) | overrides)
    for idx, (func, source_type) in enumerate(product(
        ["count", "mean", "std", "sum", "min", "max", "first", "last", "median",
         "nunique", "avg", "COUNT", "MEAN", "STD", "unknown", "", None],
        ["int", "real", "str", "bool", "date", "datetime", "geometry", "unknown", ""],
    )):
        add("aggregation_types", f"matrix-{idx:03}", dict(func=func, source_type=source_type))

    # Missing fields are distinct from explicit nulls and empty lists/strings.
    group_options = [{}, {"group_by_columns": None}, {"group_by_columns": []},
                     {"group_by_columns": ["missing"]}, {"group_by_columns": ["missing", "g"]},
                     {"group_by_columns": ["h", "g"]}]
    funcs = ["sum", "SUM", "mean", "MEAN", "avg", "AVG", "min", "MAX", "std", "count", "COUNT", None, "", "unsupported"]
    agg_options = [{}, {"aggregations": None}, {"aggregations": []}]
    for func, column, alias in product(funcs, ["v", "missing", None], ["total", ""]):
        agg_options.append({"aggregations": [{"column": column, "function": func, "alias": alias}]})
    agg_options.extend([
        {"aggregations": [{"function": "count"}]},
        {"aggregations": [{"column": "v"}]},
        {"aggregations": [{"column": "missing", "function": "sum"}, {"column": "v", "function": "AVG"}]},
        {"aggregations": [{"column": "v", "function": "sum", "alias": None}, {"function": "count", "alias": "rows"}]},
    ])
    for enabled, (gi, groups), (ai, aggs) in product([False, True], enumerate(group_options), enumerate(agg_options)):
        add("grouping", f"matrix-{enabled}-{gi}-{ai:03}", {
            "node": {"group_by_enabled": enabled, **groups, **aggs}, "names": ["g", "h", "v"]})
    add("grouping", "enabled-absent", {"node": {}, "names": ["g", "v"]})
    for rows in cases.values():
        rows.sort(key=lambda case: case["id"])
    return cases


def serialize(cases):
    return json.dumps(cases, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


if __name__ == "__main__":
    generated = build_cases()
    FIXTURE_PATH.write_text(serialize(generated), encoding="utf-8", newline="\n")
    print({kind: len(rows) for kind, rows in generated.items()})
