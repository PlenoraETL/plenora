"""Validatore PUT-time per i nodi Manipola (transform, multi_join, result).

Regola unica per i vincoli di esecuzione (A3, P1+P2): Valida di un passo =
**esecuzione a vuoto** della stessa operazione (``app.core.manipola.dry_run``).
Ingressi con zero righe e lo schema inferito di ciascuna maniglia, stesso
percorso dell'esecuzione (risoluzione dei riferimenti per ID, normalizzazione,
preparazione, funzione). Un'eccezione è un errore con lo stesso messaggio del
runtime; nessuna regola di configurazione duplicata qui.

Restano soltanto regole che NON sono vincoli di esecuzione:

- strutturali: operazione assente o fuori registry (l'esecuzione a vuoto non
  è definita);
- di schema a monte, su informazioni che zero righe non portano: tipo
  geometrico del parametro geometria, tabella tipi/aggregazioni generata
  dall'esecuzione (``aggregation_output_type``), collisioni della rinomina;
- regole di Valida più severe dell'esecuzione (come HEAD 3c8609fe, senza
  cambiare l'esecuzione): colonna configurata assente (``column.missing``) e
  parametro obbligatorio non configurato (``TRANSFORM_PARAM_REQUIRED``) sono
  ERRORI, perché l'esecuzione li tollera producendo in silenzio un no-op o un
  risultato diverso dall'intenzione (md5/mask non applicati, rinomina o cast
  ignorati...). Restano avvisi soltanto dove il runtime dà davvero il
  risultato voluto (``_runtime_default``): parametro obbligatorio ASSENTE con un
  default non vuoto nel registry, che il runtime applica (un valore presente ma
  vuoto resta errore); la selezione delle
  colonne (Select), che ignora per contratto le colonne assenti; nomi a monte
  incerti. La guardia ``test_a3_dry_run_parity`` verifica sul runtime con
  righe che ogni no-op o rifiuto sia un errore e che ogni avviso dia lo stesso
  risultato del default.

Con nomi a monte ``indeterminate`` un rifiuto è ``warning``; con tipi incerti
o CRS ignoto lo è solo se dipende da essi (``DryRunOutcome.data_dependent``).
"""

from __future__ import annotations

from typing import Any, List

from app.core.manipola.dry_run import dry_run_multi_join, dry_run_transform
from app.core.manipola.plugin_executor import get_executor
from app.core.manipola.preparation import rename_collisions
from app.core.manipola.types import JsonObject
from app.shared.execution.column_selection import select_column_plan
from app.shared.execution.config_column_refs import (
    referenced_column_names,
    resolve_config_column_refs,
)
from app.shared.execution.node_fields import node_field
from app.shared.execution.registry_types import parameter_value, require_known_param_type
from app.shared.execution.result_grouping import result_group_plan
from app.shared.execution.schema_column_plans import aggregation_output_type
from app.shared.execution.schema_types import InferredSchema
from app.shared.execution.validation_protocol import register_node_validator
from app.shared.execution.validation_types import Issue

_MANIPOLA_NODE_TYPES = ("transform", "multi_join", "result")


def _is_empty_param_value(val: Any) -> bool:
    """Param vuoto, stessa nozione del validatore FE (T2)."""
    return val is None or val == "" or (isinstance(val, (list, dict)) and len(val) == 0)


def _param_value_for_required_check(transform_op: str, param_id: str, config: JsonObject) -> Any:
    if transform_op == "filter" and config.get("conditions") is not None and param_id in {"column", "operator", "value"}:
        # Con le condizioni il campo vive in ogni condizione: e' compilato
        # solo se lo e' in tutte (residui collaudo live L4b).
        conditions = config.get("conditions")
        if param_id == "value" or not isinstance(conditions, list) or not conditions:
            return True
        return all(isinstance(c, dict) and not _is_empty_param_value(c.get(param_id)) for c in conditions) or None
    if transform_op == "formula" and config.get("items") is not None and param_id in {"new_column", "formula"}:
        return True
    return parameter_value(config, param_id)


def _runtime_default(param: JsonObject, config: JsonObject) -> bool:
    """Parametro obbligatorio che il runtime completa con il default del registry.

    Il runtime applica il default solo se la chiave MANCA: un valore presente
    ma vuoto ("" o null, come lo manda l'interfaccia svuotando un campo) arriva
    tale e quale all'operazione, quindi resta un errore di Valida.
    """
    return param.get("id") not in config and not _is_empty_param_value(param.get("default"))


#: Il messaggio di un campo obbligatorio vuoto: il nome del campo come lo
#: mostra il pannello (``label`` del registry), mai l'id o il valore.
REQUIRED_PARAM_MESSAGE = "Compila «{label}»: il campo è obbligatorio."


