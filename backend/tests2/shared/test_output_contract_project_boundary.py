import json
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from app.core.output_contract import (
    build_output_contract,
    ensure_dataframe_matches_contract,
    node_columns_from_contract,
)


pytestmark = [
    pytest.mark.unit,
    pytest.mark.contract,
    pytest.mark.regression,
]


def test_output_contract_preserves_column_id_when_alias_changes():
    previous = build_output_contract(
        project_id="extract-1",
        result_columns=[
            {"id": "col-stable", "source_name": "id", "name": "id", "type": "int"}
        ],
    ).model_dump()

    current = build_output_contract(
        project_id="extract-1",
        result_columns=[
            {
                "source_name": "id",
                "name": "myid",
                "type": "int64",
            }
        ],
        previous_contract=previous,
    )

    assert current.columns[0].id == "col-stable"
    assert current.columns[0].source_name == "id"
    assert current.columns[0].physical_name == "myid"
    assert current.columns[0].type.logical == "integer"


def test_output_contract_disambiguates_duplicate_join_columns():
    contract = build_output_contract(
        project_id="extract-join",
        result_columns=[
            {"id": "col-id", "source_name": "id", "name": "id", "type": "int"},
            {"id": "col-name", "source_name": "name", "name": "name", "type": "str"},
            {"id": "col-id", "source_name": "id", "name": "id", "type": "int"},
            {"id": "col-name", "source_name": "name", "name": "name", "type": "str"},
        ],
    )

    assert [c.physical_name for c in contract.columns] == [
        "id",
        "name",
        "id_2",
        "name_2",
    ]
    assert len({c.id for c in contract.columns}) == 4
    assert [c.ordinal for c in contract.columns] == [0, 1, 2, 3]


def test_node_columns_from_contract_does_not_match_visibility_by_source_name():
    contract = {
        "columns": [
            {
                "id": "col-stable",
                "source_name": "id",
                "physical_name": "myid",
                "display_name": "myid",
                "type": {"logical": "integer"},
                "status": "active",
            }
        ]
    }

    columns = node_columns_from_contract(
        contract,
        previous_node_columns=[
            {"id": None, "column_id": None, "name": "id", "visible": False}
        ],
    )

    assert columns == [
        {
            "id": "col-stable",
            "name": "myid",
            "source_name": "id",
            "display_name": "myid",
            "type": "integer",
                "visible": True,
        }
    ]


def test_ensure_dataframe_matches_contract_fails_on_stale_artifact():
    with pytest.raises(ValueError, match="output_contract"):
        ensure_dataframe_matches_contract(
            dataframe_columns=["id"],
            node_columns=[
                {
                    "id": "col-stable",
                    "name": "myid",
                    "source_name": "id",
                    "visible": True,
                }
            ],
            source_project_id="extract-1",
        )


def test_plugin_executor_does_not_rewrite_formula_by_source_name():
    from app.core.manipola.plugin_executor import PluginExecutor

    executor = PluginExecutor.__new__(PluginExecutor)
    node = SimpleNamespace(
        data={
            "inputColumns": [
                {"id": "col-stable", "name": "myid", "source_name": "id"}
            ]
        }
    )

    resolved = executor._resolve_column_references(
        {"formula": "id * 2"},
        node,
        {"parameters": [{"id": "formula", "type": "expression"}]},
    )

    assert resolved["formula"] == "id * 2"


def test_plugin_executor_keeps_column_names_when_column_ids_companion_is_empty():
    from app.core.manipola.plugin_executor import PluginExecutor

    executor = PluginExecutor.__new__(PluginExecutor)
    node = SimpleNamespace(
        data={
            "inputColumns": [
                {"id": "col-stable", "name": "z", "source_name": "id"}
            ]
        }
    )

    resolved = executor._resolve_column_references(
        {"columns": ["z"], "columns_column_ids": []},
        node,
        {"parameters": [{"id": "columns", "type": "columns"}]},
    )

    assert resolved["columns"] == ["z"]


