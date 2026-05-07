"""
Contract test per `app.shared.storage_dispatch.resolve_payload_target`.

Verifica:
"""

from __future__ import annotations

import pytest

from app.shared.storage_dispatch import (
    POLICY_DB,
    POLICY_HYBRID,
    POLICY_OBJECT_STORAGE,
    resolve_payload_target,
)


# Truth table

@pytest.mark.parametrize(
    "policy, size_bytes, threshold_bytes, expected",
    [
        # policy=db: sempre db, qualunque size/threshold
        ("db", 0, 100, "db"),
        ("db", 50, 100, "db"),
        ("db", 100, 100, "db"),
        ("db", 99999, 100, "db"),
        # policy=object_storage: sempre object_storage
        ("object_storage", 0, 100, "object_storage"),
        ("object_storage", 50, 100, "object_storage"),
        ("object_storage", 100, 100, "object_storage"),
        ("object_storage", 99999, 100, "object_storage"),
        # policy=hybrid: threshold-based (>=)
        ("hybrid", 0, 100, "db"),
        ("hybrid", 50, 100, "db"),
        ("hybrid", 99, 100, "db"),
        ("hybrid", 100, 100, "object_storage"),  # soglia inclusiva
        ("hybrid", 101, 100, "object_storage"),
        ("hybrid", 99999, 100, "object_storage"),
    ],
)
def test_truth_table(policy, size_bytes, threshold_bytes, expected):
    assert resolve_payload_target(
        policy=policy,
        size_bytes=size_bytes,
        threshold_bytes=threshold_bytes,
    ) == expected


# Case insensitivity + strip

@pytest.mark.parametrize(
    "policy",
    ["DB", "Db", "dB", " db ", "DB\n", "\tdb"],
)
def test_db_policy_case_insensitive_with_strip(policy):
    assert resolve_payload_target(
        policy=policy, size_bytes=10**9, threshold_bytes=1,
    ) == "db"


@pytest.mark.parametrize(
    "policy",
    ["OBJECT_STORAGE", "Object_Storage", " object_storage ", "object_storage\n"],
)
def test_object_storage_policy_case_insensitive_with_strip(policy):
    assert resolve_payload_target(
        policy=policy, size_bytes=0, threshold_bytes=10**9,
    ) == "object_storage"


@pytest.mark.parametrize(
    "policy",
    ["HYBRID", "Hybrid", " hybrid ", "hybrid\n"],
)
def test_hybrid_policy_case_insensitive_with_strip(policy):
    # size < threshold -> db
    assert resolve_payload_target(
        policy=policy, size_bytes=49, threshold_bytes=50,
    ) == "db"
    # size >= threshold -> object_storage
    assert resolve_payload_target(
        policy=policy, size_bytes=50, threshold_bytes=50,
    ) == "object_storage"


# Unknown / empty policy fallback

@pytest.mark.parametrize("policy", ["", " ", None, "ram", "unknown", "s3", "azure"])
def test_unknown_or_empty_policy_falls_back_to_threshold(policy):
    """Pre-001B: i due dispatch trattavano qualunque non-db/non-object_storage
    come `else` = hybrid. Preservato qui."""
    # Sotto soglia -> db
    assert resolve_payload_target(
        policy=policy or "", size_bytes=10, threshold_bytes=100,
    ) == "db"
    # Sopra soglia -> object_storage
    assert resolve_payload_target(
        policy=policy or "", size_bytes=10**9, threshold_bytes=100,
    ) == "object_storage"


def test_none_policy_does_not_crash():
    # `policy=None` accettato (re-normalizzato a "" stringa vuota).
    assert resolve_payload_target(
        policy=None,  # type: ignore[arg-type]
        size_bytes=0,
        threshold_bytes=100,
    ) == "db"


# Threshold edge cases

def test_hybrid_threshold_zero_means_always_object_storage():
    """Edge: threshold=0 e size=0 -> 0>=0 -> object_storage. Preserva
    il comportamento pre-001B (size >= threshold)."""
    assert resolve_payload_target(
        policy="hybrid", size_bytes=0, threshold_bytes=0,
    ) == "object_storage"