def required_param_issues(node_id: str, transform_op: str, config: JsonObject, spec: JsonObject) -> List[Issue]:
    """Campi obbligatori del registry non compilati (``TRANSFORM_PARAM_REQUIRED``).

    Errore di Valida (come HEAD): senza il parametro il runtime fa un no-op o
    altro dall'intenzione. Avviso solo se il runtime applica il default.
    """
    out = []
    for param in spec.get("parameters") or []:
        pid = param.get("id")
        if not pid or not param.get("required"):
            continue
        if not _is_empty_param_value(_param_value_for_required_check(transform_op, pid, config)):
            continue
        out.append(Issue(node_id=node_id, severity="warning" if _runtime_default(param, config) else "error",
                         code="TRANSFORM_PARAM_REQUIRED",
                         message=REQUIRED_PARAM_MESSAGE.format(label=param.get("label") or pid),
                         suggestion={"param": pid}))
    return out


def _uncertain(schemas: list[InferredSchema]) -> bool:
    """Nomi a monte incerti: i tipi incerti non degradano gli errori sui nomi."""
    return any(item.names_status == "indeterminate" for item in schemas)


def _node_config(node: Any) -> JsonObject:
    data = getattr(node, "data", None) or {}
    config = getattr(node, "config", None) or (data.get("config") if isinstance(data, dict) else None) or {}
    return config if isinstance(config, dict) else {}


