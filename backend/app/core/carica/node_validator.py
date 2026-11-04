"""Validatore PUT-time per i nodi CARICA.

Riusa la regola pura condivisa ``check_mapping`` per il sottoinsieme comune
(source-exists / dup-source / dup-dest) e copre le **regole extra di carica** che
``check_mapping`` non ha (erano in ``ColumnMapper.validate_mapping``, dead-in-prod):
max-1-geometria, match-column (UPDATE/UPSERT) — altrimenti il PUT sotto-validerebbe
carica (false-OK, viola #B). NB: nessun mapping attivo NON è un errore (carica fa
auto_map a runtime) → si ritorna ``[]``.

Onora lo status: se l'upstream è ``indeterminate`` (schema a monte non
risolvibile), una colonna sorgente mancante è un **warning**, non un error — la
colonna potrebbe esistere a runtime.
"""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from typing import Any, List, Mapping

import re

from app.core.carica.dispatch import select_carica_write_branch
from app.shared.execution.mapping_rules import check_mapping
from app.shared.execution.schema_types import InferredSchema
from app.shared.execution.validation_protocol import register_node_validator
from app.shared.execution.validation_types import Issue

# I node.type che rappresentano un nodo CARICA (allineato a CARICA_NODE_TYPES).
_CARICA_NODE_TYPES = ("carica", "carica_arcgis", "datasource_node")

_HTTP_RE = re.compile(r"^https?://", re.IGNORECASE)


def _err(node_id: str, code: str, message: str) -> Issue:
    return Issue(node_id=node_id, severity="error", code=code, message=message, suggestion=None)


def _node_config(node: Any) -> Mapping[str, Any]:
    data = getattr(node, "data", None) or {}
    config: dict[str, Any] = dict(data) if isinstance(data, Mapping) else {}
    extra = getattr(node, "model_extra", None) or getattr(node, "__pydantic_extra__", None) or {}
    if isinstance(extra, Mapping):
        config.update({k: v for k, v in extra.items() if v is not None})
    for key in (
        "dest_type",
        "connection_id",
        "schema_name",
        "table_name",
        "write_mode",
        "column_mappings",
        "match_columns",
        "rest_url",
        "rest_method",
        "arcgis_connection_id",
        "arcgis_layer_url",
        "arcgis_operation",
        "arcgis_new_service_name",
        "arcgis_match_column",
        "datasource_id",
    ):
        attrs = getattr(node, "__dict__", {})
        if isinstance(attrs, Mapping) and key in attrs:
            value = attrs[key]
        else:
            continue
        if value is not None:
            config[key] = value
    return config


def _carica_config_issues(node_type: str, data: dict, node_id: str) -> List[Issue]:
    """Config-completeness del nodo CARICA (porting fedele dei RULE_ID FE).

    Il branch (db/rest/arcgis/datasource) è scelto con la STESSA logica del
    runtime (`select_carica_write_branch`) → no-drift.
    """
    branch = select_carica_write_branch({**data, "type": node_type})
    out: List[Issue] = []

    if branch == "db":
        if not data.get("connection_id"):
            out.append(_err(node_id, "CARICA_DB_CONNECTION_MISSING",
                            "Carica DB: seleziona una connessione database."))
        if not data.get("schema_name"):
            out.append(_err(node_id, "CARICA_DB_SCHEMA_MISSING",
                            "Carica DB: seleziona uno schema di destinazione."))
        if not data.get("table_name"):
            out.append(_err(node_id, "CARICA_DB_TABLE_MISSING",
                            "Carica DB: specifica il nome della tabella di destinazione."))
        if data.get("write_mode") in ("upsert", "update") and not (data.get("match_columns") or []):
            out.append(_err(node_id, "CARICA_DB_MATCH_COLUMNS_MISSING",
                            f'Carica DB: modalità "{data.get("write_mode")}" richiede almeno '
                            'una colonna chiave in "Colonne di corrispondenza".'))

    elif branch == "rest":
        url = (data.get("rest_url") or "").strip()
        if not url or not _HTTP_RE.match(url):
            out.append(_err(node_id, "CARICA_REST_URL_MISSING",
                            "Carica REST: specifica un endpoint HTTP/HTTPS valido."))
        if (data.get("rest_method") or "POST") not in ("POST", "PUT", "PATCH"):
            out.append(_err(node_id, "CARICA_REST_METHOD_INVALID",
                            "Carica REST: metodo non supportato."))

    elif branch == "arcgis":
        op = data.get("arcgis_operation") or "add"
        if not data.get("arcgis_connection_id"):
            out.append(_err(node_id, "CARICA_ARCGIS_CONNECTION_MISSING",
                            "Carica ArcGIS: seleziona una connessione ArcGIS."))
        if op in ("add", "update", "upsert", "delete") and not data.get("arcgis_layer_url"):
            out.append(_err(node_id, "CARICA_ARCGIS_LAYER_URL_MISSING",
                            "Carica ArcGIS: seleziona il layer di destinazione."))
        if op == "create_service" and not data.get("arcgis_new_service_name"):
            out.append(_err(node_id, "CARICA_ARCGIS_NEW_SERVICE_NAME_MISSING",
                            "Carica ArcGIS: specifica il nome del nuovo Feature Service."))
        if op in ("update", "upsert", "delete") and not (data.get("arcgis_match_column") or "").strip():
            out.append(_err(node_id, "CARICA_ARCGIS_MATCH_COLUMN_MISSING",
                            f'Carica ArcGIS: seleziona la colonna chiave per l\'operazione "{op}".'))

    return out