def test_schema_inference_keeps_column_names_when_column_ids_companion_is_empty():
    pytest.importorskip("psycopg2")

    from app.core.projects.schema_inference import ColumnSchema, SchemaInferenceService

    service = SchemaInferenceService(project_service=SimpleNamespace())

    resolved = service._resolve_schema_config_column_refs(
        {"columns": ["z"], "columns_column_ids": []},
        [[ColumnSchema(id="col-stable", name="z", source_name="id", type="integer")]],
    )

    assert resolved["columns"] == ["z"]


def test_column_reference_sync_key_sets_cover_current_transform_registry():
    from app.core.output_contract.canvas_sync import (
        LIST_COLUMN_CONFIG_KEYS,
        SCALAR_COLUMN_CONFIG_KEYS,
    )

    registry_path = (
        Path(__file__).resolve().parents[2]
        / "app"
        / "core"
        / "manipola"
        / "transforms_registry.json"
    )
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    scalar_ids: set[str] = set()
    list_ids: set[str] = set()
    for transform in registry.get("transforms", {}).values():
        for param in transform.get("parameters", []) or []:
            param_id = param.get("id")
            param_type = param.get("type")
            if not param_id:
                continue
            if param_type in {"column", "geometry_column"}:
                scalar_ids.add(param_id)
            elif param_type in {"columns", "column_order"}:
                list_ids.add(param_id)

    assert scalar_ids <= SCALAR_COLUMN_CONFIG_KEYS
    assert list_ids <= LIST_COLUMN_CONFIG_KEYS


def test_plugin_executor_does_not_rewrite_column_param_shapes_by_source_name():
    from app.core.manipola.plugin_executor import PluginExecutor
    from app.core.output_contract.canvas_sync import (
        LIST_COLUMN_CONFIG_KEYS,
        SCALAR_COLUMN_CONFIG_KEYS,
    )

    executor = PluginExecutor.__new__(PluginExecutor)
    node = SimpleNamespace(
        data={
            "inputColumns": [
                {"id": "col-stable", "name": "myid", "source_name": "id"}
            ]
        }
    )
    config = {key: "id" for key in SCALAR_COLUMN_CONFIG_KEYS}
    config.update({key: ["id"] for key in LIST_COLUMN_CONFIG_KEYS})
    config.update({
        "aggregations": [{"column": "id"}],
        "aggs": [{"column": "id"}],
        "maskings": [{"column": "id"}],
        "renames": [{"old_name": "id", "new_name": "renamed"}],
        "formula": "id + 1",
    })
    transform_config = {
        "parameters": [
            *({"id": key, "type": "column"} for key in SCALAR_COLUMN_CONFIG_KEYS),
            *({"id": key, "type": "columns"} for key in LIST_COLUMN_CONFIG_KEYS),
            {"id": "formula", "type": "expression"},
        ]
    }

    resolved = executor._resolve_column_references(config, node, transform_config)

    for key in SCALAR_COLUMN_CONFIG_KEYS - LIST_COLUMN_CONFIG_KEYS:
        assert resolved[key] == "id"
    for key in LIST_COLUMN_CONFIG_KEYS:
        assert resolved[key] == ["id"]
    assert resolved["aggregations"] == [{"column": "id"}]
    assert resolved["aggs"] == [{"column": "id"}]
    assert resolved["maskings"] == [{"column": "id"}]
    assert resolved["renames"] == [{"old_name": "id", "new_name": "renamed"}]
    assert resolved["formula"] == "id + 1"


def test_schema_inference_does_not_rewrite_column_param_shapes_by_source_name():
    pytest.importorskip("psycopg2")

    from app.core.projects.schema_inference import (
        LIST_COLUMN_CONFIG_KEYS,
        SCALAR_COLUMN_CONFIG_KEYS,
        ColumnSchema,
        SchemaInferenceService,
    )

    service = SchemaInferenceService(project_service=SimpleNamespace())
    config = {key: "id" for key in SCALAR_COLUMN_CONFIG_KEYS}
    config.update({key: ["id"] for key in LIST_COLUMN_CONFIG_KEYS})
    config.update({
        "aggregations": [{"column": "id"}],
        "aggs": [{"column": "id"}],
        "maskings": [{"column": "id"}],
        "renames": [{"old_name": "id", "new_name": "renamed"}],
    })

    resolved = service._resolve_schema_config_column_refs(
        config,
        [[ColumnSchema(id="col-stable", name="myid", source_name="id", type="integer")]],
    )

    for key in SCALAR_COLUMN_CONFIG_KEYS - LIST_COLUMN_CONFIG_KEYS:
        assert resolved[key] == "id"
    for key in LIST_COLUMN_CONFIG_KEYS:
        assert resolved[key] == ["id"]
    assert resolved["aggregations"] == [{"column": "id"}]
    assert resolved["aggs"] == [{"column": "id"}]
    assert resolved["maskings"] == [{"column": "id"}]
    assert resolved["renames"] == [{"old_name": "id", "new_name": "renamed"}]