class ManipolaNodeValidator:
    """``validate_node`` per i nodi Manipola."""

    def validate_node(self, node: Any, *, upstream: InferredSchema, inputs: list[InferredSchema] | None = None) -> List[Issue]:
        node_type = getattr(node, "type", "transform")
        schemas = list(inputs or [upstream])
        if node_type == "result":
            return self._validate_result(node, upstream)
        if node_type == "multi_join":
            return self._dry_run_issues(node, dry_run_multi_join(node, schemas), schemas + [upstream])
        return self._validate_transform(node, upstream, schemas)

    # Esecuzione a vuoto ------------------------------------------------------

    def _dry_run_issues(self, node: Any, outcome: Any, schemas: list[InferredSchema]) -> List[Issue]:
        if outcome.verdict != "rejected":
            return []
        warning = outcome.data_dependent or _uncertain(schemas)
        return [Issue(node_id=getattr(node, "id", ""), severity="warning" if warning else "error",
                      code="preparation.invalid", message=outcome.message, suggestion=None)]

    def _validate_transform(self, node: Any, upstream: InferredSchema, schemas: list[InferredSchema]) -> List[Issue]:
        node_id = getattr(node, "id", "")
        data = getattr(node, "data", None) or {}
        transform_op = getattr(node, "transform_op", None) or (data.get("transform_op") if isinstance(data, dict) else None)
        registry = get_executor().registry
        if not transform_op:
            return [Issue(node_id=node_id, severity="error", code="TRANSFORM_OP_MISSING",
                          message="Nodo trasformazione senza operazione selezionata.", suggestion=None)]
        if transform_op not in registry:
            return [Issue(node_id=node_id, severity="error", code="TRANSFORM_OP_UNKNOWN",
                          message=f"Operazione '{transform_op}' non riconosciuta dal motore di trasformazione.",
                          suggestion={"op": transform_op})]

        config = _node_config(node)
        # Residui collaudo live L4b: un campo obbligatorio vuoto si dice con
        # il suo nome («Compila «Colonna»…»), PRIMA dell'esecuzione a vuoto,
        # che fallirebbe per conseguenza col messaggio tecnico del motore
        # («Filter: colonna 'None' non trovata»). Regola unica per ogni campo
        # obbligatorio del registry (anche dentro le condizioni di Filtra).
        required = required_param_issues(node_id, transform_op, config, registry[transform_op])
        if any(issue.severity == "error" for issue in required):
            return required

        issues = self._dry_run_issues(node, dry_run_transform(node, schemas, transform_op), schemas + [upstream])
        if issues:
            return issues
        issues = list(required)
        # Le regole di schema leggono ciò che l'operazione riceve: il primo
        # ingresso per le operazioni 'single', tutti per le 'multi'.
        if registry[transform_op].get("inputs") != "multi" and schemas:
            upstream = schemas[0]
        upstream_cols = [c for c in upstream.columns if isinstance(c, dict) and c.get("name")]
        names = {c["name"] for c in upstream_cols}
        id_to_name = {str(c.get("id") or c.get("column_id")): c["name"]
                      for c in upstream_cols if c.get("id") or c.get("column_id")}
        ref_config = dict(config)
        if transform_op == "filter" and config.get("conditions") is not None:
            for key in ("column", "column_column_id", "operator", "value"):
                ref_config.pop(key, None)
        resolved = resolve_config_column_refs(ref_config, id_to_name)

        # Schema a monte: collisioni della rinomina (il runtime le accetta, il
        # salvataggio le rinominerebbe; P5 le rende un errore di Valida).
        if transform_op == "rename":
            params = get_executor().prepare_params("rename", resolved)
            duplicates = rename_collisions([c["name"] for c in upstream_cols], params)
            if duplicates:
                # Avviso, non errore (decisione utente 04/11/2026): l'esecuzione
                # scrive comunque un file valido (Comune, Comune_2) e la
                # previsione coincide; un progetto esistente non si blocca.
                issues.append(Issue(node_id=node_id, severity="warning",
                                    code="rename.duplicate", suggestion=None,
                                    message="Rinomina produce colonne duplicate: " + "; ".join(
                                        f"{name}: {', '.join(origins)}" for name, origins in duplicates.items())
                                    + ". Nel file la seconda uscira' con il suffisso _2 (e cosi' via)."))

        # Schema a monte: tabella tipi/aggregazioni generata dall'esecuzione
        # con righe (zero righe non portano l'errore di tipo).
        if transform_op == "aggregate":
            by_name = {c["name"]: c for c in upstream_cols}
            for aggregation in resolved.get("aggregations") or []:
                column = by_name.get(aggregation.get("column")) if isinstance(aggregation, dict) else None
                if column is None:
                    continue
                try:
                    aggregation_output_type(aggregation.get("function", "count"), column.get("type", "str"))
                except TypeError as exc:
                    issues.append(Issue(node_id=node_id, severity="warning" if column.get("optional") else "error",
                                        code="aggregation.unsupported", message=str(exc), suggestion=None))

        # Riferimenti assenti a monte: errore di Valida (come HEAD), perché il
        # runtime li ignora in silenzio; avviso con nomi incerti e per la
        # selezione, che per contratto salta le colonne assenti.
        selection = {"columns": resolved.get("columns")} if transform_op == "select_columns" else resolved
        references = referenced_column_names(selection)
        nested = resolved.get("conditions") if transform_op == "filter" else []
        if isinstance(nested, list):
            references.update(item["column"] for item in nested if isinstance(item, dict) and isinstance(item.get("column"), str))
        for key in ("left_columns", "right_columns"):
            selected = resolved.get(key)
            if isinstance(selected, list):
                references.update(name for name in selected if isinstance(name, str))
        is_selection = transform_op == "select_columns"
        missing_severity = "warning" if is_selection or _uncertain([upstream]) else "error"
        for missing in sorted(references - names):
            message = (f"Colonna '{missing}' assente a monte, ignorata" if is_selection
                       else f"Colonna '{missing}' referenziata dal transform ma assente a monte")
            issues.append(Issue(node_id=node_id, severity=missing_severity, code="column.missing",
                                message=message, suggestion={"column": missing}))

        col_type = {c["name"]: (c.get("type") or "") for c in upstream_cols}
        for param in registry[transform_op].get("parameters") or []:
            require_known_param_type(param.get("type"))
            pid = param.get("id")
            if not pid:
                continue
            val = _param_value_for_required_check(transform_op, pid, config)
            if param.get("required") and _is_empty_param_value(val):
                continue  # gia' detto da required_param_issues
            # Schema a monte: parametro geometria su colonna nota non geometrica.
            if param.get("type") == "geometry_column" and not _is_empty_param_value(val):
                resolved_name = id_to_name.get(str(val), val)
                if resolved_name in col_type and not str(col_type[resolved_name]).startswith("geometry"):
                    issues.append(Issue(
                        node_id=node_id, severity="warning" if upstream.types_status == "indeterminate" else "error",
                        code="TRANSFORM_GEOMETRY_TYPE_INVALID",
                        message=f"La colonna '{resolved_name}' non è di tipo geometry.",
                        suggestion={"param": pid, "column": resolved_name},
                    ))
        return issues

    # Output --------------------------------------------------------------------

    def _validate_result(self, node: Any, upstream: InferredSchema) -> List[Issue]:
        node_id = getattr(node, "id", "")
        try:
            result_group_plan(node, [c["name"] for c in upstream.columns])
        except (ValueError, TypeError, KeyError) as exc:
            return [Issue(node_id=node_id, severity="warning" if upstream.names_status == "indeterminate" else "error",
                          code="preparation.invalid", message=str(exc), suggestion=None)]
        columns = node_field(node, "output_columns")
        if columns is None:
            return []
        upstream_cols = [c for c in upstream.columns if isinstance(c, dict) and c.get("name")]
        names = {c["name"] for c in upstream_cols}
        id_to_name = {str(c.get("id") or c.get("column_id")): c["name"]
                      for c in upstream_cols if c.get("id") or c.get("column_id")}
        resolved = resolve_config_column_refs({"columns": columns}, id_to_name)
        try:
            select_column_plan(list(names), resolved["columns"], skip_missing=True)
        except ValueError as exc:
            return [Issue(node_id=node_id, severity="error", code="columns.invalid", message=str(exc), suggestion=None)]
        return [Issue(node_id=node_id, severity="warning", code="column.missing",
                      message=f"Colonna '{missing}' assente a monte, ignorata", suggestion={"column": missing})
                for missing in sorted(referenced_column_names(resolved) - names)]


def register_manipola_validator() -> None:
    """Registra il validatore transform al composition-root (#D)."""
    validator = ManipolaNodeValidator()
    for node_type in _MANIPOLA_NODE_TYPES:
        register_node_validator(node_type, validator)


__all__ = ["ManipolaNodeValidator", "register_manipola_validator"]
