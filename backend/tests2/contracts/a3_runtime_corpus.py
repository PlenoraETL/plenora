"""Corpus deterministico A3 (correzione dopo la conferma): esecuzione = verità.

Lo stesso modulo gira sul riferimento 3c8609fe (sonda di generazione della
fixture ``a3_head_outcomes.json``) e sul working tree (guardia
``test_a3_head_parity.py``). Dipende soltanto da pandas/geopandas e dal
registry JSON letto dal disco dell'albero in esecuzione, così è eseguibile
anche dove i moduli A3 non esistono.

Voci (chiave → op, config, tipo di frame):

- ``valid:*``    configurazioni valide A1 (``inference_runtime_cases``);
- ``opt:*``      ogni parametro opzionale significativo aggiunto alla valida
  (colonna esistente, ogni opzione dei select, booleani, numeri) e tutti
  insieme;
- ``gen:*``      parametro obbligatorio mancante / parametro colonna assente;
- ``formula:*``  corpus di formule (test esistenti, risolutori di pandas.eval,
  costanti e funzioni), su indice anonimo e su indice con nome ``idx``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from tests2.contracts.inference_runtime_cases import CASES as VALID, EXTRA

_REGISTRY = Path(__file__).resolve().parents[2] / "app" / "core" / "manipola" / "transforms_registry.json"
REGISTRY = json.loads(_REGISTRY.read_text(encoding="utf-8"))["transforms"]
COLUMN_PARAM_TYPES = {"column", "columns", "geometry_column"}

FORMULAS = [
    # Test esistenti.
    "1", "a * 2", "a * 3", "a + 1", "a > 0", "a*2", "b*2", "id * 2", "id + 1", "id*2",
    "s * 2", "s + 'x'", "to_numeric(a) + 1", "a + b", "prezzo * 0.22", "assente * 2",
    "colonna_inesistente + 1", "valore_catastale_L / vc_tot_contratto_Romi_L",
    "__import__('os')", "__import__ + 1", "_hidden + 1",
    # Risolutori di pandas.eval (conferma A3).
    "a + inf", "a * Inf", "index * 2", "idx + 1", "index", "-inf", "a - Inf",
    "ilevel_0 + 1", "True", "a + True", "False | (a > 1)", "nan", "NaN", "None",
    # Funzioni matematiche di pandas.eval.
    "sin(a)", "cos(b)", "sqrt(b)", "abs(a - 3)", "log(b)", "log10(b)", "exp(a)",
    "expm1(a)", "log1p(b)", "arctan2(a, b)", "arcsinh(a)", "tanh(b)", "sinh(a)",
    "floor(b)", "ceil(b)", "where(a > 1, a, b)", "pi", "e",
    # Operatori, costanti e metodi.
    "a ** 2", "a // 2", "a % 2", "-a", "~(a > 1)", "(a > 1) & (b < 4)", "a > 1 and b < 4",
    "not a", "a == 1", "s == 'ab'", "s + s", "1.5", "'k'", "a.abs()", "b.round(1)",
    "a.clip(1, 2)", "a.fillna(0)", "a.astype(str)", "s.str.upper()", "s.str.len()",
    "pd.to_numeric(s)", "pd.to_numeric(a) * 2", "a if a else b", "a.mean()", "len(a)",
    "max(a, b)", "id + a + b", "a / 0", "s.str.contains('a')", "a.isna()", "a.str",
]


def frames(op: str, empty: bool, kind: str = "plain") -> list:
    """Stessi ingressi di ``test_inference_runtime_parity.frames``; ``kind='idx'``
    dà all'indice il nome ``idx`` (risolutore dell'indice di pandas.eval)."""
    frame = pd.DataFrame({"id": [1, 1, 2, 2], "a": [1, 2, 3, 4],
                          "b": [1.5, 2.5, 3.5, 4.5], "s": ["ab", "a,b", "ab", "x"]})
    if kind == "idx":
        frame.index.name = "idx"
    if op.startswith("geo_") or op == "sjoin":
        import geopandas as gpd
        from shapely.geometry import Point
        frame = gpd.GeoDataFrame(frame, geometry=[Point(0, 0), Point(1, 0), Point(1, 1), Point(0, 1)], crs=3857)
    if empty:
        frame = frame.iloc[:0]
    return [frame, frame.copy()]


def valid_config(op: str, raw: dict) -> dict:
    config = dict(raw)
    for param in REGISTRY[op].get("parameters", []):
        if "geom" in param["id"] and param["id"] != "keep_geom_type":
            config.setdefault(param["id"], "geometry")
    return config


def _optional_values(param: dict) -> list:
    kind = param.get("type")
    if kind == "column":
        return ["s", "id"]
    if kind == "columns":
        return [["s"]]
    if kind == "select":
        return [o.get("value") if isinstance(o, dict) else o for o in param.get("options") or []]
    if kind == "boolean":
        return [True, False]
    if kind == "number":
        return [value for value in (param.get("default"), param.get("min"), 1, 2) if value is not None][:2]
    return []


def corpus() -> list[tuple[str, str, dict, str]]:
    entries: list[tuple[str, str, dict, str]] = []
    seen: set[str] = set()

    def add(key: str, op: str, config: dict, kind: str = "plain") -> None:
        key = f"{key}|{json.dumps(config, sort_keys=True, default=str)}|{kind}"
        if key not in seen:
            seen.add(key)
            entries.append((key, op, config, kind))

    positives = list(VALID.items()) + list(EXTRA)
    for op, raw in positives:
        add(f"valid:{op}", op, valid_config(op, raw))
    for op, raw in VALID.items():
        base = valid_config(op, raw)
        together = dict(base)
        for param in REGISTRY[op].get("parameters", []):
            if param.get("required") or param["id"] in base or param.get("type") == "geometry_column":
                continue
            values = _optional_values(param)
            for value in values:
                add(f"opt:{op}:{param['id']}", op, {**base, param["id"]: value})
            if values:
                together[param["id"]] = values[0]
        add(f"opt:{op}:*", op, together)
        for param in REGISTRY[op].get("parameters", []):
            if param.get("required"):
                add(f"gen:{op}:-{param['id']}", op, {k: v for k, v in base.items() if k != param["id"]})
            if param.get("type") in COLUMN_PARAM_TYPES:
                add(f"gen:{op}:?{param['id']}", op, {**base, param["id"]: ["assente"] if param["type"] == "columns" else "assente"})
    for formula in FORMULAS:
        for kind in ("plain", "idx"):
            add("formula", "formula", {"new_column": "z", "formula": formula}, kind)
    return entries


# Esiti ---------------------------------------------------------------------------

RANDOM_OPS = {"sample", "uuid_generator"}  # valori casuali: si confrontano solo schema e righe


def _node(op: str, config: dict):
    from app.core.projects.canvas_models import Node
    return Node.model_validate({"id": "t", "type": "transform", "position": {"x": 0, "y": 0},
                                "transform_op": op, "config": config})


def _digest(frame) -> str:
    """Portable fingerprint: LF records and 14 significant digits for floats.

    The historical fixture was written on Windows (implicit CRLF). NumPy's
    transcendental functions can also differ by an ULP across platform math
    libraries. Keep column/row order and text exact; normalize only floating
    scalars, never numbers stored as text. Schema/dtypes are checked separately.
    The same function serves reference generation and execution comparison.
    """
    import hashlib
    import numpy as np

    text_frame = frame.astype(str)
    for column in range(len(frame.columns)):
        for row, value in enumerate(frame.iloc[:, column]):
            if isinstance(value, (float, np.floating)):
                text_frame.iat[row, column] = format(value, ".14g")
    text = text_frame.to_csv(index=False, lineterminator="\n")
    return hashlib.sha1(text.encode()).hexdigest()[:12]


def run_outcome(op: str, config: dict, inputs: list) -> dict:
    """Esecuzione reale (dispatcher dell'esecutore Manipola), esito confrontabile."""
    from app.core.manipola.executor import ManipolaExecutor
    try:
        out = ManipolaExecutor(project_service=None)._esegui_trasformazione(_node(op, config), inputs)
    except Exception as error:  # noqa: BLE001 - il confronto riguarda qualunque rifiuto
        return {"ok": False, "err": str(error)[:300]}
    first = inputs[0]
    noop = (list(out.columns) == list(first.columns) and [str(t) for t in out.dtypes] == [str(t) for t in first.dtypes]
            and len(out) == len(first) and _digest(out) == _digest(first))
    return {"ok": True, "cols": [str(c) for c in out.columns], "dtypes": [str(t) for t in out.dtypes],
            "rows": len(out), "noop": noop, "digest": None if op in RANDOM_OPS else _digest(out)}


def valida_outcome(op: str, config: dict, inputs: list) -> list:
    """Issue di Valida (severità, codice) con lo schema esatto degli ingressi."""
    import inspect
    from app.core.manipola.canvas_schema_runtime import build_columns_with_types
    from app.core.manipola.node_validator import ManipolaNodeValidator
    from app.shared.execution.schema_types import InferredSchema
    schemas = []
    for frame in inputs:
        crs = getattr(frame, "crs", None)
        columns = build_columns_with_types(frame, {})
        for column in columns:
            if column["type"] == "geometry" and crs is not None:
                column["type"] = f"geometry:point:{crs.to_epsg()}"
        schemas.append(InferredSchema(columns=columns))
    validator = ManipolaNodeValidator()
    kwargs = {"upstream": InferredSchema(columns=[c for s in schemas for c in s.columns])}
    if "inputs" in inspect.signature(validator.validate_node).parameters:
        kwargs["inputs"] = schemas
    issues = validator.validate_node(_node(op, config), **kwargs)
    return sorted({(issue.severity, issue.code) for issue in issues})


def outcomes() -> dict:
    result = {}
    for key, op, config, kind in corpus():
        result[key] = {"rows": run_outcome(op, config, frames(op, False, kind)),
                       "empty": run_outcome(op, config, frames(op, True, kind)),
                       "valida": [list(item) for item in valida_outcome(op, config, frames(op, False, kind))]}
    return result
