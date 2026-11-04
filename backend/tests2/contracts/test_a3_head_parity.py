"""A3, correzione dopo la conferma — regola madre: esecuzione = verità.

La preparazione condivisa non può rifiutare né cambiare ciò che l'esecuzione
di HEAD 3c8609fe eseguiva con successo. Guardia di enumerazione contro gli
esiti REGISTRATI su HEAD (fixture ``a3_head_outcomes.json``, generata sul
riferimento da ``generate_a3_head_outcomes.py``, mai dal working tree):

- corpus ``a3_runtime_corpus``: configurazioni valide A1, ogni parametro
  opzionale significativo (es. ``stratify_column``) e tutti insieme, varianti
  generiche, corpus di formule (risolutori di pandas.eval: ``inf``, ``Inf``,
  ``index``, nome dell'indice, funzioni matematiche...);
- con righe e su ZERO righe: HEAD riuscita ⇒ WT riuscita con stesse colonne,
  dtype, numero di righe e valori; HEAD rifiuto ⇒ WT rifiuto con lo stesso
  messaggio, salvo le correzioni volute dichiarate in ``CORREZIONI_VOLUTE``;
- Valida: un errore di HEAD non diventa avviso, salvo il parametro
  obbligatorio che il runtime completa con il default del registry.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests2.contracts.a3_runtime_corpus import REGISTRY, corpus, frames, run_outcome

pytestmark = [pytest.mark.unit, pytest.mark.contract]

HEAD = json.loads(Path(__file__).with_name("a3_head_outcomes.json").read_text(encoding="utf-8"))
CORPUS = corpus()

# (operazione, modalità, frammento del rifiuto di HEAD) → motivo. Solo rifiuti
# di HEAD che ora riescono: la regola madre vieta il verso opposto.
CORREZIONI_VOLUTE = {
    ("bin", "empty", "Cannot cut empty array"):
        "pandas rifiutava di tagliare un array vuoto anche con parametri validi (terza sessione A3)",
    ("formula", "empty", "s + 'x'"):
        "su zero righe la conversione numerica delle colonne testo sceglieva il numerico (terza sessione A3)",
}


def _allowed_correction(op: str, mode: str, head: dict) -> bool:
    return any(op == c_op and mode == c_mode and fragment in head.get("err", "")
               for c_op, c_mode, fragment in CORREZIONI_VOLUTE)


def test_corpus_is_the_recorded_one():
    assert set(HEAD) == {key for key, *_ in CORPUS}
    assert {op for _, op, *_ in CORPUS} == set(REGISTRY), "ogni operazione del registry è nel corpus"


@pytest.mark.parametrize("key,op,config,kind", CORPUS, ids=[key[:90] for key, *_ in CORPUS])
@pytest.mark.parametrize("mode", ["rows", "empty"])
def test_execution_equals_head(mode, key, op, config, kind):
    head = HEAD[key][mode]
    actual = run_outcome(op, config, frames(op, mode == "empty", kind))
    if head["ok"]:
        assert actual["ok"], f"HEAD eseguiva, ora rifiuta: {actual.get('err')}"
        assert {k: actual[k] for k in ("cols", "dtypes", "rows", "digest")} == \
               {k: head[k] for k in ("cols", "dtypes", "rows", "digest")}
    elif actual["ok"]:
        assert _allowed_correction(op, mode, head), f"HEAD rifiutava ({head['err']}), ora esegue senza correzione dichiarata"
    else:
        assert actual["err"] == head["err"]


def _demotions_allowed(op: str, config: dict, kind: str) -> bool:
    from tests2.contracts.a3_runtime_corpus import _node
    from app.core.manipola.canvas_schema_runtime import build_columns_with_types
    from app.core.manipola.node_validator import ManipolaNodeValidator
    from app.shared.execution.schema_types import InferredSchema
    inputs = frames(op, False, kind)
    schemas = []
    for frame in inputs:
        columns = build_columns_with_types(frame, {})
        crs = getattr(frame, "crs", None)
        for column in columns:
            if column["type"] == "geometry" and crs is not None:
                column["type"] = f"geometry:point:{crs.to_epsg()}"
        schemas.append(InferredSchema(columns=columns))
    issues = ManipolaNodeValidator().validate_node(
        _node(op, config), upstream=InferredSchema(columns=[c for s in schemas for c in s.columns]), inputs=schemas)
    params = {param["id"]: param for param in REGISTRY[op].get("parameters", [])}
    warnings = [issue for issue in issues if issue.severity == "warning"]
    return bool(warnings) and all(
        issue.code == "TRANSFORM_PARAM_REQUIRED"
        and params[issue.suggestion["param"]].get("default") not in (None, "", [], {})
        for issue in warnings)


@pytest.mark.parametrize("key,op,config,kind", CORPUS, ids=[key[:90] for key, *_ in CORPUS])
def test_head_validation_errors_are_not_demoted(key, op, config, kind):
    from tests2.contracts.a3_runtime_corpus import valida_outcome
    head_errors = {code for severity, code in HEAD[key]["valida"] if severity == "error"}
    if not head_errors:
        return
    actual = valida_outcome(op, config, frames(op, False, kind))
    if any(severity == "error" for severity, _ in actual):
        return
    assert _demotions_allowed(op, config, kind), f"HEAD dava errore {sorted(head_errors)}, ora {actual}"
