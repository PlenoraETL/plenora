"""Sonda riproducibile HEAD/working tree: solo runtime, API simulate, nessuna inferenza."""
import logging
import json
import pickle
import sys
from pathlib import Path


def probe(cases, previous=None):
    logging.disable(logging.CRITICAL)
    import pandas as pd
    from app.core.projects.canvas_models import CanvasState
    from app.core.estrai.query_generator import QueryGenerator
    from app.core.arricchisci.enrichment_service import build_enrichment_executor
    from app.core.arricchisci.api_connector import ApiConnectionService
    from app.core.dataframes.output_columns import apply_output_columns

    sql = {}
    frames = {}
    for name, raw in cases["sql"].items():
        canvas = CanvasState.model_validate(raw)
        for provider in ["sqlite", "postgres", "oracle", "mysql", "mssql"]:
            sql[f"{name}:{provider}"] = QueryGenerator(provider).generate_sql(canvas.nodes, canvas.edges, "r")["sql"]
    for index, (node, mapping) in enumerate(cases["mappings"]):
        for empty in [False, True]:
            executor = build_enrichment_executor(node, {"output_mapping": mapping, "options": {"rate_limit": 100000}})
            executor._call_api_with_retry = lambda *args: (executor._extract_output({"x": 5, "z": "testo"}), None)
            frame = pd.DataFrame({"id": pd.Series([] if empty else [1, 2], dtype="int64")})
            output = executor.execute(frame)[0]
            frames[f"enricher:{index}:{empty}"] = output
            frames[f"output:{index}:{empty}"] = apply_output_columns(output, [{"name": "id", "label": "identificativo"}])
    for index, payload in enumerate([[], [{"x": 1}, {"x": 2}], {"data": [{"x": 3}]}, {"x": 4}]):
        for mapped in [False, True]:
            svc = ApiConnectionService()
            mapping = [{"path": "x", "column": "risposta"}] if mapped else []
            svc._call_generator_endpoint = lambda **kwargs: (svc._extract_rows_from_response(payload, mapping), payload, None)
            frames[f"generator:{index}:{mapped}"] = svc._execute_api_generator({
                "endpoint": {"url": "https://api.example.com"}, "output_mapping": mapping,
            })
    contracts = {}
    from types import SimpleNamespace
    from app.core.estrai.executor import EstraiExecutor
    import app
    sources_path = Path(app.__file__).parent / "core/projects/schema_execution_sources.py"
    sources = None
    if sources_path.exists():
        from app.core.projects import schema_execution_sources as sources
    for name, scenario in cases.get("contracts", {}).items():
        for provider in ["sqlite", "postgres", "oracle", "mysql", "mssql", None]:
            if sources is not None:
                sources.load_sql_provider = lambda _, provider=provider: provider
            for fmt in ["csv", "json", "xlsx", "geojson", "gpkg", "shp", "gdb"]:
                key = f"{name}:{provider}:{fmt}"
                canvas = CanvasState.model_validate(scenario["canvas"])
                canvas.nodes[-1].format = fmt
                old = (previous or {}).get("contracts", {}).get(key)
                svc = SimpleNamespace(get_project_canvas=lambda _: canvas,
                    get_project_metadata=lambda _: SimpleNamespace(project_type="estrai", default_connection_id="db" if provider else None),
                    get_project_output_contract=lambda _: old, update_project_output_contract=lambda *args: None)
                payload = dict(scenario["payload"])
                if fmt in {"csv", "xlsx"}:
                    payload.pop("column_types", None)
                    payload.pop("column_dtypes", None)
                EstraiExecutor(svc, None, None, None)._salva_tipi_colonne_da_canvas("p", canvas, payload)
                contracts[key] = payload["output_contract"]
    return {"sql": sql, "frames": frames, "contracts": contracts}


def compare(before, after):
    from pandas.testing import assert_frame_equal
    from app.core.output_contract.service import node_columns_from_contract
    from app.core.output_contract.canvas_sync import _build_column_migrations, _REMOVED_COLUMN
    assert before["sql"] == after["sql"]
    assert before["frames"].keys() == after["frames"].keys()
    for key in before["frames"]:
        assert_frame_equal(before["frames"][key], after["frames"][key], check_exact=True)
    for key, old in before["contracts"].items():
        new = after["contracts"][key]
        by_name = {c["physical_name"]: c["id"] for c in old["columns"]}
        old_ids = {c["id"]: c["physical_name"] for c in old["columns"]}
        for col in new["columns"]:
            if col["physical_name"] in by_name:
                assert old_ids.get(col["id"], col["physical_name"]) == col["physical_name"], (key, col)
                assert col["id"] == by_name[col["physical_name"]], (key, col)
        migrations = _build_column_migrations(node_columns_from_contract(old), node_columns_from_contract(new))
        assert not any(a != b and b in by_name for a, b in migrations.items() if b is not _REMOVED_COLUMN), (key, migrations)
        if key.startswith("types:"):
            assert [c["type"]["logical"] for c in new["columns"]] == ["geometry", "datetime", "number"], key
    print(f"Identici: {len(before['sql'])} SQL, {len(before['frames'])} DataFrame; contratti verificati: {len(before['contracts'])}")


if __name__ == "__main__":
    # Il percorso import viene scelto esplicitamente: mai un misto dei due tree.
    sys.path.insert(0, str(Path(sys.argv[1]).resolve()))
    result = probe(json.loads(Path(sys.argv[2]).read_text(encoding="utf-8")),
                   pickle.loads(Path(sys.argv[4]).read_bytes()) if len(sys.argv) > 4 else None)
    Path(sys.argv[3]).write_bytes(pickle.dumps(result))
    if len(sys.argv) > 4:
        compare(pickle.loads(Path(sys.argv[4]).read_bytes()), result)
