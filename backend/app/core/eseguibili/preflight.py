"""Stima prima di avviare un eseguibile versionato (Fase 5 T2, BA2).

Lo stesso contratto del preflight dell'editor (``POST /projects/{id}/preflight``)
applicato alle esecuzioni degli Eseguibili:

- la stima usa GLI STESSI input dell'avvio (file caricati compresi), sulla
  versione che partira', senza eseguire API esterne;
- l'impronta (``preparation_fingerprint``) riassume versione, input, gruppo,
  utente che esegue (input «Utente che esegue») e connettori API usati: l'avvio (``POST /eseguibili/{id}/runs``) la ricalcola
  e, se diversa, rifiuta con 409 ``preparation_changed`` (la finestra rifa'
  la stima e mostra il motivo);
- i file temporanei creati per leggere gli input si eliminano sempre; gli
  upload NON si consumano (servono all'avvio).

Solo Arricchisci ha una stima delle chiamate; per gli altri tipi la risposta
ha ``nodes: []`` e l'impronta.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping, Optional

from app.core.arricchisci.api_connector import api_connection_service
from app.core.arricchisci.canvas_preflight import estimate_canvas
from app.core.orchestrator.execution_contract import PipelineExecutionContract
from app.shared.contracts.payloads import JsonObject
from app.shared.execution.node_fields import node_field
from app.shared.execution.runtime_bound_fields import runtime_bound_context
from app.shared.execution.runtime_identity import RuntimeIdentity, resolve_identity_inputs

from . import db as eseguibili_db
from . import service as eseguibili_service
from .runtime import resolve_runtime_inputs

PREFLIGHT_CHECKS = {
    "runtime_inputs": "verified",
    "external_dependencies": "not_guaranteed",
    "resources": "not_guaranteed",
}


def _public_inputs(value: Any) -> Any:
    """Input senza i campi interni (``_upload_group_id``): stessa impronta per
    stima e avvio."""
    if isinstance(value, Mapping):
        return {k: _public_inputs(v) for k, v in value.items() if not str(k).startswith("_")}
    if isinstance(value, list):
        return [_public_inputs(v) for v in value]
    return value


def api_connections_of(canvas: Mapping[str, Any]) -> JsonObject:
    """Connettori API dei nodi di arricchimento della versione."""
    connections: JsonObject = {}
    for node in canvas.get("nodes") or []:
        if not isinstance(node, Mapping) or node.get("type") != "api_enrich":
            continue
        api_id = node_field(node, "api_id")
        if not api_id or api_id in connections:
            continue
        api = api_connection_service.get_api_connection(api_id, include_token=True)
        if not api:
            raise ValueError(f"API '{api_id}' non trovata")
        connections[api_id] = api
    return connections


def preparation_fingerprint(
    *,
    eseguibile_id: str,
    version_id: str,
    group_id: str,
    raw_inputs: Mapping[str, Any],
    connections: JsonObject,
    identity_values: Mapping[str, str],
) -> str:
    content = {
        "eseguibile_id": eseguibile_id,
        "version_id": version_id,
        "group_id": group_id,
        "inputs": _public_inputs(dict(raw_inputs)),
        # Valori iniettati dal server (username di chi esegue): due utenti
        # diversi = due preparazioni diverse.
        "identity": dict(identity_values),
        "connections": connections,
    }
    return hashlib.sha256(
        json.dumps(content, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":")).encode()
    ).hexdigest()


def fingerprint_for(
    *,
    eseguibile: eseguibili_db.EseguibileRow,
    raw_inputs: Mapping[str, Any],
    identity: RuntimeIdentity,
    version_id: Optional[str] = None,
) -> str:
    """Impronta della preparazione di questa esecuzione (per l'avvio)."""
    version, manifest = eseguibili_service.resolve_version_manifest(eseguibile, version_id)
    return preparation_fingerprint(
        eseguibile_id=eseguibile.id,
        version_id=version.id,
        group_id=eseguibile.group_id,
        raw_inputs=raw_inputs,
        connections=api_connections_of(version.canvas),
        identity_values=resolve_identity_inputs(manifest.inputs, identity=identity, client_values=raw_inputs),
    )


def preflight_versioned(
    *,
    eseguibile: eseguibili_db.EseguibileRow,
    raw_inputs: Mapping[str, Any],
    identity: RuntimeIdentity,
    version_id: Optional[str] = None,
) -> JsonObject:
    """Stima dell'esecuzione con questi input. Errori di preparazione =
    ``ValueError`` con il motivo (il router risponde 400)."""
    version, manifest = eseguibili_service.resolve_version_manifest(eseguibile, version_id)
    connections = api_connections_of(version.canvas)
    fingerprint = preparation_fingerprint(
        eseguibile_id=eseguibile.id,
        version_id=version.id,
        group_id=eseguibile.group_id,
        raw_inputs=raw_inputs,
        connections=connections,
        identity_values=resolve_identity_inputs(manifest.inputs, identity=identity, client_values=raw_inputs),
    )
    if manifest.project_type != "arricchisci":
        return {"valid": True, "nodes": [], "preparation_fingerprint": fingerprint, "checks": dict(PREFLIGHT_CHECKS)}

    canvas, cleanup_ids, _summary = resolve_runtime_inputs(
        eseguibile_id=eseguibile.id,
        canvas=version.canvas,
        manifest=manifest,
        values=raw_inputs,
        group_id=eseguibile.group_id,
        identity=identity,
    )
    try:
        runtime_connection_id = eseguibili_service.resolve_runtime_connection_id(
            manifest,
            raw_inputs=raw_inputs,
            group_id=eseguibile.group_id,
        )
        contract = PipelineExecutionContract(
            schema_version=2,
            project_id=eseguibile.id,
            project_type=manifest.project_type,
            group_id=eseguibile.group_id,
            canvas=canvas,
            artifact_refs={},
            runtime_context={"default_connection_id": runtime_connection_id, **runtime_bound_context(canvas)},
        )
        nodes = estimate_canvas(contract, connections)
    finally:
        # I file temporanei letti per la stima non restano: l'avvio li rifa'.
        eseguibili_service.discard_temp_file_sources(cleanup_ids)
    return {
        "valid": all(node.get("valid", True) for node in nodes),
        "nodes": nodes,
        "preparation_fingerprint": fingerprint,
        "checks": dict(PREFLIGHT_CHECKS),
    }
