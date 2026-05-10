"""
Pure resource decision boundary: DB vs object storage payload target.

Source-of-truth per la decisione "questo payload va in DB BYTEA o su
"""

from __future__ import annotations

from typing import Literal


PayloadTarget = Literal["db", "object_storage"]


POLICY_DB = "db"
POLICY_OBJECT_STORAGE = "object_storage"
POLICY_HYBRID = "hybrid"


def resolve_payload_target(
    *,
    policy: str,
    size_bytes: int,
    threshold_bytes: int,
) -> PayloadTarget:
    """Decide se un payload va in DB BYTEA o su object storage.

    - `policy == "db"` -> sempre `"db"` (il caller applica
                                     l'eventuale hard cap come guard
                                     post-decisione separata).
    - `policy == "object_storage"` -> sempre `"object_storage"` (il caller
                                     fail-closed se l'upload fallisce).
    - `policy == "hybrid"` o qualunque altro valore (incluso unknown
      policy):
                                     `size_bytes >= threshold_bytes` ->
                                     `"object_storage"`, altrimenti
                                     `"db"`.

    Il match policy e' case-insensitive con strip difensivo: replica il
    `(settings.get(...) or "hybrid").strip.lower` che i caller
    esistenti gia' applicavano. Cosi' chiamate con `"DB"`/`" db "` non
    cadono silenziosamente nel ramo `else`.

    Args:
        policy: valore di STORAGE_POLICY normalizzato (ma il modulo lo
            re-normalizza). Valori canonici: `"db"`, `"object_storage"`,
            `"hybrid"`. Stringhe non riconosciute cadono nel branch
            threshold-based.
        size_bytes: dimensione del payload in byte.
        threshold_bytes: soglia per il branch hybrid. Per il dominio
            result e' `RESULT_S3_THRESHOLD_BYTES` (default 50 MB), per
            file-source e' `FILE_SOURCE_S3_THRESHOLD_BYTES` (default 50
            MB). Sono env distinte e restano tali (ops puo' tunarle in
            modo indipendente).

    Returns:
        `"db"` o `"object_storage"`.

    Note:
        Decisione su soglia inclusiva (`>=`): un payload esattamente di
        `threshold_bytes` byte va in object storage.
    """
    normalized = (policy or "").strip().lower()

    if normalized == POLICY_DB:
        return "db"
    if normalized == POLICY_OBJECT_STORAGE:
        return "object_storage"
    if size_bytes >= threshold_bytes:
        return "object_storage"
    return "db"
