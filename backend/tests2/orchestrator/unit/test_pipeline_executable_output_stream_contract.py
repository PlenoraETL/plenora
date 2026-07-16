from io import BytesIO

import pytest

from workers.tasks import pipeline_task


def test_persist_executable_output_accepts_chunk_iterator(monkeypatch):
    consumed: dict[str, object] = {}
    source_closed = False

    def source():
        nonlocal source_closed
        try:
            yield b"first"
            yield b""
            yield bytearray(b"second")
        finally:
            source_closed = True

    def open_result_stream(result_id, **kwargs):
        consumed["open"] = (result_id, kwargs)
        return {"body": source(), "filename": "result.csv"}

    def save_chunks(task_id, filename, chunks, *, group_id):
        consumed["save"] = (task_id, filename, list(chunks), group_id)
        return "eseguibili/task-1/result.csv"

    monkeypatch.setattr(
        "app.core.results.blob.open_result_stream", open_result_stream
    )
    monkeypatch.setattr("app.core.eseguibili.storage.save_chunks", save_chunks)

    result = pipeline_task._persist_executable_output(
        contract={
            "execution_subject": {"kind": "eseguibile", "id": "executable-1"}
        },
        result={"result_id": "result-1"},
        task_id="task-1",
        group_id="group-1",
    )

    assert consumed["open"] == (
        "result-1",
        {"expected_project_id": "executable-1", "expected_group_id": "group-1"},
    )
    assert consumed["save"] == (
        "task-1",
        "result.csv",
        [b"first", b"second"],
        "group-1",
    )
    assert source_closed is True
    assert result == {
        "result_id": "result-1",
        "filename": "result.csv",
        "storage_key": "eseguibili/task-1/result.csv",
    }


def test_executable_output_chunks_accept_readable_stream_and_close_it():
    body = BytesIO(b"abcdef")

    chunks = list(
        pipeline_task._iter_executable_output_chunks(body, chunk_size=2)
    )

    assert chunks == [b"ab", b"cd", b"ef"]
    assert body.closed is True


def test_executable_output_chunks_reject_non_binary_iterables():
    with pytest.raises(TypeError, match="chunk binari"):
        list(pipeline_task._iter_executable_output_chunks(iter(["not-bytes"])))
