"""Contract test P1-04 / F-DATA-01 — NaN/Inf JSONB sentinel roundtrip.

Scope:
  Fissa la simmetria ser/deser dei float speciali nei row_data JSONB
  persistiti in `plenora.node_rows`. Pre-fix `_to_jsonable` mappava
  NaN/+Inf/-Inf a None: divergenza fantasma con `compute_row_hash` (che
  li preserva come "nan" / "+inf" / "-inf"). Reload da `node_rows` perdeva
  la distinzione e ricalcolare l'hash dava un risultato diverso da quello
  stored.

  Il fix introduce sentinel object JSONB namespaced di forma esatta
  `{"__plenora_special_float__": "nan"|"+inf"|"-inf"}` in `_to_jsonable`
  e un `_from_jsonable_value` simmetrico applicato in `get_node_table_rows`.
  La forma object-namespaced (vs string semplice come "__nan__") evita
  collisioni con valori utente legittimi: un utente che scrive la
  stringa "__nan__" non viene corrotto al reverse.

  Il writer hash (`writer.py`) NON e' toccato: la coppia ser/deser tiene
  l'invariante "round-trip preserva semantica float speciali", e questo
  e' sufficiente perche' `compute_row_hash` resti stabile cross-run.

Sezioni:
  1. Forward: `_to_jsonable` produce dict-sentinel namespaced
  2. Reverse: `_from_jsonable_value` ricostruisce float dai dict-sentinel
  3. Anti-collision: stringhe omonime e dict utente NON vengono corrotti
  4. Round-trip: ser+deser su valori misti preserva semantica
  5. Hash recompute: dopo reverse mapping, `compute_row_hash` produce lo
     stesso digest dell'originale (invariante chiave del fix)
  6. Compat: pre-fix `null` JSONB resta None (no false-positive sentinel)

Tutti i test sono unit puri, in memoria, zero DB. Niente psycopg2,
niente fixture DB-live, niente conftest stub aggiuntivi.
"""

from __future__ import annotations

import math

import pytest


pytestmark = [
    pytest.mark.unit,
    pytest.mark.intermediate,
    pytest.mark.contract,
    pytest.mark.regression,
]


# ---------------------------------------------------------------------------
# 1. Forward: _to_jsonable produce dict-sentinel namespaced
# ---------------------------------------------------------------------------

def test_to_jsonable_nan_returns_sentinel_dict_not_none():
    """NaN -> dict-sentinel namespaced, NON None come pre-fix e
    NON la stringa "__nan__" che colliderebbe con valori utente."""
    from app.core.intermediate.db import _to_jsonable, _SPECIAL_FLOAT_KEY

    out = _to_jsonable(float("nan"))
    assert isinstance(out, dict), f"atteso dict-sentinel, trovato: {type(out).__name__}"
    assert out == {_SPECIAL_FLOAT_KEY: "nan"}
    assert out is not None, "regressione: NaN non deve piu' essere mappato a None"


def test_to_jsonable_pos_inf_returns_sentinel_dict():
    """+Inf -> {"__plenora_special_float__": "+inf"}."""
    from app.core.intermediate.db import _to_jsonable, _SPECIAL_FLOAT_KEY

    assert _to_jsonable(float("inf")) == {_SPECIAL_FLOAT_KEY: "+inf"}


def test_to_jsonable_neg_inf_returns_sentinel_dict():
    """-Inf -> {"__plenora_special_float__": "-inf"}."""
    from app.core.intermediate.db import _to_jsonable, _SPECIAL_FLOAT_KEY

    assert _to_jsonable(float("-inf")) == {_SPECIAL_FLOAT_KEY: "-inf"}


def test_to_jsonable_finite_floats_unchanged():
    """I float finiti restano float (no false-positive sentinel)."""
    from app.core.intermediate.db import _to_jsonable

    assert _to_jsonable(1.23) == 1.23
    assert _to_jsonable(0.0) == 0.0
    assert _to_jsonable(-1.5e10) == -1.5e10


def test_to_jsonable_none_stays_none():
    """None resta None: nessuna confusione tra null pre-fix e sentinel."""
    from app.core.intermediate.db import _to_jsonable

    assert _to_jsonable(None) is None


# ---------------------------------------------------------------------------
# 2. Reverse: _from_jsonable_value ricostruisce float
# ---------------------------------------------------------------------------

def test_from_jsonable_nan_dict_sentinel_restores_nan():
    """Dict-sentinel canonico -> float NaN."""
    from app.core.intermediate.db import _from_jsonable_value, _SPECIAL_FLOAT_KEY

    out = _from_jsonable_value({_SPECIAL_FLOAT_KEY: "nan"})
    assert isinstance(out, float)
    assert math.isnan(out), "atteso NaN dopo reverse mapping"