def test_arricchisci_project_source_consumes_output_contract_not_canvas_columns():
    pytest.importorskip("psycopg2")

    from app.core.arricchisci.api_connection_source_loading import (
        ApiConnectionSourceLoadingMixin,
    )
    from app.core.arricchisci.execution_context import ArricchisciExecutionContext

    svc = ApiConnectionSourceLoadingMixin()
    svc._upstream_blob_is_stale = lambda _project_id: False
    source_df = pd.DataFrame({"myid": [1, 2], "name": ["a", "b"]})
    contract = {
        "columns": [
            {
                "id": "col-stable",
                "source_name": "id",
                "physical_name": "myid",
                "display_name": "myid",
                "type": {"logical": "integer"},
                "status": "active",
            }
        ]
    }

    with patch(
        "app.core.arricchisci.api_connection_source_loading.SourceLoader",
    ) as loader_cls, patch(
        "app.core.arricchisci.api_connection_source_loading.ProjectService",
    ) as project_service_cls:
        loader_cls.return_value.load.return_value = source_df
        project_service_cls.return_value.get_project_output_contract.return_value = contract

        out = svc._load_source_project(
            {
                "source_project_id": "extract-1",
                "columns": [
                    {"id": None, "column_id": None, "name": "id", "visible": True}
                ],
            },
            ctx=ArricchisciExecutionContext(
                project_id="enrich-1",
                group_id="group-1",
                preview_limit=None,
            ),
        )

    assert list(out.columns) == ["myid"]
    assert out["myid"].tolist() == [1, 2]


def test_carica_type_enricher_reads_output_contract():
    from app.shared.execution.output_contract_types_enricher import (
        enrich_canvas_types_from_output_contract,
    )

    node_data = {"canvas_types": {"id": "text"}}
    contract = {
        "columns": [
            {
                "id": "col-id",
                "source_name": "id",
                "physical_name": "id",
                "display_name": "id",
                "type": {"logical": "integer"},
                "ordinal": 0,
                "status": "active",
            },
            {
                "id": "col-score",
                "source_name": "score",
                "physical_name": "score",
                "display_name": "score",
                "type": {"logical": "number"},
                "ordinal": 1,
                "status": "active",
            },
        ],
    }

    out = enrich_canvas_types_from_output_contract(
        node_data,
        "extract-1",
        lambda _project_id: contract,
    )

    assert out["canvas_types"] == {"id": "text", "score": "number"}


def test_canvas_sync_does_not_rewrite_formula_from_source_name():
    from app.core.output_contract import sync_canvas_with_output_contracts
    from app.core.projects.canvas_models import CanvasState

    class _ProjectService:
        def get_project_output_contract(self, project_id):
            assert project_id == "extract-1"
            return {
                "columns": [
                    {
                        "id": "col-stable",
                        "source_name": "id",
                        "physical_name": "myid",
                        "display_name": "myid",
                        "type": {"logical": "integer"},
                        "ordinal": 0,
                        "status": "active",
                    }
                ]
            }

    canvas = CanvasState(
        nodes=[
            {
                "id": "source-1",
                "type": "project_source",
                "position": {"x": 0, "y": 0},
                "source_project_id": "extract-1",
                "columns": [{"id": None, "name": "myid", "type": "integer"}],
            },
            {
                "id": "formula-1",
                "type": "transform",
                "position": {"x": 1, "y": 0},
                "transform_op": "formula",
                "config": {"formula": "id*2"},
            },
        ],
        edges=[
            {
                "id": "e1",
                "source": "source-1",
                "target": "formula-1",
            }
        ],
    )

    changed = sync_canvas_with_output_contracts(
        canvas=canvas,
        project_id="manipola-1",
        project_service=_ProjectService(),
    )

    assert changed is True
    assert canvas.nodes[0].columns[0].id == "col-stable"
    assert canvas.nodes[0].columns[0].source_name == "id"
    assert canvas.nodes[1].config["formula"] == "id*2"
    assert "formula_column_refs" not in canvas.nodes[1].config


