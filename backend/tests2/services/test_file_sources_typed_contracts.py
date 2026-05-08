"""
Sentinel contract test - BE-SHAPE-001 + BE-SHAPE-002.

Verifica che i due TypedDict introdotti come typed contracts go-ready
"""

from __future__ import annotations

import inspect

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


# 1. FileSourceRow contract


def test_file_source_row_keys_cover_save_file_source_kwargs():
    """`FileSourceRow.__annotations__` deve coprire tutti i kwargs di
    `save_file_source` ECCETTO il kwarg legacy positional `fmt` (la
    colonna SQL si chiama `format` ma il param Python e' `fmt` per
    evitare clash con la builtin)."""
    from app.core.files.db import FileSourceRow, save_file_source

    sig = inspect.signature(save_file_source)
    save_kwargs = set(sig.parameters.keys())  # file_id, group_id, name, ...

    row_keys = set(FileSourceRow.__annotations__.keys())

    missing = save_kwargs - row_keys
    assert not missing, (
        f"FileSourceRow non copre tutti i kwargs di save_file_source. "
        f"Mancano: {sorted(missing)}. Aggiungili al TypedDict in "
        f"app/db/config/file_sources.py."
    )


def test_file_source_row_required_keys_present():
    """I 5 campi obbligatori per un INSERT valido devono essere
    annotati in `FileSourceRow` (TypedDict ha total=False, ma i caller
    devono sempre fornirli)."""
    from app.core.files.db import FileSourceRow

    required = {"file_id", "group_id", "name", "original_filename", "fmt"}
    row_keys = set(FileSourceRow.__annotations__.keys())
    assert required.issubset(row_keys), (
        f"FileSourceRow manca chiavi obbligatorie {required - row_keys}"
    )


def test_file_source_row_config_facade_points_at_canonical_contract():
    """`FileSourceRow` deve restare ancorato al modulo canonico."""
    from app.core.files.db import FileSourceRow as canonical
    from app.db.config_db_service import FileSourceRow as via_config_facade

    assert via_config_facade is canonical


# 2. BlobDispatchResult contract


def test_blob_dispatch_result_has_exactly_three_keys():
    """`BlobDispatchResult` ha esattamente {data, storage_key, size_bytes}.
    I caller (`file_raw_upload`, `file_upload`, `file_multi_layer_upload`)
    fanno splattare il return con `**blob_kwargs` su `db.save_file_source`
    / `save_file_sources_batch`: aggiungere una chiave qui senza
    aggiornare i 3 caller crea row DB inconsistenti."""
    from app.services.file_storage_dispatch import BlobDispatchResult

    expected = {"data", "storage_key", "size_bytes"}
    actual = set(BlobDispatchResult.__annotations__.keys())
    assert actual == expected, (
        f"BlobDispatchResult shape drift: atteso {sorted(expected)}, "
        f"trovato {sorted(actual)}"
    )


def test_blob_dispatch_result_subset_of_file_source_row():
    """Le 3 chiavi di `BlobDispatchResult` devono essere un subset di
    `FileSourceRow`: il caller fa `**blob_kwargs` dentro la row del
    batch save / dei kwargs del single-row save. Se BlobDispatchResult
    introduce una chiave non presente in FileSourceRow, save_file_source
    rifiuterebbe il kwarg con TypeError."""
    from app.core.files.db import FileSourceRow
    from app.services.file_storage_dispatch import BlobDispatchResult

    blob_keys = set(BlobDispatchResult.__annotations__.keys())
    row_keys = set(FileSourceRow.__annotations__.keys())
    drift = blob_keys - row_keys
    assert not drift, (
        f"BlobDispatchResult ha chiavi non presenti in FileSourceRow: "
        f"{sorted(drift)}. I caller che fanno **blob_kwargs nel "
        f"save_file_source crasherebbero con TypeError."
    )
