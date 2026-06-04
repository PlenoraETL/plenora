"""Sessione 12 contract test: 6 P2 HIGH backend.

Bug coperti:
1. results/db.py save_result_row → ON CONFLICT DO NOTHING + log warning
2. resources/blob_dispatch.py -> Path(filename).name sanitize
3. arricchisci/api_connection_rest_file → output_ext regex validato
4. datasources/sharepoint.py:368 → log via _sanitize_sharepoint_error
5. orchestrator/task_service.py → error fallback safe (no str(meta) raw)
6. eseguibili/service.py → temp_name idempotente via hash sha256
"""

from __future__ import annotations

import inspect

import pytest

from tests2.shared._ast_identifiers import all_identifiers, uses_attribute


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.regression]


def test_save_result_row_uses_on_conflict_do_nothing():
    """`save_result_row` ON CONFLICT DO NOTHING (no overwrite) + warning."""
    from app.core.results import db as results_db

    src = inspect.getsource(results_db.save_result_row)
    src_no_comments = "\n".join(
        line for line in src.splitlines() if not line.lstrip().startswith("#")
    )

    assert "ON CONFLICT (id) DO NOTHING" in src_no_comments
    # Anti-regression: no DO UPDATE che sovrascrive silenziosamente
    assert "ON CONFLICT (id) DO UPDATE" not in src_no_comments
    # Warning sull'anomalia
    assert "[ANOMALIA]" in src_no_comments


def test_storage_dispatch_sanitizes_filename_via_basename():
    """storage_key costruzione usa `Path(filename).name` per stripping
    directory component (path traversal residual)."""
    from app.core.resources import blob_dispatch as storage_dispatch

    src = inspect.getsource(storage_dispatch)
    src_no_comments = "\n".join(
        line for line in src.splitlines() if not line.lstrip().startswith("#")
    )

    assert "safe_filename" in all_identifiers(src_no_comments)
    # RAW: espressione-pattern complessa (Path(str(filename)).name), non solo identificatore
    assert "_Path(str(filename)).name" in src_no_comments or "Path(str(filename)).name" in src_no_comments


def test_rest_file_processor_sanitizes_output_extension():
    """`_execute_rest_file` valida output_ext con regex `\\.[A-Za-z0-9_]{1,16}`."""
    from app.core.arricchisci import api_connection_rest_file

    src = inspect.getsource(api_connection_rest_file)
    assert uses_attribute(src, "re", "match") or uses_attribute(src, "re", "fullmatch")
    # RAW: literal regex character-class content, non un identificatore
    assert "[A-Za-z0-9_]" in src
    # RAW: stringa-log + statement di assegnazione, non identificatori
    assert "fallback a '.bin'" in src or "output_ext = '.bin'" in src


def test_sharepoint_recursion_uses_sanitize_helper():
    """`list_files` recursion warning usa `_sanitize_sharepoint_error`
    invece di `{e}` raw che leakerebbe token Bearer."""
    from app.core.datasources import sharepoint

    src = inspect.getsource(sharepoint)
    # Trova il blocco "Errore ricorsione cartella"
    idx = src.find("Errore ricorsione cartella")
    assert idx >= 0
    window = src[max(0, idx - 200):idx + 200]
    assert "_sanitize_sharepoint_error" in window, (
        "logger.warning della ricorsione deve usare _sanitize_sharepoint_error "
        "per evitare token leak in ClientRequestException"
    )


def test_task_service_error_fallback_does_not_leak_meta_payload():
    """`task_service` fallback su FAILURE NON deve emettere `str(meta)` raw
    (che serializzerebbe l'intero dict meta con context interno)."""
    from app.core.orchestrator import task_service

    # Cerca il metodo `get_task` o equivalente che assembla error
    src = inspect.getsource(task_service)
    src_no_comments = "\n".join(
        line for line in src.splitlines() if not line.lstrip().startswith("#")
    )

    # Anti-regression: niente piu' `str(meta) if status == TaskStatus.FAILURE`
    # in entrambi i branch fallback
    assert "Errore sconosciuto" in src_no_comments, (
        "task_service deve usare un fallback safe ('Errore sconosciuto') "
        "invece di str(meta) raw che leakeva payload Celery"
    )


def test_eseguibili_temp_name_uses_hash_signature():
    """`eseguibili.service.execute` usa hash sha256 deterministico per
    `temp_name` invece di `uuid.uuid4()` non deterministico."""
    from app.core.eseguibili import service as eseguibili_service

    src = inspect.getsource(eseguibili_service.execute)
    src_no_comments = "\n".join(
        line for line in src.splitlines() if not line.lstrip().startswith("#")
    )

    # Anti-regression: niente piu' uuid.uuid4().hex per temp_name
    # (puo' essere usato altrove ma non per costruire temp_name)
    assert uses_attribute(src_no_comments, "hashlib", "sha256")
    assert "_df_signature" in all_identifiers(src_no_comments)
    # Il temp_name deve usare la signature, non uuid4
    temp_name_idx = src_no_comments.find('temp_name = f"__exec_temp_')
    assert temp_name_idx >= 0
    temp_name_line = src_no_comments[temp_name_idx:temp_name_idx + 150]
    assert "_df_signature" in temp_name_line
