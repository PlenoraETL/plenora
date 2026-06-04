"""
Contract test BE-REST-001A — `ExternalArtifactProcessor` Protocol.

Contract tests for the external artifact processor boundary.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import get_type_hints

import pytest

from tests2.shared._ast_identifiers import (
    all_identifiers,
    imports_module,
    uses_attribute,
)

from app.shared.resources.external_artifact_processor import (
    ArtifactResult,
    ArtifactSource,
    ExternalArtifactProcessor,
    ProcessorConfig,
)


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


_BACKEND_APP = Path(__file__).resolve().parent.parent.parent / "app"


# 1. ArtifactSource shape


def test_artifact_source_fields():
    s = ArtifactSource(data=b"abc", filename="in.dxf")
    assert s.data == b"abc"
    assert s.filename == "in.dxf"
    assert s.content_type == "application/octet-stream"


def test_artifact_source_with_explicit_content_type():
    s = ArtifactSource(data=b"x", filename="m.json", content_type="application/json")
    assert s.content_type == "application/json"


def test_artifact_source_is_frozen():
    s = ArtifactSource(data=b"x", filename="x")
    with pytest.raises(FrozenInstanceError):
        s.data = b"y"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        s.filename = "z"  # type: ignore[misc]


def test_artifact_source_field_types():
    hints = get_type_hints(ArtifactSource)
    assert hints["data"] is bytes
    assert hints["filename"] is str
    assert hints["content_type"] is str


# 2. ProcessorConfig shape


def test_processor_config_required_endpoint():
    c = ProcessorConfig(endpoint_url="https://imdf.plenora.it/api/process")
    assert c.endpoint_url == "https://imdf.plenora.it/api/process"
    assert c.method == "POST"
    assert c.timeout_seconds == 60.0
    assert c.multipart_field_name == "file"
    assert dict(c.extra_form_fields) == {}
    assert dict(c.extra_headers) == {}


def test_processor_config_overrides():
    c = ProcessorConfig(
        endpoint_url="https://x.test/v1/process",
        method="PUT",
        timeout_seconds=120.0,
        multipart_field_name="input",
        extra_form_fields={"opt": "v"},
        extra_headers={"X-Trace-ID": "t-1"},
    )
    assert c.method == "PUT"
    assert c.timeout_seconds == 120.0
    assert c.multipart_field_name == "input"
    assert c.extra_form_fields == {"opt": "v"}
    assert c.extra_headers == {"X-Trace-ID": "t-1"}


def test_processor_config_is_frozen():
    c = ProcessorConfig(endpoint_url="https://x.test")
    with pytest.raises(FrozenInstanceError):
        c.endpoint_url = "https://y.test"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        c.timeout_seconds = 1.0  # type: ignore[misc]


# 3. ArtifactResult shape


def test_artifact_result_fields():
    r = ArtifactResult(
        data=b"PK\x03\x04",
        filename="out.zip",
        content_type="application/zip",
        status_code=200,
        headers={"Content-Length": "100"},
    )
    assert r.data == b"PK\x03\x04"
    assert r.filename == "out.zip"
    assert r.content_type == "application/zip"
    assert r.status_code == 200
    assert r.headers["Content-Length"] == "100"


def test_artifact_result_default_headers_empty():
    r = ArtifactResult(
        data=b"", filename="x", content_type="application/octet-stream",
        status_code=200,
    )
    assert dict(r.headers) == {}


def test_artifact_result_is_frozen():
    r = ArtifactResult(data=b"", filename="x", content_type="t", status_code=200)
    with pytest.raises(FrozenInstanceError):
        r.status_code = 500  # type: ignore[misc]


def test_artifact_result_equality_by_value():
    a = ArtifactResult(data=b"x", filename="f", content_type="t", status_code=200)
    b = ArtifactResult(data=b"x", filename="f", content_type="t", status_code=200)
    assert a == b


# 4. Protocol shape


def test_protocol_has_process_method():
    assert hasattr(ExternalArtifactProcessor, "process")


def test_protocol_is_runtime_checkable():
    class _MinImpl:
        def process(
            self, source: ArtifactSource, config: ProcessorConfig,
        ) -> ArtifactResult:
            return ArtifactResult(
                data=b"", filename="x", content_type="t", status_code=200,
            )

    assert isinstance(_MinImpl(), ExternalArtifactProcessor)


def test_protocol_rejects_incomplete_impl():
    class _NoProcess:
        pass

    assert not isinstance(_NoProcess(), ExternalArtifactProcessor)


# 5. Pure module — niente import pesanti


def test_module_has_no_heavy_imports():
    """Il modulo shared deve restare protocol/dataclass-only: niente
    requests/httpx/pandas/config/I-O/env reads."""
    src = (
        _BACKEND_APP / "shared" / "resources" / "external_artifact_processor.py"
    ).read_text(
        encoding="utf-8",
    )
    assert not imports_module(src, "requests"), (
        "external_artifact_processor.py NON deve contenere 'import requests'/'from requests': "
        "modulo deve restare pure protocol/dataclass."
    )
    assert not imports_module(src, "httpx"), (
        "external_artifact_processor.py NON deve contenere 'import httpx'/'from httpx': "
        "modulo deve restare pure protocol/dataclass."
    )
    assert not imports_module(src, "pandas"), (
        "external_artifact_processor.py NON deve contenere 'import pandas'/'from pandas': "
        "modulo deve restare pure protocol/dataclass."
    )
    assert not imports_module(src, "boto3"), (
        "external_artifact_processor.py NON deve contenere 'import boto3': "
        "modulo deve restare pure protocol/dataclass."
    )
    assert not imports_module(src, "app.infrastructure.storage.cloud_backends"), (
        "external_artifact_processor.py NON deve contenere 'from app.infrastructure.storage.cloud_backends': "
        "modulo deve restare pure protocol/dataclass."
    )
    assert not imports_module(src, "app.config"), (
        "external_artifact_processor.py NON deve contenere 'from app.config': "
        "modulo deve restare pure protocol/dataclass."
    )
    assert not imports_module(src, "os"), (
        "external_artifact_processor.py NON deve contenere 'import os': "
        "modulo deve restare pure protocol/dataclass."
    )
    assert not uses_attribute(src, "os", "getenv"), (
        "external_artifact_processor.py NON deve contenere 'os.getenv': "
        "modulo deve restare pure protocol/dataclass."
    )
    assert not uses_attribute(src, "os", "environ"), (
        "external_artifact_processor.py NON deve contenere 'os.environ': "
        "modulo deve restare pure protocol/dataclass."
    )
    assert "open" not in all_identifiers(src), (
        "external_artifact_processor.py NON deve contenere 'open(': "
        "modulo deve restare pure protocol/dataclass."
    )
    assert "Path" not in all_identifiers(src), (
        "external_artifact_processor.py NON deve contenere 'Path(': "
        "modulo deve restare pure protocol/dataclass."
    )


# 6. Parity audit: Protocol common denominator vs current mixin


def test_existing_mixin_has_equivalent_concept():
    """Audit: l'unica impl concrete oggi vive in
    `ApiConnectionRestFileMixin`. Il metodo `_execute_rest_file`
    (privato) implementa il pattern process(source, config) ->
    result. Questo test sentinella verifica solo l'esistenza del
    metodo + dei suoi 2 helper canonici (multipart build + dual-timeout
    download). Niente delega/wiring runtime — solo audit go-ready."""
    from app.core.arricchisci import api_connection_rest_file as mod

    cls = mod.ApiConnectionRestFileMixin
    assert hasattr(cls, "_build_rest_file_multipart")
    assert hasattr(cls, "_download_binary_response")
    # `_execute_rest_file` esiste sul mixin (lookup case-insensitive
    # via dir() per resilienza a refactor futuri di nome).
    methods = {n for n in dir(cls) if "rest_file" in n.lower()}
    assert any("execute" in n.lower() for n in methods), (
        "ApiConnectionRestFileMixin deve avere il metodo orchestratore "
        "del REST_FILE pipeline (oggi `_execute_rest_file`)."
    )


def test_no_runtime_wiring_between_protocol_and_mixin():
    """Sentinella: il Protocol non e' ancora wired dentro il mixin
    (audit-only). Quando un secondo consumer reale emerge si fa
    REST-002 (extraction effettiva). Questo test fallisce se qualcuno
    accidentalmente importa il Protocol nel modulo del mixin senza
    aggiornare il piano."""
    from app.core.arricchisci import api_connection_rest_file as mod
    src = Path(mod.__file__).read_text(encoding="utf-8")
    assert not imports_module(src, "app.shared.resources.external_artifact_processor"), (
        "BE-REST-001A e' audit-only. Wiring del Protocol nel mixin "
        "richiede REST-002 (extraction completa con test isolati)."
    )
