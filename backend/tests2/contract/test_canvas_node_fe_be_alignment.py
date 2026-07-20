"""
Contract test: ogni campo che il FE emette per un nodo canvas deve
essere dichiarato sul Pydantic `Node` model.

Motivazione (post audit cross-modulo, 32+ fix):
~12 dei bug nella sessione erano dello stesso pattern: il FE
serializza un campo (es. ``rest_url``, ``dest_type='rest'``,
``rest_method``, ``rest_batch_mode``, ``in/name/value`` per auth
api_key) ma il Pydantic `Node` con `extra="ignore"` (default) lo
droppava silenziosamente al PUT canvas. Il path runtime poi leggeva
``getattr(node, 'rest_url', None)`` -> None -> failure parlante
solo "in profondita'" (es. "CARICA REST url is required" dal
parser config).

Approccio:
1. Il modello `Node` ora usa ``extra="allow"`` + un `model_validator`
   che logga WARNING per ogni campo "non noto" (vedi
   ``app.core.projects.canvas_models``). Questo evita lo
   silent-drop a runtime e produce un segnale strutturato per ops.
2. Questo test, al CI time, fa il check statico: scansiona
   ``frontend/src/utils/canvasSerializer.js`` e per ogni chiave
   emessa dal serializer (sia tramite ``nd(node, 'key', ...)`` sia
   come literal ``key: ...``) verifica che ci sia un campo Pydantic
   ``Node`` con lo stesso nome. Una nuova chiave aggiunta lato FE
   che non passa per il modello fa fallire il test al PR time
   invece che a un audit manuale.

Note di robustezza:
- Falsi positivi accettati piuttosto che falsi negativi: se la regex
  cattura una key che non e' davvero una colonna serializzata (es.
  un nome interno al JS), il test fallisce e la fix e' aggiungere
  l'allowlist locale ``_FE_KEYS_NOT_ON_BE_NODE``. Meglio rumore
  esplicito che drift silenzioso.
- Il parser regex e' deliberatamente conservativo (matcha solo
  pattern strutturali tipici); per JS arbitrario serve un AST
  parser, qui non vale lo sforzo per <1KB di codice da analizzare.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


_FE_SERIALIZER_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent  # backend/../
    / "frontend"
    / "src"
    / "utils"
    / "canvasSerializer.js"
)


# Campi che il FE serializza per ragioni di trasporto/visualizzazione
# e che il BE legittimamente NON modella. Mantenere stretto: ogni entry
# qui dovrebbe spiegare in commento perche' resta fuori dal `Node`.
_FE_KEYS_NOT_ON_BE_NODE: frozenset[str] = frozenset({
    # Chiavi del dict `SERIALIZERS` del FE (mappa node-type ->
    # serializer function). NON sono campi serializzati, sono i nomi
    # dei tipi di nodo. Il regex parser le matcha perche' compaiono
    # come literal-key in un object, ma non sono campi Pydantic.
    "tableNode", "featureLayerNode", "result", "projectSource",
    "fileSource", "datasourceNode", "transform", "api_enrich",
    "staticValues", "multi_join", "carica", "caricaArcgis",
    "comment", "project_input",

    # Envelope del canvas restituito da `serializeCanvas`, non campi Node.
    "nodes", "edges",

    # Campi serializzati per gli **Edge**, non per i Node. Il regex
    # parser non distingue il blocco edge da quello node nel file.
    # Vivono sul modello `Edge` (canvas_models.py) gia' allineato col
    # FE per quei campi.
    "join_type", "is_spatial", "on_filters", "join_conditions",
    "source_column", "target_column",
    "source_geom_column", "target_geom_column",
    "spatial_predicate", "spatial_distance",
})


@pytest.fixture(scope="module")
def fe_emitted_keys() -> set[str]:
    """Set di tutte le chiavi che il serializer FE emette."""
    assert _FE_SERIALIZER_PATH.exists(), (
        f"FE canvasSerializer.js non trovato: {_FE_SERIALIZER_PATH}. "
        "Test contract richiede il frontend co-versionato nella stessa repo."
    )
    src = _FE_SERIALIZER_PATH.read_text(encoding="utf-8")

    keys: set[str] = set()

    # Pattern 1: `nd(node, 'fieldname', default)` o `nd(node, 'fieldname')`
    # — copre il 95% dei campi nelle factory di serializer.
    for m in re.finditer(r"\bnd\s*\(\s*node\s*,\s*['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]", src):
        keys.add(m.group(1))

    # Pattern 2: literal-key dentro un return object: `key: nd(...)`
    # o `key: node.x`. Filtra commenti e righe non-coda.
    for m in re.finditer(r"^\s+([A-Za-z_][A-Za-z0-9_]*)\s*:\s*[^,/]", src, re.MULTILINE):
        # Skip se la parola e' parte di sintassi JS (es. dentro `function`,
        # `const`, ecc.) — il match di un literal key in oggetto avra'
        # tipicamente comma trailing, ma noi siamo conservativi.
        key = m.group(1)
        if key in {"const", "function", "let", "var", "return", "if", "else",
                   "for", "while", "switch", "case", "default", "from", "import",
                   "export"}:
            continue
        keys.add(key)

    return keys


@pytest.fixture(scope="module")
def be_node_field_names() -> set[str]:
    """Set dei field name dichiarati sul Pydantic Node."""
    from app.core.projects.canvas_models import Node

    return set(Node.model_fields.keys())


def test_every_fe_emitted_field_is_declared_on_be_node(
    fe_emitted_keys: set[str], be_node_field_names: set[str]
) -> None:
    """Smoke contract: ogni chiave del serializer ↔ Pydantic Node."""
    assert fe_emitted_keys, "regex parser non ha estratto chiavi dal FE"
    assert be_node_field_names, "Pydantic Node senza campi (??)"

    missing = (
        fe_emitted_keys
        - be_node_field_names
        - _FE_KEYS_NOT_ON_BE_NODE
        - {"id", "type", "position"}  # gia' sul Node base
    )
    if missing:
        # Output dettagliato: stampa cosa il FE emette ma il BE Pydantic
        # non vede. Tipicamente il bug e' "ho aggiunto un campo al FE
        # e ho dimenticato di dichiararlo sul Node": aggiungerlo qui.
        raise AssertionError(
            "Campi che il FE serializer emette ma il Pydantic Node NON "
            "dichiara (drop silenzioso con extra='ignore', ora a runtime "
            "vedrai un WARNING [canvas-contract], ma il fix corretto e' "
            "dichiararli sul modello con il tipo giusto):\n  - "
            + "\n  - ".join(sorted(missing))
            + f"\n\nTotale FE keys parsate: {len(fe_emitted_keys)}, "
            f"BE Node fields: {len(be_node_field_names)}.\n"
            "Se la regex ha catturato un falso positivo, aggiungilo a "
            "`_FE_KEYS_NOT_ON_BE_NODE` in questo file con un commento."
        )


def test_node_model_has_extra_allow_config() -> None:
    """Guard: Node deve mantenere ``extra='allow'``. Senza, il fix
    architetturale di questa famiglia di bug (campi droppati
    silenziosamente) regredisce."""
    from app.core.projects.canvas_models import Node

    cfg = Node.model_config
    extra = cfg.get("extra")
    assert extra == "allow", (
        f"Node.model_config['extra'] = {extra!r} — atteso 'allow'.\n"
        "Cambio non rifiutato qui di proposito: rivedere il decision log "
        "in canvas_models.py prima di tornare a 'ignore' o 'forbid'."
    )


def test_node_warns_on_unknown_extra_field(monkeypatch: pytest.MonkeyPatch) -> None:
    """Guard: il `model_validator` deve emettere WARNING strutturato
    su campi non dichiarati. Senza, il bug torna invisibile.

    Non usiamo `caplog`: il logger applicativo ha un `LogsDBHandler`
    custom che ingoia gli errori via `handleError`, e quel path
    impedisce a caplog di vedere il record in modo affidabile dal
    test. Spy diretto sul metodo `.warning` del logger di
    canvas_models e' piu' robusto.
    """
    from app.core.projects import canvas_models

    captured: list[tuple[object, ...]] = []
    original_warning = canvas_models._canvas_logger.warning

    def spy_warning(msg: str, *args, **kwargs) -> None:
        captured.append((msg, args, kwargs))
        original_warning(msg, *args, **kwargs)

    monkeypatch.setattr(canvas_models._canvas_logger, "warning", spy_warning)

    canvas_models.Node(
        id="n1",
        type="source",
        position={"x": 0, "y": 0},
        # Campo NON dichiarato sul modello né nelle exempt visual extras:
        __test_unknown_field__="sentinel-value",  # type: ignore[call-arg]
    )
    assert any(
        "canvas-contract" in str(call[0])
        and any("__test_unknown_field__" in str(a) for a in call[1])
        for call in captured
    ), (
        f"Nessun WARNING [canvas-contract] emesso per unknown field. "
        f"Captured calls: {captured!r}"
    )
