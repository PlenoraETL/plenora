"""
Canvas type enumerations — single source of truth FE↔BE.

Tutti i `Literal[...]` shared tra Pydantic models del canvas e il
frontend vivono qui come `TypeAlias`. Il modulo `canvas_models.py`
li importa al posto di ridefinire Literal inline; il frontend
(quando il mirror JS sara` in piedi) li riprende dal sibling
`frontend/src/constants/canvasTypes.js` con valori IDENTICI.

Pattern:
    NodeTypeLiteral = Literal["source", "result", ...]
    VALID_NODE_TYPES = get_args(NodeTypeLiteral)  # tuple a runtime

Il `TypeAlias` Literal e' la fonte di verita` typing; le tuple sono
derivate via `typing.get_args()` per uso runtime (membership check,
serializzazione FE mirror, contract test).

Convention di bump:
    - Aggiungere un valore a un Literal e' additive: bumpare
      API_VERSION minor.
    - Rimuovere o rinominare un valore e' breaking: bumpare
      API_VERSION major + migration sui canvas legacy.

Drift attualmente NON corretti (vedi commit message di
introduzione): conflict_policy "update" vs "force" canvas/executor,
rest_method DELETE assente FE, dest_type "arcgis" non offerto da
CaricanNode UI.
"""

from __future__ import annotations

from typing import Literal, get_args


# Pipeline node types — dominio dei nodi disegnabili sul canvas.
# Il valore NON ha alias case-variant; `normalize_type` in canvas_models.py
# mappa eventuali camelCase legacy del frontend (es. "caricaArcgis") sul
# nome snake_case canonico ("carica_arcgis") prima della validazione.
NodeTypeLiteral = Literal[
    "source", "result", "project_source", "file_source", "feature_source",
    "transform", "api_enrich", "staticValues", "carica", "carica_arcgis",
    "multi_join", "comment", "datasource_node", "imdf_publisher",
]
VALID_NODE_TYPES: tuple[str, ...] = get_args(NodeTypeLiteral)


# Column data types. I sottotipi geometry sono enumerati nel ColumnType
# completo di canvas_models.py; qui esponiamo i 6 base + "geometry"
# generico. La validazione fine sui sottotipi geometry vive nel feature
# layer / nodo geometric, non sul Column generale.
ColumnTypeBaseLiteral = Literal[
    "int", "real", "str", "bool", "date", "geometry",
]
VALID_COLUMN_TYPES_BASE: tuple[str, ...] = get_args(ColumnTypeBaseLiteral)


# Carica write modes — comportamento al momento della scrittura DB.
# Subset utilizzabile da CRON (ScheduleWizard): append, replace, upsert
# (gli altri richiedono interazione utente per match_columns/conflitti).
WriteModeLiteral = Literal[
    "create", "replace", "append", "upsert", "update", "truncate_insert",
]
VALID_WRITE_MODES: tuple[str, ...] = get_args(WriteModeLiteral)


# Carica destination type.
DestTypeLiteral = Literal["db", "arcgis", "rest"]
VALID_DEST_TYPES: tuple[str, ...] = get_args(DestTypeLiteral)


# Carica transaction granularity.
TransactionModeLiteral = Literal["all_or_nothing", "per_batch"]
VALID_TRANSACTION_MODES: tuple[str, ...] = get_args(TransactionModeLiteral)


# Carica conflict policy — comportamento del CARICA su righe in conflitto
# con i match_columns. Allineato con `app.core.carica.models.ConflictPolicy`
# enum (SKIP, FORCE, ABORT) che e' canonico dell'executor + col FE.
# Prima del fix dei drift il canvas Pydantic accettava "update" (bug
# pre-esistente, valore non corrispondente a nulla nell'executor): il
# validator `_normalize_conflict_policy` su Node mappa "update" -> "force"
# per preservare canvas legacy eventualmente salvati con il valore
# sbagliato.
ConflictPolicyLiteral = Literal["abort", "skip", "force"]
VALID_CONFLICT_POLICIES: tuple[str, ...] = get_args(ConflictPolicyLiteral)
# Legacy mapping per validator backward-compat. Bumpa al fix di
# ulteriori drift.
LEGACY_CONFLICT_POLICY_ALIASES: dict[str, str] = {
    "update": "force",
}


# REST destination HTTP method (per carica con dest_type='rest').
# DELETE rimosso post fix-drift (commit successivo al lockin): CARICA
# su DELETE non ha semantica (cosa cancelli?), il FE non l'ha mai
# offerto. Nessuna migration: canvas legacy con rest_method="DELETE"
# verrebbero rifiutati dal Pydantic; se mai dovessero comparire (solo
# scripts che hanno bypassato il FE) il fail e' visibile e gestibile
# caso per caso.
RestMethodLiteral = Literal["POST", "PUT", "PATCH"]
VALID_REST_METHODS: tuple[str, ...] = get_args(RestMethodLiteral)


# REST batching mode — invio batch unico vs per record.
RestBatchModeLiteral = Literal["batch", "per_record"]
VALID_REST_BATCH_MODES: tuple[str, ...] = get_args(RestBatchModeLiteral)


# Multi-join semantica (multi_join node).
MultiJoinTypeLiteral = Literal["inner", "left", "outer"]
VALID_MULTI_JOIN_TYPES: tuple[str, ...] = get_args(MultiJoinTypeLiteral)


__all__ = [
    "NodeTypeLiteral", "VALID_NODE_TYPES",
    "ColumnTypeBaseLiteral", "VALID_COLUMN_TYPES_BASE",
    "WriteModeLiteral", "VALID_WRITE_MODES",
    "DestTypeLiteral", "VALID_DEST_TYPES",
    "TransactionModeLiteral", "VALID_TRANSACTION_MODES",
    "ConflictPolicyLiteral", "VALID_CONFLICT_POLICIES",
    "RestMethodLiteral", "VALID_REST_METHODS",
    "RestBatchModeLiteral", "VALID_REST_BATCH_MODES",
    "MultiJoinTypeLiteral", "VALID_MULTI_JOIN_TYPES",
]
