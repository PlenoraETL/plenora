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


# ---------------------------------------------------------------- colonne nullable (B-K1)

NULLABLE_GOLDEN_PATH = Path(__file__).with_name("filter_nullable_golden.json")
NULLABLE_REFERENCE_COMMIT = "e05f8fea"
_ROW = "__riga__"


def _any_error(exc: Exception) -> dict:
    # Anche TypeError & co.: con le colonne nullable il difetto era proprio
    # un'eccezione non ValueError.
    return {"error": type(exc).__name__, "message": str(exc)}


def _split_groups(conditions: list[dict]) -> list[list[dict]]:
    """Gruppi in ordine di prima apparizione (assente = 0), senza la chiave ``group``.

    Scritta qui a parte (non ``condition_groups``): l'oracolo non usa il codice
    che verifica.
    """
    groups: dict[int, list[dict]] = {}
    for item in conditions:
        groups.setdefault(item.get("group") or 0, []).append({k: v for k, v in item.items() if k != "group"})
    return list(groups.values())


def manipola_nullable_outcome(filtering, case: dict) -> dict:
    """Esito di ``filtering.op_filter`` sul frame nullable.

    ``filtering`` è il modulo attuale (test) o quello di e05f8fea (golden). Con
    più gruppi e il modulo di e05f8fea (che non li conosceva) l'esito è
    l'oracolo: validazione dell'intera lista, poi ogni gruppo a catena, poi
    l'unione delle righe nell'ordine originale.
    """
    frame = corpus.manipola_nullable_frame()
    params = case["params"]
    conditions = params.get("conditions")
    groups = _split_groups(conditions) if conditions else []
    try:
        if len(groups) <= 1 or getattr(filtering, "_apply_group", None) is not None:
            return frame_payload(filtering.op_filter(frame, **params))
        filtering.prepare_filter(frame, conditions=[item for group in groups for item in group])
        tracked = frame.assign(**{_ROW: range(len(frame))})
        rows: set[int] = set()
        for group in groups:
            rows.update(filtering.op_filter(tracked, conditions=group)[_ROW].tolist())
        return frame_payload(frame.iloc[sorted(rows)].reset_index(drop=True))
    except Exception as exc:  # noqa: BLE001 - il tipo d'errore fa parte dell'esito
        return _any_error(exc)


def manipola_nullable_conditional_outcome(filtering, case: dict) -> dict:
    try:
        return frame_payload(filtering.op_conditional(corpus.manipola_nullable_frame(), **case["params"]))
    except Exception as exc:  # noqa: BLE001
        return _any_error(exc)


def reference_filtering_module():
    """``manipola/transforms/filtering.py`` di e05f8fea, caricato da git."""
    import importlib.util
    import subprocess
    import tempfile

    repo = Path(__file__).resolve().parents[3]
    source = subprocess.run(
        ["git", "show", f"{NULLABLE_REFERENCE_COMMIT}:backend/app/core/manipola/transforms/filtering.py"],
        cwd=repo, check=True, capture_output=True, text=True, encoding="utf-8",
    ).stdout
    path = Path(tempfile.mkdtemp()) / "filtering_reference.py"
    path.write_text(source, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("filtering_reference", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_nullable_golden(filtering) -> dict:
    return {
        "filter": {case["id"]: manipola_nullable_outcome(filtering, case) for case in corpus.manipola_nullable_cases()},
        "conditional": {case["id"]: manipola_nullable_conditional_outcome(filtering, case)
                        for case in corpus.manipola_nullable_conditional_cases()},
    }


if __name__ == "__main__":
    if "--nullable" in sys.argv:
        # Solo il golden delle colonne nullable, dal codice di e05f8fea.
        NULLABLE_GOLDEN_PATH.write_text(serialize(build_nullable_golden(reference_filtering_module())), encoding="utf-8")
    else:
        GOLDEN_PATH.write_text(serialize(build_golden()), encoding="utf-8")