def test_from_jsonable_pos_inf_dict_sentinel_restores_pos_inf():
    from app.core.intermediate.db import _from_jsonable_value, _SPECIAL_FLOAT_KEY

    out = _from_jsonable_value({_SPECIAL_FLOAT_KEY: "+inf"})
    assert math.isinf(out) and out > 0


def test_from_jsonable_neg_inf_dict_sentinel_restores_neg_inf():
    from app.core.intermediate.db import _from_jsonable_value, _SPECIAL_FLOAT_KEY

    out = _from_jsonable_value({_SPECIAL_FLOAT_KEY: "-inf"})
    assert math.isinf(out) and out < 0


def test_from_jsonable_passes_through_non_sentinel_values():
    """Stringhe non-sentinel, numeri, None, bool: invariati."""
    from app.core.intermediate.db import _from_jsonable_value

    assert _from_jsonable_value("hello") == "hello"
    assert _from_jsonable_value(42) == 42
    assert _from_jsonable_value(3.14) == 3.14
    assert _from_jsonable_value(None) is None
    assert _from_jsonable_value(True) is True


# ---------------------------------------------------------------------------
# 3. Anti-collision: stringhe omonime e dict utente NON vengono corrotti
# ---------------------------------------------------------------------------

def test_user_string_nan_is_not_converted():
    """Un valore utente uguale alla stringa "__nan__" deve restare
    stringa: la forma sentinel e' object, non string. Questo era il
    rischio del primo design rigettato in review."""
    from app.core.intermediate.db import _from_jsonable_value

    assert _from_jsonable_value("__nan__") == "__nan__"


def test_user_string_inf_pos_is_not_converted():
    """Stringa "__inf+__" resta stringa."""
    from app.core.intermediate.db import _from_jsonable_value

    assert _from_jsonable_value("__inf+__") == "__inf+__"


def test_user_string_inf_neg_is_not_converted():
    """Stringa "__inf-__" resta stringa (simmetria col positivo)."""
    from app.core.intermediate.db import _from_jsonable_value

    assert _from_jsonable_value("__inf-__") == "__inf-__"


def test_user_dict_with_sentinel_key_plus_other_keys_is_not_converted():
    """Dict utente che contiene anche `__plenora_special_float__` ma con
    altre chiavi NON e' un sentinel: deve essere ricorso come dict normale,
    non collassato a NaN.
    """
    from app.core.intermediate.db import _from_jsonable_value, _SPECIAL_FLOAT_KEY

    user_dict = {_SPECIAL_FLOAT_KEY: "nan", "other": 1}
    out = _from_jsonable_value(user_dict)

    assert isinstance(out, dict), "dict con altre chiavi NON e' un sentinel"
    assert out[_SPECIAL_FLOAT_KEY] == "nan", "tag preservato come stringa utente"
    assert out["other"] == 1


def test_dict_with_sentinel_key_but_unknown_tag_is_not_converted():
    """Dict di forma `{<key>: <tag>}` ma tag sconosciuto: trattato come
    dict utente normale, non come sentinel. Difesa-in-profondita' contro
    valori utente che casualmente usano la nostra chiave riservata."""
    from app.core.intermediate.db import _from_jsonable_value, _SPECIAL_FLOAT_KEY

    user_dict = {_SPECIAL_FLOAT_KEY: "something_else"}
    out = _from_jsonable_value(user_dict)

    assert isinstance(out, dict)
    assert out == {_SPECIAL_FLOAT_KEY: "something_else"}


def test_from_jsonable_recurses_into_list_and_dict_with_sentinels():
    """Reverse mapping ricorsivo: list/dict propagano la trasformazione,
    i sentinel annidati vengono ricostruiti, gli altri valori invariati."""
    from app.core.intermediate.db import _from_jsonable_value, _SPECIAL_FLOAT_KEY

    sentinel_nan = {_SPECIAL_FLOAT_KEY: "nan"}
    sentinel_inf_pos = {_SPECIAL_FLOAT_KEY: "+inf"}

    out_list = _from_jsonable_value([1, sentinel_nan, "x", sentinel_inf_pos])
    assert out_list[0] == 1
    assert math.isnan(out_list[1])
    assert out_list[2] == "x"
    assert math.isinf(out_list[3]) and out_list[3] > 0

    out_dict = _from_jsonable_value({"a": sentinel_nan, "b": 5, "c": "hello"})
    assert math.isnan(out_dict["a"])
    assert out_dict["b"] == 5
    assert out_dict["c"] == "hello"


# ---------------------------------------------------------------------------
# 4. Round-trip su valori misti
# ---------------------------------------------------------------------------

