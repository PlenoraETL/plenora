from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from app.core.manipola.executor import ManipolaExecutor
from app.core.eseguibili import storage as executable_storage
from app.core.features.features_service import FeaturesService
from app.core.orchestrator import executable_output
from app.core.orchestrator.executable_output import ExecutableOutputPersistence
from app.core.orchestrator.service import OrchestratorService
from app.core.projects.canvas_models import CanvasState


class _MemoryExecutableWriter:
    instances = []

    def __init__(self, task_id, filename, *, group_id):
        self.task_id = task_id
        self.filename = filename
        self.group_id = group_id
        self.data = bytearray()
        self.aborted = False
        self.key = f"tenants/{group_id}/eseguibili-outputs/{task_id}/{filename}"
        self.__class__.instances.append(self)

    def write(self, data):
        self.data.extend(data)
        return len(data)

    def finalize(self):
        return self.key

    def abort(self):
        self.aborted = True


class _MultipartClient:
    def __init__(self):
        self.uploaded = []
        self.completed = []
        self.aborted = []
        self.put = []

    def create_multipart_upload(self, **kwargs):
        return {"UploadId": "upload-1"}

    def upload_part(self, **kwargs):
        self.uploaded.append(kwargs)
        return {"ETag": f"etag-{kwargs['PartNumber']}"}

    def complete_multipart_upload(self, **kwargs):
        self.completed.append(kwargs)

    def abort_multipart_upload(self, **kwargs):
        self.aborted.append(kwargs)

    def put_object(self, **kwargs):
        self.put.append(kwargs)


class _MultipartStorage:
    bucket = "bucket"

    def __init__(self):
        self.client = _MultipartClient()
        self.tags = []

    def _client(self):
        return self.client

    def _extra_args(self):
        return {}

    def set_object_tags(self, key, tags):
        self.tags.append((key, tags))


def test_executable_output_writer_streams_parts_and_marks_object(monkeypatch):
    storage = _MultipartStorage()
    monkeypatch.setattr(executable_storage, "_storage", lambda: storage)
    monkeypatch.setattr(executable_storage, "_PART_SIZE", 4)

    writer = executable_storage.ExecutableOutputWriter(
        "task-1", "result.csv", group_id="group-1"
    )
    assert writer.write(b"abcdef") == 6
    assert writer.tell() == 6
    key = writer.finalize()

    assert [call["Body"] for call in storage.client.uploaded] == [b"abcd", b"ef"]
    assert len(storage.client.completed) == 1
    assert storage.tags == [(key, executable_storage.EPHEMERAL_TAGS)]
    assert writer.closed is True


def test_executable_output_writer_empty_payload_uses_put_object(monkeypatch):
    storage = _MultipartStorage()
    monkeypatch.setattr(executable_storage, "_storage", lambda: storage)

    writer = executable_storage.ExecutableOutputWriter(
        "task-empty", "empty.csv", group_id="group-1"
    )
    writer.finalize()

    assert len(storage.client.aborted) == 1
    assert storage.client.put[0]["Body"] == b""


def test_executable_dataframe_output_bypasses_results_table(monkeypatch):
    _MemoryExecutableWriter.instances.clear()
    monkeypatch.setattr(
        executable_output.executable_storage,
        "ExecutableOutputWriter",
        _MemoryExecutableWriter,
    )

    persistence = ExecutableOutputPersistence(task_id="task-1", group_id="group-1")
    saved = persistence.persist_dataframe_output(
        df=pd.DataFrame({"id": [1, 2]}),
        output_format="csv",
        filename_stem="report",
    )

    writer = _MemoryExecutableWriter.instances[-1]
    assert bytes(writer.data).replace(b"\r\n", b"\n") == b"id\n1\n2\n"
    assert saved.output_filename == "report.csv"
    assert saved.storage_key.endswith("/task-1/report.csv")


@pytest.mark.parametrize(
    ("output_format", "expected_filename"),
    [("gpkg", "report.gpkg"), ("shp", "report.zip")],
)
def test_executable_geospatial_output_never_falls_back_to_csv(
    monkeypatch,
    output_format,
    expected_filename,
):
    _MemoryExecutableWriter.instances.clear()
    monkeypatch.setattr(
        executable_output.executable_storage,
        "ExecutableOutputWriter",
        _MemoryExecutableWriter,
    )
    monkeypatch.setattr(
        executable_output.frame_io_module,
        "save_dataframe_to_bytes",
        lambda *args, **kwargs: b"real-geospatial-payload",
    )
    monkeypatch.setattr(
        executable_output,
        "detect_geometry_column",
        lambda df: "geometry",
    )

    saved = ExecutableOutputPersistence(
        task_id="task-geo",
        group_id="group-1",
    ).persist_dataframe_output(
        df=pd.DataFrame({"id": [1], "geometry": ["POINT (0 0)"]}),
        output_format=output_format,
        filename_stem="report",
    )

    writer = _MemoryExecutableWriter.instances[-1]
    assert bytes(writer.data) == b"real-geospatial-payload"
    assert saved.output_filename == expected_filename