def test_canvas_sync_rewrites_drop_columns_after_upstream_alias_removed():
    from app.core.output_contract import sync_canvas_with_output_contracts
    from app.core.projects.canvas_models import CanvasState

    class _ProjectService:
        def get_project_output_contract(self, project_id):
            assert project_id == "extract-1"
            return {
                "columns": [
                    {
                        "id": "col-stable",
                        "source_name": "id",
                        "physical_name": "id",
                        "display_name": "id",
                        "type": {"logical": "integer"},
                        "ordinal": 0,
                        "status": "active",
                    }
                ]
            }

    canvas = CanvasState(
        nodes=[
            {
                "id": "source-1",
                "type": "project_source",
                "position": {"x": 0, "y": 0},
                "source_project_id": "extract-1",
                "columns": [
                    {
                        "id": "col-stable",
                        "name": "z",
                        "source_name": "id",
                        "display_name": "z",
                        "type": "integer",
                    }
                ],
            },
            {
                "id": "drop-1",
                "type": "transform",
                "position": {"x": 1, "y": 0},
                "transform_op": "drop_columns",
                "config": {"columns": ["z"], "columns_column_ids": []},
            },
        ],
        edges=[
            {
                "id": "e1",
                "source": "source-1",
                "target": "drop-1",
            }
        ],
    )

    changed = sync_canvas_with_output_contracts(
        canvas=canvas,
        project_id="manipola-1",
        project_service=_ProjectService(),
    )

    assert changed is True
    assert canvas.nodes[0].columns[0].name == "id"
    assert canvas.nodes[1].config["columns"] == ["id"]
    assert canvas.nodes[1].config["columns_column_ids"] == ["col-stable"]


def test_canvas_sync_propagates_alias_migration_through_transform_chain():
    from app.core.output_contract import sync_canvas_with_output_contracts
    from app.core.projects.canvas_models import CanvasState

    class _ProjectService:
        def get_project_output_contract(self, project_id):
            assert project_id == "extract-1"
            return {
                "columns": [
                    {
                        "id": "col-stable",
                        "source_name": "id",
                        "physical_name": "id",
                        "display_name": "id",
                        "type": {"logical": "integer"},
                        "ordinal": 0,
                        "status": "active",
                    }
                ]
            }

    canvas = CanvasState(
        nodes=[
            {
                "id": "source-1",
                "type": "project_source",
                "position": {"x": 0, "y": 0},
                "source_project_id": "extract-1",
                "columns": [
                    {
                        "id": "col-stable",
                        "name": "z",
                        "source_name": "id",
                        "display_name": "z",
                        "type": "integer",
                    }
                ],
            },
            {
                "id": "formula-1",
                "type": "transform",
                "position": {"x": 1, "y": 0},
                "transform_op": "formula",
                "columns": [
                    {
                        "id": "col-stable",
                        "name": "id",
                        "source_name": "id",
                        "display_name": "id",
                        "type": "integer",
                    },
                    {"id": "col-calc", "name": "calculated", "type": "integer"},
                ],
                "config": {
                    "formula": "z*2",
                    "new_column": "calculated",
                    "formula_column_refs": [{"id": "col-stable", "name": "z"}],
                },
            },
            {
                "id": "drop-1",
                "type": "transform",
                "position": {"x": 2, "y": 0},
                "transform_op": "drop_columns",
                "config": {"columns": ["z"], "columns_column_ids": []},
            },
        ],
        edges=[
            {"id": "e1", "source": "source-1", "target": "formula-1"},
            {"id": "e2", "source": "formula-1", "target": "drop-1"},
        ],
    )

    changed = sync_canvas_with_output_contracts(
        canvas=canvas,
        project_id="manipola-1",
        project_service=_ProjectService(),
    )

    assert changed is True
    assert canvas.nodes[1].config["formula"] == "id*2"
    assert canvas.nodes[2].config["columns"] == ["id"]
    assert canvas.nodes[2].config["columns_column_ids"] == ["col-stable"]


