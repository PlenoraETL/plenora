"""
Contract test: il Pydantic ``Column`` deve mantenere ``extra="allow"``
e preservare i campi extra che il FE emette per ogni colonna nel canvas.

Motivazione (commit ``fe37ccbb``, simmetrico a ``fc86a999`` per ``Node``):
Senza ``extra="allow"`` il default ``ignore`` di Pydantic droppava
silenziosamente i campi extra che il FE serializza dentro ``columns[i]``
(es. ``fromApi``, ``selected``, ``path``). Sintomo: dopo PUT canvas
(salvataggio) -> GET canvas (reload) i flag scomparivano e la
validation FE rigenerava errori falsi positivi tipo
"Parametri obbligatori senza mapping" per i fixed-param marker dei
nodi ``api_enrich``.

Approccio: il modello ``Node`` ha anche un ``model_validator(mode="after")``
che logga WARNING per ogni campo extra non noto; il ``Column`` no, perche'
i campi extra delle colonne sono pochi, ben noti, e cambiano molto meno
frequentemente del Node (Column ha 4 campi BE; Node ne ha 117). Lo
scan-based parser come quello del Node sui ``columns[i]`` darebbe troppo
rumore — le Column nascono inline in 50+ posti del FE (factories,
useMemo, mapping helper). Quindi qui ci limitiamo a due guard di tipo
config + roundtrip su un set documentato di extra noti.
"""

from __future__ import annotations

from app.shared.execution.canvas_column import Column


# Campi extra che il FE serializza per ogni Column al momento del fix
# `fe37ccbb`. Aggiunti qui per documentazione — non sono usati come
# allowlist enforcement (con `extra='allow'` qualsiasi campo passa), ma
# come riferimento dell'audit del momento. Quando un nuovo campo extra
# compare in produzione, vale la pena chiedersi se aggiungerlo come
# campo tipizzato sul modello Column.
_FE_KNOWN_COLUMN_EXTRAS: dict[str, object] = {
    # Marker booleano emesso da ApiEnrichNode.jsx per distinguere le
    # colonne provenienti dall'output API da quelle dell'input pipeline.
    # Lo legge la validation FE per i fixed-param di api_enrich.
    "fromApi": True,
    # Flag ReactFlow di selezione visuale (occasionalmente serializzato
    # dentro la column quando un componente specchia lo stato editor).
    "selected": False,
    # Path JSON di provenienza per colonne flatten_json / output_mapping
    # — usato dalla UI per mostrare l'origine della colonna senza
    # toccare la semantica BE.
    "path": "$.payload.value",
}


def test_column_model_has_extra_allow_config() -> None:
    """Guard: ``Column`` deve mantenere ``extra='allow'``.

    Regressione coperta: senza, i campi FE-only su columns vengono
    droppati silenziosamente al PUT canvas e il roundtrip GET perde
    informazioni (fix originale: commit ``fe37ccbb``).
    """
    cfg = Column.model_config
    extra = cfg.get("extra")
    assert extra == "allow", (
        f"Column.model_config['extra'] = {extra!r} — atteso 'allow'.\n"
        "Cambio non rifiutato qui di proposito: rivedere il decision log "
        "in canvas_models.py prima di tornare a 'ignore' o 'forbid'."
    )


def test_column_preserves_unknown_fe_extra_fields_in_roundtrip() -> None:
    """Parse + dump di una Column con campi FE extra deve preservarli.

    Senza ``extra='allow'`` ``model_dump()`` ometterebbe i campi non
    dichiarati. Test sentinel per ognuno dei campi extra noti.
    """
    payload = {
        "name": "geom",
        "alias": None,
        "visible": True,
        "type": "str",
        **_FE_KNOWN_COLUMN_EXTRAS,
    }
    col = Column.model_validate(payload)
    dumped = col.model_dump()
    for key, expected in _FE_KNOWN_COLUMN_EXTRAS.items():
        assert key in dumped, (
            f"Column.model_dump() ha droppato il campo extra '{key}': "
            f"presente nel payload FE ma assente nel dump. Probabile "
            f"regressione di extra='allow'.\nDump completo: {dumped!r}"
        )
        assert dumped[key] == expected, (
            f"Column.model_dump()['{key}'] = {dumped[key]!r}, atteso "
            f"{expected!r}. Coercion inaspettata su un campo extra."
        )


def test_column_accepts_arbitrary_future_fe_extra_field() -> None:
    """Sentinel di forward-compatibility: un campo extra mai visto prima
    non deve far fallire la validation. Se questo test rompe, qualcuno
    ha cambiato extra='allow' a 'forbid' o ha aggiunto un model_validator
    troppo restrittivo.
    """
    col = Column.model_validate({
        "name": "x",
        "__some_future_fe_field__": "sentinel-value",
    })
    dumped = col.model_dump()
    assert dumped.get("__some_future_fe_field__") == "sentinel-value", (
        "Column non preserva un campo extra arbitrario nel roundtrip. "
        f"Dump: {dumped!r}"
    )
