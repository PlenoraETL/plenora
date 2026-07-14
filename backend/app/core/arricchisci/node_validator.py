"""Validatore PUT-time per i nodi api_enrich (arricchisci).

Regole pure (no DB), porting fedele del validatore FE:
- ARRICCHISCI_API_NOT_SELECTED: nessuna API connessa (``api_id`` assente).
- ARRICCHISCI_PARAM_MAPPING_MISSING: parametro obbligatorio della API (snapshot
  ``node.data.parameters``, la STESSA fonte del validatore FE → no-drift) senza un
  mapping verso una colonna a monte. La parte "nessun mapping" è deterministica
  (errore a prescindere dai dati); la parte "mappato a colonna inesistente" è
  verificata solo se lo schema a monte è noto (#A: indeterminate → non flaggata).

NON porta qui (richiede contesto DB):
- ARRICCHISCI_API_CONNECTION_STALE → richiede la lista delle connessioni API
  disponibili (lookup DB). È iniettata dall'endpoint /validate (DB-aware) in
  ``validate_canvas``; il node-validator resta puro.
"""

from __future__ import annotations

import re
from typing import Any, List, Mapping, MutableMapping

from app.shared.execution.schema_types import InferredSchema
from app.shared.execution.validation_protocol import register_node_validator
from app.shared.execution.validation_types import Issue

_TEMPLATE_REF_RE = re.compile(r"\{([^}]+)\}")
_TRAILING_PAREN_RE = re.compile(r"\s*\(.*\)$")


def _node_config(node: Any) -> Mapping[str, Any]:
    """Return node config from top-level/model_extra plus node.data."""
    data = getattr(node, "data", None) or {}
    config: MutableMapping[str, Any] = dict(data) if isinstance(data, Mapping) else {}
    extra = getattr(node, "model_extra", None) or getattr(node, "__pydantic_extra__", None) or {}
    if isinstance(extra, Mapping):
        config.update({k: v for k, v in extra.items() if v is not None})
    for key in (
        "label",
        "api_name",
        "api_id",
        "parameters",
        "param_mapping",
        "static_columns",
        "static_parameters",
    ):
        attrs = getattr(node, "__dict__", {})
        if isinstance(attrs, Mapping) and key in attrs:
            value = attrs[key]
        else:
            continue
        if value is not None:
            config[key] = value
    return config


class ArricchisciNodeValidator:
    """``validate_node`` per i nodi api_enrich."""

    def validate_node(self, node: Any, *, upstream: InferredSchema) -> List[Issue]:
        node_id = getattr(node, "id", "")
        data = _node_config(node)
        label = data.get("label") or data.get("api_name") or node_id

        if not data.get("api_id"):
            return [Issue(
                node_id=node_id, severity="error",
                code="ARRICCHISCI_API_NOT_SELECTED",
                message=f'Nodo "{label}": nessuna API connessa. Seleziona una connessione API.',
                suggestion=None,
            )]

        issues: List[Issue] = []
        missing = _missing_required_param_mappings(data, upstream)
        if missing:
            issues.append(Issue(
                node_id=node_id, severity="error",
                code="ARRICCHISCI_PARAM_MAPPING_MISSING",
                message=f'Nodo "{label}": parametri obbligatori senza mapping — {", ".join(missing)}.',
                suggestion={"params": missing},
            ))
        return issues


def _missing_required_param_mappings(data: Mapping[str, Any], upstream: InferredSchema) -> List[str]:
    """Porting fedele di ARRICCHISCI_PARAM_MAPPING_MISSING dal validatore FE.

    Per ogni parametro obbligatorio (``required``, ``mode != 'fixed'``, non in
    ``static_columns``/``static_parameters``) verifica che ci sia un mapping a una
    colonna a monte risolvibile. Template ``{col}`` → tutte le ref devono esistere.
    """
    parameters = data.get("parameters")
    if not isinstance(parameters, list):
        return []
    param_mapping = data.get("param_mapping") or {}
    static_columns = set(data.get("static_columns") or [])
    static_parameters = data.get("static_parameters") or {}
    if not isinstance(param_mapping, dict):
        param_mapping = {}
    if not isinstance(static_parameters, dict):
        static_parameters = {}

    upstream_names = {
        c["name"] for c in upstream.columns
        if isinstance(c, dict) and c.get("name")
    }
    # #A: schema a monte non risolvibile → non possiamo confermare l'esistenza
    # della colonna mappata; saltiamo quel ramo (mai falso blocco), ma il ramo
    # "nessun mapping" resta deterministico.
    can_check_columns = upstream.status != "indeterminate" and bool(upstream_names)

    missing: List[str] = []
    for p in parameters:
        if not isinstance(p, dict):
            continue
        name = p.get("name")
        if not name or not p.get("required") or p.get("mode") == "fixed":
            continue
        if name in static_columns or name in static_parameters:
            continue
        mapped = param_mapping.get(name)
        if not mapped:
            missing.append(name)
            continue
        if not can_check_columns:
            continue
        refs = _TEMPLATE_REF_RE.findall(str(mapped))
        if refs:
            if any(r.strip() not in upstream_names for r in refs):
                missing.append(name)
        else:
            col = _TRAILING_PAREN_RE.sub("", str(mapped)).strip()
            if col not in upstream_names:
                missing.append(name)
    return missing


def register_arricchisci_validator() -> None:
    """Registra il validatore api_enrich al composition-root (#D)."""
    register_node_validator("api_enrich", ArricchisciNodeValidator())


__all__ = ["ArricchisciNodeValidator", "register_arricchisci_validator"]