def test_canvas_sync_rewrites_all_non_expression_column_param_shapes():
    from app.core.output_contract import sync_canvas_with_output_contracts
    from app.core.output_contract.canvas_sync import (
        LIST_COLUMN_CONFIG_KEYS,
        SCALAR_COLUMN_CONFIG_KEYS,
    )
    from app.core.projects.canvas_models import CanvasState

    class _ProjectService:
        def get_project_output_contract(self, project_id):
            assert project_id == "extract-1"
            return {
                "columns": [
                    {
                        "id": "col-stable",
                        "source_name": "id",
                        "physical_name": "id",
                        "display_name": "id",
                        "type": {"logical": "integer"},
                        "ordinal": 0,
                        "status": "active",
                    }
                ]
            }

    config = {key: "z" for key in SCALAR_COLUMN_CONFIG_KEYS}
    config.update({key: ["z"] for key in LIST_COLUMN_CONFIG_KEYS})
    config.update({f"{key}_column_ids": [] for key in LIST_COLUMN_CONFIG_KEYS})
    config.update({
        "aggregations": [{"column": "z", "column_id": ""}],
        "aggs": [{"column": "z", "column_id": ""}],
        "maskings": [{"column": "z", "column_id": ""}],
        "renames": [{"old_name": "z", "old_name_id": "", "new_name": "renamed"}],
    })
    canvas = CanvasState(
        nodes=[
            {
                "id": "source-1",
                "type": "project_source",
                "position": {"x": 0, "y": 0},
                "source_project_id": "extract-1",
                "columns": [
                    {
                        "id": "col-stable",
                        "name": "z",
                        "source_name": "id",
                        "display_name": "z",
                        "type": "integer",
                    }
                ],
            },
            {
                "id": "transform-1",
                "type": "transform",
                "position": {"x": 1, "y": 0},
                "transform_op": "filter",
                "config": config,
            },
        ],
        edges=[{"id": "e1", "source": "source-1", "target": "transform-1"}],
    )

    changed = sync_canvas_with_output_contracts(
        canvas=canvas,
        project_id="manipola-1",
        project_service=_ProjectService(),
    )

    assert changed is True
    synced = canvas.nodes[1].config
    for key in SCALAR_COLUMN_CONFIG_KEYS - LIST_COLUMN_CONFIG_KEYS:
        assert synced[key] == "id"
    for key in LIST_COLUMN_CONFIG_KEYS:
        assert synced[key] == ["id"]
        assert synced[f"{key}_column_ids"] == ["col-stable"]
    assert synced["aggregations"] == [{"column": "id", "column_id": "col-stable"}]
    assert synced["aggs"] == [{"column": "id", "column_id": "col-stable"}]
    assert synced["maskings"] == [{"column": "id", "column_id": "col-stable"}]
    assert synced["renames"] == [
        {"old_name": "id", "old_name_id": "col-stable", "new_name": "renamed"}
    ]