def test_executable_sql_stream_returns_storage_key_without_result_id(monkeypatch):
    _MemoryExecutableWriter.instances.clear()
    monkeypatch.setattr(
        executable_output.executable_storage,
        "ExecutableOutputWriter",
        _MemoryExecutableWriter,
    )

    def stream_query(**kwargs):
        kwargs["output_writer"].write(b"id\n1\n")
        return 1, ["id"]

    monkeypatch.setattr(executable_output, "execute_streaming_to_writer", stream_query)

    persistence = ExecutableOutputPersistence(task_id="task-sql", group_id="group-1")
    saved = persistence.persist_streaming_query_to_storage(
        project_id="exec_123",
        engine_factory=object(),
        connection=object(),
        sql="SELECT 1",
        params=None,
        output_format="csv",
        output_filename="query.csv",
        group_id="group-1",
    )

    assert saved.result_id is None
    assert saved.output_path == Path("query.csv")
    assert saved.storage_key.endswith("/task-sql/query.csv")
    assert saved.rows_count == 1


def test_manipola_executable_save_never_calls_generic_result_store(monkeypatch):
    def forbidden_result_save(**kwargs):
        raise AssertionError("results table must not be used for exec_* owners")

    monkeypatch.setattr(
        "app.core.manipola.executor.save_pipeline_result_outcome",
        forbidden_result_save,
    )

    captured = {}

    class DirectPersistence:
        def persist_dataframe_output(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                output_filename="report.csv",
                storage_key="tenants/group-1/eseguibili-outputs/task-1/report.csv",
                output_columns=None,
            )

    executor = ManipolaExecutor(project_service=object())
    result = executor._salva_risultato(
        "exec_123",
        SimpleNamespace(fileName="report", format="csv", geomColumn=None, data={}),
        pd.DataFrame({"id": [1]}),
        group_id_override="group-1",
        output_persistence=DirectPersistence(),
    )

    assert result["success"] is True
    assert result["result_id"] is None
    assert result["storage_key"].endswith("/task-1/report.csv")
    assert captured["filename_stem"] == "report"


def test_arcgis_executable_save_never_calls_generic_result_store(monkeypatch):
    def forbidden_result_save(**kwargs):
        raise AssertionError("results table must not be used for exec_* owners")

    monkeypatch.setattr(
        "app.core.features.features_service.save_pipeline_result_outcome",
        forbidden_result_save,
    )

    class DirectPersistence:
        def persist_dataframe_output(self, **kwargs):
            return SimpleNamespace(
                output_filename="features.geojson",
                storage_key=(
                    "tenants/group-1/eseguibili-outputs/" "task-arcgis/features.geojson"
                ),
            )

    service = object.__new__(FeaturesService)
    service._get_layer_crs = lambda *args, **kwargs: "EPSG:4326"
    service.query_layer = lambda *args, **kwargs: ([[1]], ["id"])

    result = service.execute_query(
        connection_id="arcgis-1",
        layer_url="https://example.invalid/FeatureServer/0",
        project_id="exec_123",
        output_filename="features",
        output_format="geojson",
        return_geometry=False,
        group_id="group-1",
        output_persistence=DirectPersistence(),
    )

    assert result["success"] is True
    assert result["result_id"] is None
    assert str(result["storage_key"]).endswith("/task-arcgis/features.geojson")


def test_orchestrator_injects_direct_persistence_for_executable_contract():
    captured = {}

    class Estrai:
        def execute(self, project_id, canvas, **kwargs):
            captured.update(kwargs)
            return {
                "success": True,
                "output_path": "report.csv",
                "storage_key": "tenants/group-1/eseguibili-outputs/task-1/report.csv",
                "result_id": None,
            }

    service = object.__new__(OrchestratorService)
    service.estrai_executor = Estrai()
    service.manipola_executor = object()
    service.enrich_executor = object()

    result = service._execute_loaded_pipeline(
        project_id="exec_123",
        project_type="estrai",
        canvas=CanvasState(nodes=[], edges=[]),
        force_recalculate=True,
        skip_carica=True,
        execution_context={
            "group_id": "group-1",
            "output_task_id": "task-1",
        },
    )

    assert result.success is True
    assert result.result_id is None
    assert result["storage_key"].endswith("/task-1/report.csv")
    assert isinstance(captured["output_persistence"], ExecutableOutputPersistence)
