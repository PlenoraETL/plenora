"""Contracts for range-backed Parquet previews."""

from __future__ import annotations

import io

import pytest

from app.core.io.bounded_range_reader import BoundedRangeReader
from app.core.resources.exceptions import MemoryLimitExceededError


pytestmark = [pytest.mark.unit, pytest.mark.contract]


def test_bounded_range_reader_counts_unique_bytes_only() -> None:
    payload = bytes(range(100))
    reader = BoundedRangeReader(
        size=len(payload),
        read_range=lambda start, end: payload[start:end + 1],
        max_bytes=12,
        label="artifact.parquet",
        stage="preview",
    )

    assert reader.read(8) == payload[:8]
    reader.seek(4)
    assert reader.read(8) == payload[4:12]
    reader.seek(12)
    with pytest.raises(MemoryLimitExceededError) as exc_info:
        reader.read(1)

    assert exc_info.value.observed_bytes == 13
    assert exc_info.value.limit_bytes == 12


def test_artifact_preview_uses_ranges_without_opening_full_stream(monkeypatch):
    import pyarrow as pa
    import pyarrow.parquet as pq

    from app.core.intermediate import artifact_reader

    table = pa.table({
        "id": list(range(1000)),
        "payload": [bytes([i % 251]) * 1024 for i in range(1000)],
    })
    sink = io.BytesIO()
    pq.write_table(
        table,
        sink,
        row_group_size=100,
        compression=None,
    )
    payload = sink.getvalue()

    class RangeStorage:
        def __init__(self) -> None:
            self.ranges: list[tuple[int, int]] = []

        def get_object_size(self, _key: str) -> int:
            return len(payload)

        def get_object_range(self, _key: str, *, start: int, end: int) -> bytes:
            self.ranges.append((start, end))
            return payload[start:end + 1]

        def get_object_stream(self, _key: str) -> dict:
            raise AssertionError("range-capable storage must not open full stream")

    storage = RangeStorage()
    monkeypatch.setenv("ARTIFACT_PREVIEW_MAX_BYTES", str(256 * 1024))
    monkeypatch.setattr(
        "app.core.io.object_storage.get_runtime_object_storage",
        lambda: storage,
    )

    result = artifact_reader.load_preview_from_artifact(
        "intermediate/key/data.parquet",
        expected_group_id="g1",
        limit=5,
    )

    assert result.error is None
    assert result.row_count == 1000
    assert [row["id"] for row in result.rows] == [0, 1, 2, 3, 4]
    assert storage.ranges
    assert not any(
        start == 0 and end == len(payload) - 1
        for start, end in storage.ranges
    )


def test_parquet_preview_disables_cross_row_group_prebuffer(monkeypatch) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    from app.core.intermediate.preview_parser import parse_parquet_preview

    sink = io.BytesIO()
    pq.write_table(pa.table({"id": [1, 2]}), sink, row_group_size=1)

    original = pq.ParquetFile
    constructor_options: dict[str, object] = {}

    def recording_parquet_file(source, *args, **kwargs):
        constructor_options.update(kwargs)
        return original(source, *args, **kwargs)

    monkeypatch.setattr(pq, "ParquetFile", recording_parquet_file)

    parsed = parse_parquet_preview(sink.getvalue(), limit=1)

    assert parsed.rows == [{"id": 1}]
    assert constructor_options.get("pre_buffer") is False
