"""Congela le attese del corpus ``filter_groups_corpus`` (eseguito UNA volta su HEAD e05f8fea).

    python backend/tests2/contracts/generate_filter_groups_golden.py

Il golden ``filter_groups_golden.json`` è la fotografia del comportamento
PRIMA dei gruppi E/O e del self-join; ``test_filter_groups_parity`` confronta
il codice attuale con quella fotografia. Rigenerarlo con il codice nuovo
toglierebbe la prova: si rigenera solo se un cambio voluto del SQL/DataFrame
senza ``group`` e senza tabelle ripetute è stato deciso, e va detto nel commit.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.core.estrai.query_generator import QueryGenerator  # noqa: E402
from app.core.manipola.transforms.filtering import op_filter  # noqa: E402
from app.core.projects.canvas_models import Edge, Node  # noqa: E402
from app.core.projects.schema_execution_sources import sql_projection  # noqa: E402

import filter_groups_corpus as corpus  # noqa: E402

GOLDEN_PATH = Path(__file__).with_name("filter_groups_golden.json")


def _error(exc: Exception) -> dict:
    return {"error": type(exc).__name__, "message": str(exc)}


def estrai_filter_outcome(case: dict) -> dict:
    nodes = [Node(**corpus._source("src", "EVENTS", case["alias"], corpus._ESTRAI_COLUMNS, filters=case["filters"])),
             Node(**corpus._result())]
    try:
        out = QueryGenerator(db_type=case["db_type"]).generate_sql(nodes, [Edge(**corpus._out("src"))], "result-1")
    except ValueError as exc:
        return _error(exc)
    return {"sql": out["sql"], "params": out["params"]}


def estrai_canvas_outcome(case: dict) -> dict:
    nodes = [Node(**item) for item in case["nodes"]]
    edges = [Edge(**item) for item in case["edges"]]
    result = next(node for node in nodes if node.type == "result")
    try:
        out = QueryGenerator(db_type=case["db_type"]).generate_sql(nodes, edges, result.id)
        columns = sql_projection(nodes, edges, result, case["db_type"])
    except ValueError as exc:
        return _error(exc)
    return {"sql": out["sql"], "params": out["params"], "tables_used": out["tables_used"],
            "columns": [{"name": col["name"], "source_node": col.get("source_node"),
                         "source_name": col.get("source_name")} for col in columns]}


def frame_payload(frame) -> dict:
    return {"columns": [str(col) for col in frame.columns], "index": [int(i) for i in frame.index],
            "dtypes": [str(dtype) for dtype in frame.dtypes], "values": json.loads(frame.to_json(orient="values"))}


def manipola_outcome(case: dict) -> dict:
    try:
        return frame_payload(op_filter(corpus.manipola_frame(), **case["params"]))
    except ValueError as exc:
        return _error(exc)


def build_golden() -> dict:
    return {
        "estrai_filters": {case["id"]: estrai_filter_outcome(case) for case in corpus.estrai_filter_cases()},
        "estrai_canvas": {f"{case['db_type']}/{case['id']}": estrai_canvas_outcome(case) for case in corpus.estrai_canvas_cases()},
        "manipola": {case["id"]: manipola_outcome(case) for case in corpus.manipola_cases()},
    }


def serialize(golden: dict) -> str:
    return json.dumps(golden, indent=1, sort_keys=True, ensure_ascii=False) + "\n"


if __name__ == "__main__":
    GOLDEN_PATH.write_text(serialize(build_golden()), encoding="utf-8")