def _normalize(raw: Any) -> SimpleNamespace:
    """Dict di mapping (JSON canvas) → oggetto duck-typed per check_mapping.

    Tollera gli alias storici source/source_column e dest/dest_column.
    """
    if not isinstance(raw, dict):
        return SimpleNamespace(source_column="", dest_column="", is_geometry=False, skip=True)
    src = (raw.get("source_column") or raw.get("source") or "").strip()
    dst = (raw.get("dest_column") or raw.get("dest") or "").strip()
    return SimpleNamespace(
        source_column=src, dest_column=dst,
        is_geometry=raw.get("is_geometry") is True,
        skip=raw.get("skip") is True,
    )


class CaricaNodeValidator:
    """``validate_node`` per i nodi CARICA."""

    def validate_node(self, node: Any, *, upstream: InferredSchema) -> List[Issue]:
        data = _node_config(node)
        node_id = getattr(node, "id", "")
        node_type = getattr(node, "type", "") or ""

        # Config-completeness (connessione/tabella/url/match...) — fire SEMPRE,
        # indipendente dai mapping.
        issues_cfg = _carica_config_issues(node_type, data, node_id)

        raw_mappings = data.get("column_mappings") or []
        active = [m for m in (_normalize(r) for r in raw_mappings)
                  if m.source_column and m.dest_column and not m.skip]

        # Nessun mapping esplicito: a runtime carica fa auto_map → niente
        # col-resolution da validare a PUT (darebbe falsi positivi).
        if not active:
            return issues_cfg

        columns = {
            c["name"] for c in upstream.columns
            if isinstance(c, dict) and c.get("name")
        }
        issues = issues_cfg + check_mapping(columns, active, node_id=node_id)

        # #A: con upstream indeterminate, "source mancante" è un warning, non error.
        if upstream.names_status == "indeterminate":
            issues = [
                replace(i, severity="warning") if i.code == "mapping.source_missing" else i
                for i in issues
            ]

        # Extra carica #1: al più una colonna geometria.
        geom_dests = [m.dest_column for m in active if m.is_geometry]
        if len(geom_dests) > 1:
            issues.append(Issue(
                node_id=node_id, severity="error", code="mapping.multi_geometry",
                message="Più di una colonna geometria nel mapping: " + ", ".join(geom_dests),
                suggestion={"columns": geom_dests},
            ))

        # Extra carica #2: la colonna match (UPDATE/UPSERT ArcGIS) dev'essere prodotta.
        match = (data.get("arcgis_match_column") or "").strip()
        if match and match not in {m.dest_column for m in active}:
            issues.append(Issue(
                node_id=node_id, severity="error", code="mapping.match_missing",
                message=f"La colonna match '{match}' non è prodotta dal mapping",
                suggestion={"column": match},
            ))

        return issues


def register_carica_validator() -> None:
    """Registra il validatore CARICA al composition-root (#D)."""
    validator = CaricaNodeValidator()
    for node_type in _CARICA_NODE_TYPES:
        register_node_validator(node_type, validator)


__all__ = ["CaricaNodeValidator", "register_carica_validator"]
