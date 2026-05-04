"""Contract test BE-PHASE3-SCHEMA-CONTRACTS-001-REGISTRY-METADATA —
`output_columns` schema contract metadata in `transforms_registry.json`.

Verifica che il registry esponga il contratto schema-inference per i
transform i cui handler accettano `output_columns` (introdotti in
BE-PHASE2-SCHEMA-INFER-009-ZERO-RESIDUALS):

- `flatten_json`
- `transpose`

Il parametro `output_columns` e' un **schema contract** (non un
runtime parameter): viene consumato da
`SchemaInferenceService._schema_flatten_json` /
`_schema_transpose` per la dichiarazione canvas-time delle colonne
attese. Quando vuoto, lo schema cade in passthrough deterministic
(no MIGRATE_NEXT). Il runtime transform NON legge questo campo.

Source-bound: parsa `transforms_registry.json` come fonte di verita'.
Failure qui = registry/handler drift sul contratto.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Set

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent  # .../backend
_REGISTRY_JSON = (
    _BACKEND_ROOT / "app" / "core" / "manipola" / "transforms_registry.json"
)


# Transform IDs che DEVONO esporre il parametro `output_columns`
# come schema contract. Drift target: ogni nuovo transform che
# adotta il pattern config-explicit deve essere aggiunto qui +
# avere un handler dedicato in `SchemaInferenceService`.
TRANSFORMS_WITH_OUTPUT_COLUMNS_CONTRACT: Set[str] = {
    "flatten_json",
    "transpose",
}


def _load_registry() -> dict:
    return json.loads(_REGISTRY_JSON.read_text(encoding="utf-8"))


def _get_param(registry: dict, transform_id: str, param_id: str) -> dict | None:
    transform = registry.get("transforms", {}).get(transform_id)
    if not transform:
        return None
    for p in transform.get("parameters", []):
        if isinstance(p, dict) and p.get("id") == param_id:
            return p
    return None


# ---------------------------------------------------------------------------
# 1. `output_columns` parameter must exist on each contracted transform
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("transform_id", sorted(TRANSFORMS_WITH_OUTPUT_COLUMNS_CONTRACT))
def test_transform_exposes_output_columns_parameter(transform_id):
    """Ogni transform contracted deve esporre il parametro
    `output_columns` nel registry."""
    registry = _load_registry()
    param = _get_param(registry, transform_id, "output_columns")
    assert param is not None, (
        f"Transform `{transform_id}` non espone `output_columns` in "
        f"`transforms_registry.json`. Aggiungi la metadata o rimuovi "
        f"l'ID da TRANSFORMS_WITH_OUTPUT_COLUMNS_CONTRACT."
    )


@pytest.mark.parametrize("transform_id", sorted(TRANSFORMS_WITH_OUTPUT_COLUMNS_CONTRACT))
def test_output_columns_uses_string_list_type(transform_id):
    """Il tipo registry deve essere `string_list` — il FE renderer
    generico (`StringListInput`) e' gia' in grado di gestire questa
    shape senza componenti dedicati. La forma typed
    `List[{name, type}]` resta supportata dal BE handler ma e' un
    power-user contract (non esposto in UI in questa fase)."""
    registry = _load_registry()
    param = _get_param(registry, transform_id, "output_columns")
    assert param is not None
    assert param.get("type") == "string_list", (
        f"`{transform_id}.output_columns` ha type "
        f"`{param.get('type')}` invece di `string_list`. "
        f"Cambio richiede review FE renderer."
    )


@pytest.mark.parametrize("transform_id", sorted(TRANSFORMS_WITH_OUTPUT_COLUMNS_CONTRACT))
def test_output_columns_marked_as_schema_contract(transform_id):
    """Marker `schema_contract: true` distingue questo parametro
    da quelli runtime. Il FE puo' usarlo per eventuale UI dedicata
    (badge, tooltip, gruppo separato); oggi e' metadata
    forward-compatible (FE renderer ignora chiavi sconosciute)."""
    registry = _load_registry()
    param = _get_param(registry, transform_id, "output_columns")
    assert param is not None
    assert param.get("schema_contract") is True, (
        f"`{transform_id}.output_columns` non e' marcato "
        f"`schema_contract: true`. Necessario per distinguere il "
        f"contratto schema dai parametri runtime."
    )


@pytest.mark.parametrize("transform_id", sorted(TRANSFORMS_WITH_OUTPUT_COLUMNS_CONTRACT))
def test_output_columns_default_is_empty_list(transform_id):
    """Default `[]` lockato: il fallback handler e' passthrough
    quando `output_columns` non e' valorizzata. Mantenere il default
    a lista vuota (non `None`, non assente) garantisce che il FE
    e il BE abbiano la stessa baseline."""
    registry = _load_registry()
    param = _get_param(registry, transform_id, "output_columns")
    assert param is not None
    assert param.get("default") == [], (
        f"`{transform_id}.output_columns` ha default "
        f"`{param.get('default')}` invece di `[]`."
    )


@pytest.mark.parametrize("transform_id", sorted(TRANSFORMS_WITH_OUTPUT_COLUMNS_CONTRACT))
def test_output_columns_has_label_and_description(transform_id):
    """UX metadata minima: `label` italiano e `description`
    esplicativa del fallback passthrough."""
    registry = _load_registry()
    param = _get_param(registry, transform_id, "output_columns")
    assert param is not None
    assert param.get("label"), f"`{transform_id}.output_columns` senza `label`."
    description = param.get("description") or ""
    # La description deve menzionare il fallback passthrough per non
    # sorprendere l'utente sul comportamento canvas-time.
    assert "passthrough" in description.lower(), (
        f"`{transform_id}.output_columns.description` deve documentare "
        f"il fallback passthrough quando vuoto."
    )


# ---------------------------------------------------------------------------
# 2. Registry preserves all transforms (no accidental drop / rename)
# ---------------------------------------------------------------------------


def test_registry_size_unchanged_after_metadata_addition():
    """Lock di non-regressione: aggiungere metadata schema_contract
    non deve alterare il numero totale di transform IDs."""
    registry = _load_registry()
    assert len(registry.get("transforms", {})) == 70


def test_no_other_transform_uses_output_columns_id():
    """Nessun altro transform deve esporre un parametro `output_columns`
    finche' non e' aggiunto esplicitamente al set
    TRANSFORMS_WITH_OUTPUT_COLUMNS_CONTRACT (drift detection)."""
    registry = _load_registry()
    transforms = registry.get("transforms", {})
    found = set()
    for tid, transform in transforms.items():
        for p in transform.get("parameters", []):
            if isinstance(p, dict) and p.get("id") == "output_columns":
                found.add(tid)
                break
    assert found == TRANSFORMS_WITH_OUTPUT_COLUMNS_CONTRACT, (
        f"Drift `output_columns` parameter coverage. Atteso "
        f"{TRANSFORMS_WITH_OUTPUT_COLUMNS_CONTRACT}, trovato {found}. "
        f"Aggiungi al set sopra o rimuovi dal registry."
    )


# ---------------------------------------------------------------------------
# 3. BE handler/registry coherence — schema_contract handlers exist
# ---------------------------------------------------------------------------


def test_schema_handler_exists_for_each_contracted_transform():
    """Per ogni transform contracted, il `SchemaInferenceService`
    deve esporre un handler dedicato (`_schema_<op>`). Lock
    cross-component contro drift handler vs registry."""
    from app.services.schema_inference import SchemaInferenceService

    service = SchemaInferenceService(project_service=None)
    handlers = service._build_transform_handlers(edges_by_target={})
    for transform_id in TRANSFORMS_WITH_OUTPUT_COLUMNS_CONTRACT:
        assert transform_id in handlers, (
            f"Registry expone `{transform_id}.output_columns` ma il "
            f"`SchemaInferenceService` non ha handler dedicato. "
            f"Drift cross-component."
        )