def test_hybrid_inclusive_at_boundary():
    """size_bytes == threshold_bytes -> object_storage (>= inclusivo)."""
    assert resolve_payload_target(
        policy="hybrid", size_bytes=1024, threshold_bytes=1024,
    ) == "object_storage"


# Purity: nessun env read, nessun import da services/db

def test_module_has_no_settings_or_services_imports():
    """Verifica statica che `storage_dispatch.py` non importi
    `app.config.settings`, `app.shared.cloud_backends`, ne' qualunque
    modulo da `app.services.*` / `app.db.*` / `app.utils.*`. Pure
    module = nessun side-effect, nessun env read."""
    from pathlib import Path
    src = Path(__file__).parent.parent.parent / "app" / "shared" / "storage_dispatch.py"
    text = src.read_text(encoding="utf-8")
    forbidden = [
        "from app.config",
        "import app.config",
        "from app.shared.cloud_backends",
        "from app.services",
        "import app.services",
        "from app.db",
        "import app.db",
        "from app.utils",
        "import app.utils",
        "os.getenv",
        "os.environ",
    ]
    for pattern in forbidden:
        assert pattern not in text, (
            f"storage_dispatch.py NON deve contenere {pattern!r}: "
            "il modulo deve restare pure."
        )


def test_resolve_does_not_read_env(monkeypatch):
    """Il caller passa policy/size/threshold gia' risolti. Anche se
    l'env e' impostata a valori contraddittori, il risultato dipende
    solo dagli argomenti."""
    monkeypatch.setenv("STORAGE_POLICY", "object_storage")
    monkeypatch.setenv("RESULT_S3_THRESHOLD_BYTES", "1")
    monkeypatch.setenv("FILE_SOURCE_S3_THRESHOLD_BYTES", "1")
    # Argomenti dicono `policy=db` -> sempre db, ignora env
    assert resolve_payload_target(
        policy="db", size_bytes=10**9, threshold_bytes=1,
    ) == "db"


# Constants exposed for callers

def test_policy_constants_match_canonical_strings():
    assert POLICY_DB == "db"
    assert POLICY_OBJECT_STORAGE == "object_storage"
    assert POLICY_HYBRID == "hybrid"


# Equivalenza con i dispatch pre-001B

def _legacy_should_use_object_storage_result_store(size: int, policy: str, threshold: int) -> bool:
    """Replica testuale del `result_store._should_use_object_storage`
    pre-001B (con threshold inlined come parametro)."""
    if policy == "db":
        return False
    if policy == "object_storage":
        return True
    return size >= threshold


def _legacy_should_use_object_storage_file_dispatch(size: int, policy: str, threshold: int) -> bool:
    """Replica testuale del ramo policy in
    `file_storage_dispatch.dispatch_blob_storage` pre-001B."""
    if policy == "db":
        return False
    if policy == "object_storage":
        return True
    # hybrid
    return size >= threshold


@pytest.mark.parametrize(
    "policy, size, threshold",
    [
        ("db", 0, 100),
        ("db", 1000, 100),
        ("object_storage", 0, 100),
        ("object_storage", 1000, 100),
        ("hybrid", 0, 100),
        ("hybrid", 99, 100),
        ("hybrid", 100, 100),
        ("hybrid", 101, 100),
    ],
)
def test_equivalence_with_legacy_result_store(policy, size, threshold):
    legacy = _legacy_should_use_object_storage_result_store(size, policy, threshold)
    new = resolve_payload_target(
        policy=policy, size_bytes=size, threshold_bytes=threshold,
    )
    assert (new == "object_storage") == legacy


@pytest.mark.parametrize(
    "policy, size, threshold",
    [
        ("db", 0, 100),
        ("db", 1000, 100),
        ("object_storage", 0, 100),
        ("object_storage", 1000, 100),
        ("hybrid", 0, 100),
        ("hybrid", 99, 100),
        ("hybrid", 100, 100),
        ("hybrid", 101, 100),
    ],
)
def test_equivalence_with_legacy_file_storage_dispatch(policy, size, threshold):
    legacy = _legacy_should_use_object_storage_file_dispatch(size, policy, threshold)
    new = resolve_payload_target(
        policy=policy, size_bytes=size, threshold_bytes=threshold,
    )
    assert (new == "object_storage") == legacy
