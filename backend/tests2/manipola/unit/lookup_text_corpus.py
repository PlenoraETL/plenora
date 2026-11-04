"""Corpus del Lookup su colonne di TESTO (F3, correzione dopo la conferma).

Lo stesso modulo gira sul riferimento HEAD 1c654091 (generazione della
fixture ``lookup_text_head_outcomes.json``) e sul working tree (guardia
``test_lookup_compare_as_text.py``): dipende solo da pandas e da ``op_lookup``.

Assi: frame di TESTO (object con null/vuoti/spazi, dtype ``str`` e
``string`` di pandas, ``category``, object misto con numeri/booleani/NaN,
virgola decimale) x forma della mappa (dizionario, ``from/to``,
``old_name/new_name``) x mappa (testo, numeri come testo, virgola decimale,
vuoto, null, numeri veri; chiavi JSON numeriche, booleane, null e la
collisione 1/True in entrambi gli ordini) x «Valori non mappati»
(assente/None/""/keep/null/value) x default x colonna di uscita.

Le mappe sono liste di coppie (chiave, valore): la forma dizionario le
raccoglie con ``dict`` (le chiavi uguali per Python, come 1 e True,
collassano come farebbe la normalizzazione della lista).

Generazione (procedura dichiarata nel rapporto A3, «F3 — correzione dopo la
conferma»)::

    git archive 1c654091 backend | tar -x -C <scratch>/lookup-head
    cp tests2/manipola/unit/lookup_text_corpus.py <scratch>/lookup-head/backend/tests2/manipola/unit/
    cd <scratch>/lookup-head/backend && python tests2/manipola/unit/lookup_text_corpus.py <dest>
"""
from __future__ import annotations

import itertools
import json
import sys
from pathlib import Path

import pandas as pd

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

FIXTURE_PATH = Path(__file__).with_name("lookup_text_head_outcomes.json")

FRAMES = {
    "object": lambda: pd.DataFrame({
        "s": pd.Series(["a", "b", "5", "", "  ", None, "a", "1,5"], dtype=object),
        "other": ["x"] * 8}),
    "str": lambda: pd.DataFrame({
        "s": pd.Series(["a", "b", "5", None, "a"], dtype="str"),
        "other": ["x"] * 5}),
    "decimal": lambda: pd.DataFrame({
        "s": pd.Series(["1,5", "2.5", "x", "1,5"], dtype=object),
        "other": ["y"] * 4}),
    "string": lambda: pd.DataFrame({
        "s": pd.Series(["a", "b", "5", None, "True", "  "], dtype="string"),
        "other": ["x"] * 6}),
    "category": lambda: pd.DataFrame({
        "s": pd.Series(["a", "b", "5", None, "a", "True"], dtype="category"),
        "other": ["x"] * 6}),
    "mixed": lambda: pd.DataFrame({
        "s": pd.Series(["a", 5, 5.0, True, 1, None, "5", float("nan"), False, 0, 2.5, "True"], dtype=object),
        "other": ["z"] * 12}),
}

VALUES = {
    "text": [("a", "A"), ("b", "B")],
    "numeric_text": [("a", "10"), ("5", "50")],
    "decimal_comma": [("a", "1,5"), ("1,5", "2,5")],
    "empty_and_null": [("a", ""), ("b", None)],
    "true_numbers": [("a", 7), ("5", 2.5)],
    "mixed": [("a", "testo"), ("b", "3")],
    # Chiavi JSON non di testo ({"from": 5}, {"from": true}, {"from": null}).
    "numeric_keys": [(5, "cinque"), (2.5, "due e mezzo"), (0, "zero")],
    "bool_keys": [(True, "vero"), (False, "falso")],
    "collision_1_true": [(1, "uno"), (True, "vero")],
    "collision_true_1": [(True, "vero"), (1, "uno")],
    "null_key": [(None, "NUL"), ("a", "A")],
    "null_key_numeric": [(None, 9), (5, 1)],
}


def _shapes(pairs: list) -> dict:
    return {
        "dict": dict(pairs),
        "from_to": [{"from": k, "to": v} for k, v in pairs],
        "old_new": [{"old_name": k, "old_name_id": "id", "new_name": v} for k, v in pairs],
    }


# None coincide con l'assente (valore predefinito del parametro di op_lookup).
UNMATCHED = ["<assente>", "", "keep", "null", "value"]
DEFAULTS = ["<assente>", "x", "2,5", 3]
OUTPUTS = ["<assente>", "out", "s", "other"]


def corpus() -> list[tuple[str, str, dict]]:
    entries = []
    for frame, (vname, values), unmatched, default, output in itertools.product(
            FRAMES, VALUES.items(), UNMATCHED, DEFAULTS, OUTPUTS):
        for shape, mapping in _shapes(values).items():
            config = {"column": "s", "mapping": mapping}
            if unmatched != "<assente>":
                config["unmatched"] = unmatched
            if default != "<assente>":
                config["default"] = default
            if output != "<assente>":
                config["output_column"] = output
            key = f"{frame}|{vname}|{shape}|u={unmatched!r}|d={default!r}|o={output!r}"
            entries.append((key, frame, config))
    return entries


def _cell(value) -> str:
    try:
        if pd.isna(value):
            return "<null>"
    except (TypeError, ValueError):
        pass
    return f"{type(value).__name__}:{value!r}"


def outcome(frame_name: str, config: dict) -> dict:
    from app.core.manipola.transforms.analysis import op_lookup
    frame = FRAMES[frame_name]()
    try:
        out = op_lookup(frame, **config)
    except Exception as error:  # noqa: BLE001 - si confronta anche il rifiuto
        return {"ok": False, "err": str(error)[:300]}
    import hashlib
    cells = json.dumps([[_cell(v) for v in out[c].tolist()] for c in out.columns], ensure_ascii=False)
    return {"ok": True, "cols": [str(c) for c in out.columns], "dtypes": [str(t) for t in out.dtypes],
            "values": hashlib.sha1(cells.encode("utf-8")).hexdigest()[:16]}


def outcomes() -> dict:
    return {key: outcome(frame, config) for key, frame, config in corpus()}


if __name__ == "__main__":
    import logging
    import warnings
    warnings.filterwarnings("ignore")
    logging.disable(logging.CRITICAL)
    dest = Path(sys.argv[1]) if len(sys.argv) > 1 else FIXTURE_PATH
    data = outcomes()
    lines = [f"{json.dumps(k, ensure_ascii=False)}: {json.dumps(data[k], sort_keys=True, ensure_ascii=False)}"
             for k in sorted(data)]
    dest.write_text("{\n" + ",\n".join(lines) + "\n}\n", encoding="utf-8")
    print(f"{len(data)} voci -> {dest}")