def test_roundtrip_preserves_mixed_special_floats():
    """Forward + reverse su un dict realistico: valori finiti invariati,
    speciali ricostruiti, valori utente con stringhe omonime invariati."""
    from app.core.intermediate.db import _to_jsonable, _from_jsonable_value

    original = {
        "id": 42,
        "amount": 1234.56,
        "ratio_nan": float("nan"),
        "growth_inf_pos": float("inf"),
        "growth_inf_neg": float("-inf"),
        "name": "row-A",
        # Valore utente che casualmente eguaglia il vecchio sentinel-string:
        # deve restare stringa, non venir corrotto.
        "user_label": "__nan__",
        "missing": None,
    }
    serialized = {k: _to_jsonable(v) for k, v in original.items()}
    deserialized = {k: _from_jsonable_value(v) for k, v in serialized.items()}

    assert deserialized["id"] == 42
    assert deserialized["amount"] == 1234.56
    assert math.isnan(deserialized["ratio_nan"])
    assert math.isinf(deserialized["growth_inf_pos"]) and deserialized["growth_inf_pos"] > 0
    assert math.isinf(deserialized["growth_inf_neg"]) and deserialized["growth_inf_neg"] < 0
    assert deserialized["name"] == "row-A"
    assert deserialized["user_label"] == "__nan__", (
        "Anti-collision: la stringa utente '__nan__' deve restare stringa"
    )
    assert deserialized["missing"] is None


# ---------------------------------------------------------------------------
# 5. Hash recompute: invariante chiave del fix
# ---------------------------------------------------------------------------

def test_compute_row_hash_matches_after_jsonb_roundtrip():
    """L'invariante chiave di F-DATA-01: hash dei dati ricostruiti
    da JSONB deve eguagliare l'hash dei dati originali.

    Pre-fix: NaN -> None alla scrittura, hash recompute usava None
    invece di "nan", digest divergente.
    Post-fix: NaN -> dict-sentinel -> NaN, hash recompute identico.
    """
    from app.core.intermediate.db import _to_jsonable, _from_jsonable_value
    from app.core.intermediate.writer import compute_row_hash

    original_row = {
        "id": 1,
        "amount": 99.5,
        "ratio_nan": float("nan"),
        "growth_inf_pos": float("inf"),
        "growth_inf_neg": float("-inf"),
        "label": "X",
    }
    col_names_sorted = sorted(original_row.keys())
    hash_original = compute_row_hash(original_row, col_names_sorted)

    # Simula il roundtrip JSONB: ser via _to_jsonable (come `insert_node_rows`),
    # poi deser via _from_jsonable_value (come `get_node_table_rows` post-fix).
    serialized = {k: _to_jsonable(v) for k, v in original_row.items()}
    reconstructed = {k: _from_jsonable_value(v) for k, v in serialized.items()}
    hash_reconstructed = compute_row_hash(reconstructed, col_names_sorted)

    assert hash_original == hash_reconstructed, (
        f"Hash divergente dopo JSONB roundtrip: "
        f"original={hash_original} vs reconstructed={hash_reconstructed}. "
        f"F-DATA-01 regressione: il sentinel non e' simmetrico."
    )


def test_compute_row_hash_diverges_pre_fix_with_none():
    """Documenta il bug pre-fix come regression-guard.

    Se qualcuno ripristina il vecchio comportamento (NaN -> None alla
    scrittura), il digest dei dati riletti diverge dall'originale. Il
    test fissa esattamente quella divergenza per evitare che il fix
    venga rimosso silenziosamente.
    """
    from app.core.intermediate.writer import compute_row_hash

    original_row = {"id": 1, "ratio": float("nan")}
    col_names_sorted = sorted(original_row.keys())
    hash_original = compute_row_hash(original_row, col_names_sorted)

    # Comportamento pre-fix: NaN -> None alla persistenza.
    pre_fix_reconstructed = {"id": 1, "ratio": None}
    hash_pre_fix = compute_row_hash(pre_fix_reconstructed, col_names_sorted)

    assert hash_original != hash_pre_fix, (
        "REGRESSIONE: il digest sui dati riletti pre-fix doveva differire "
        "dall'originale. Se il fix sentinel viene rimosso, P1-04 si riapre."
    )


# ---------------------------------------------------------------------------
# 6. Compat con dati pre-fix
# ---------------------------------------------------------------------------

def test_legacy_null_jsonb_stays_none_after_reverse_mapping():
    """Run vecchie hanno NaN persistiti come `null` (pre-fix). Il reverse
    mapping NON deve trasformarli in NaN per retro-compat — solo i
    dict-sentinel canonici vengono ricostruiti come float.
    """
    from app.core.intermediate.db import _from_jsonable_value

    # row_data come arriverebbe da una run pre-fix
    legacy_row_data = {"ratio_was_nan": None, "amount": 99.5}
    out = {k: _from_jsonable_value(v) for k, v in legacy_row_data.items()}

    assert out["ratio_was_nan"] is None
    assert out["amount"] == 99.5