def test_canvas_sync_rewrites_drop_columns_from_previous_project_contract():
    from app.core.output_contract import sync_canvas_with_output_contracts
    from app.core.projects.canvas_models import CanvasState

    class _ProjectService:
        def get_project_output_contract(self, project_id):
            if project_id == "extract-1":
                return {
                    "columns": [
                        {
                            "id": "col-stable",
                            "source_name": "id",
                            "physical_name": "id",
                            "display_name": "id",
                            "type": {"logical": "integer"},
                            "ordinal": 0,
                            "status": "active",
                        }
                    ]
                }
            assert project_id == "manipola-1"
            return {
                "columns": [
                    {
                        "id": "col-stable",
                        "source_name": "id",
                        "physical_name": "z",
                        "display_name": "z",
                        "type": {"logical": "integer"},
                        "ordinal": 0,
                        "status": "active",
                    }
                ]
            }

    canvas = CanvasState(
        nodes=[
            {
                "id": "source-1",
                "type": "project_source",
                "position": {"x": 0, "y": 0},
                "source_project_id": "extract-1",
                "columns": [
                    {
                        "id": "col-stable",
                        "name": "id",
                        "source_name": "id",
                        "display_name": "id",
                        "type": "integer",
                    }
                ],
            },
            {
                "id": "formula-1",
                "type": "transform",
                "position": {"x": 1, "y": 0},
                "transform_op": "formula",
                "columns": [
                    {
                        "id": "col-stable",
                        "name": "id",
                        "source_name": "id",
                        "display_name": "id",
                        "type": "integer",
                    }
                ],
                "config": {
                    "formula": "id*2",
                    "new_column": "calculated",
                    "formula_column_refs": [{"id": "col-stable", "name": "id"}],
                },
            },
            {
                "id": "drop-1",
                "type": "transform",
                "position": {"x": 2, "y": 0},
                "transform_op": "drop_columns",
                "config": {"columns": ["z"], "columns_column_ids": []},
            },
        ],
        edges=[
            {"id": "e1", "source": "source-1", "target": "formula-1"},
            {"id": "e2", "source": "formula-1", "target": "drop-1"},
        ],
    )

    changed = sync_canvas_with_output_contracts(
        canvas=canvas,
        project_id="manipola-1",
        project_service=_ProjectService(),
    )

    assert changed is False
    assert canvas.nodes[2].config["columns"] == ["z"]
    assert canvas.nodes[2].config["columns_column_ids"] == []


def test_canvas_sync_migrates_formula_ref_when_upstream_calculated_column_was_renamed():
    from app.core.output_contract import sync_canvas_with_output_contracts
    from app.core.projects.canvas_models import CanvasState

    class _ProjectService:
        def get_project_output_contract(self, project_id):
            assert project_id == "manipola-upstream"
            return {
                "columns": [
                    {
                        "id": "col-current-calculated",
                        "source_name": "calculated:formula-1:formula",
                        "physical_name": "calculated3",
                        "display_name": "calculated3",
                        "type": {"logical": "integer"},
                        "ordinal": 0,
                        "status": "active",
                    }
                ]
            }

    canvas = CanvasState(
        nodes=[
            {
                "id": "source-1",
                "type": "project_source",
                "position": {"x": 0, "y": 0},
                "source_project_id": "manipola-upstream",
                "columns": [
                    {
                        "id": "col-old-calculated",
                        "name": "calculated",
                        "source_name": "calculated",
                        "display_name": "calculated",
                        "type": "integer",
                    }
                ],
            },
            {
                "id": "formula-2",
                "type": "transform",
                "position": {"x": 1, "y": 0},
                "transform_op": "formula",
                "config": {
                    "formula": "calculated*4",
                    "formula_column_refs": [
                        {"id": "col-old-calculated", "name": "calculated"}
                    ],
                },
            },
        ],
        edges=[
            {
                "id": "e1",
                "source": "source-1",
                "target": "formula-2",
            }
        ],
    )

    changed = sync_canvas_with_output_contracts(
        canvas=canvas,
        project_id="manipola-downstream",
        project_service=_ProjectService(),
    )

    assert changed is True
    assert canvas.nodes[0].columns[0].id == "col-current-calculated"
    assert canvas.nodes[0].columns[0].name == "calculated3"
    assert canvas.nodes[1].config["formula"] == "calculated3*4"
    assert canvas.nodes[1].config["formula_column_refs"] == [
        {"id": "col-current-calculated", "name": "calculated3"}
    ]


def test_legacy_project_schema_runtime_residues_are_removed():
    backend_root = Path(__file__).resolve().parents[2]
    repo_root = backend_root.parent
    forbidden = (
        "columns_" "metadata",
        "Columns" "Metadata" "SaveError",
        "save_columns_" "metadata",
        "get_project_columns_" "metadata",
        "update_project_columns_" "metadata",
        "canvas_types_" "enricher",
    )
    roots = (
        backend_root / "app",
        backend_root / "workers",
        repo_root / "frontend" / "src",
    )

    hits: list[str] = []
    for root in roots:
        for path in root.rglob("*"):
            if path.suffix not in {".py", ".js", ".jsx", ".ts", ".tsx"}:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for token in forbidden:
                if token in text:
                    hits.append(f"{path.relative_to(repo_root)}: {token}")

    assert hits == []
