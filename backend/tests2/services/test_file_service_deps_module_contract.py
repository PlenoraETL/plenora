"""
Smoke contract: Protocols + Defaults del dominio files.

Verifica le invarianti minime del refactor post tombstone
DefaultFileSystem (2026-05-26): la DI surface ora copre solo
Logger / TimeProvider / DataLoader.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


_PROTOCOLS = ("LoggerProtocol", "DataLoaderProtocol", "TimeProviderProtocol")
_DEFAULTS = ("NullLogger", "DefaultTimeProvider", "DefaultDataLoader")
_ALL = _PROTOCOLS + _DEFAULTS


# 1. Esposizione + identity dal core service


def test_file_service_deps_module_exposes_six_symbols():
    from app.core.files import service_deps as deps

    for name in _ALL:
        sym = getattr(deps, name, None)
        assert sym is not None and isinstance(sym, type), (
            f"file_service_deps.{name} mancante o non e' una classe"
        )


def test_file_service_deps_reexports_dataframe_loader_boundary():
    from app.core.dataframes import default_loader
    from app.core.dataframes import ports
    from app.core.files import service_deps as deps

    assert deps.DataLoaderProtocol is ports.DataLoaderProtocol
    assert default_loader.DataLoaderProtocol is ports.DataLoaderProtocol
    assert deps.DefaultDataLoader is default_loader.DefaultDataLoader


def test_core_file_service_uses_deps_objects_by_identity():
    """app.core.files.service espone gli STESSI oggetti di service_deps.
    Identity preservata: monkeypatch su `app.core.files.service.X`
    modifica l'attributo del modulo service ma service_deps.X resta la
    classe sorgente."""
    from app.core.files import service as fs_mod
    from app.core.files import service_deps as deps

    for name in _ALL:
        assert getattr(fs_mod, name) is getattr(deps, name), (
            f"file_service.{name} e file_service_deps.{name} dovrebbero "
            f"essere lo stesso oggetto"
        )


# 2. DefaultTimeProvider


def test_default_time_provider_now_is_utc_aware():
    from app.core.files.service_deps import DefaultTimeProvider

    now = DefaultTimeProvider().now()
    assert isinstance(now, datetime)
    assert now.tzinfo is not None, "now() deve essere UTC-aware (no naive datetime)"
    assert now.utcoffset() == timezone.utc.utcoffset(now)


def test_default_time_provider_generate_uuid_is_32_hex():
    from app.core.files.service_deps import DefaultTimeProvider

    out = DefaultTimeProvider().generate_uuid()
    assert isinstance(out, str)
    assert len(out) == 32, "uuid hex full = 32 char (post-N1)"
    int(out, 16)  # deve essere parsabile come hex


# 3. NullLogger


def test_null_logger_is_no_op_on_all_levels():
    from app.core.files.service_deps import NullLogger

    log = NullLogger()
    # Nessuno deve sollevare; ritorno implicito None.
    assert log.info("x") is None
    assert log.error("x") is None
    assert log.debug("x") is None
    assert log.warning("x") is None


# 4. DefaultFileSystem (tombstone sentinel)


def test_default_file_system_tombstoned_post_audit_followup():
    """Audit follow-up (2026-05-26): `DefaultFileSystem` + `FileSystemProtocol`
    rimossi interamente da `service_deps`.

    Razionale: i metodi `mkdir`/`exists`/`read_text`/`stat_size`/`rmtree`/
    `unlink`/`rglob`/`iterdir`/`is_dir`/`is_file` non erano chiamati a
    runtime — `DefaultFileSystem()` era istanziato come DI default in
    `FileSourceService.__init__` e `DataSourceService.preview_file_temp`,
    e passato a `preview_file_temp(fs=...)` che lo ignorava esplicitamente.
    Storia tombstone metodi write: 2026-05-25 (`write_text`, `extract_zip`)
    + 2026-05-26 follow-up (`write_binary`)."""
    from app.core.files import service_deps as deps

    for legacy in ("DefaultFileSystem", "FileSystemProtocol"):
        assert not hasattr(deps, legacy), (
            f"service_deps.{legacy} ancora esposto: tombstone DI cleanup incompleto."
        )


# 5. DefaultDataLoader.load_csv (round-trip minimo)


def test_default_data_loader_load_csv_round_trip(tmp_path: Path):
    from app.core.files.service_deps import DefaultDataLoader

    loader = DefaultDataLoader()
    csv_path = tmp_path / "mini.csv"
    csv_path.write_text("id,name\n1,alpha\n2,beta\n", encoding="utf-8")

    df = loader.load_csv(csv_path)
    assert list(df.columns) == ["id", "name"]
    assert len(df) == 2
    assert df.iloc[0]["id"] == 1
    assert df.iloc[1]["name"] == "beta"


def test_default_data_loader_save_methods_tombstone_post_phase5():
    """Post-Phase5: i metodi `save_*` su DefaultDataLoader sono rimossi
    (dead code, nessun caller live in app/). Per scrivere DataFrame in
    output usare `tabular_dataframe_to_bytes` + `storage.save_bytes`."""
    from app.core.files.service_deps import DefaultDataLoader

    for legacy in ("save_csv", "save_excel", "save_json", "save_geospatial"):
        assert not hasattr(DefaultDataLoader, legacy), (
            f"DefaultDataLoader.{legacy} ancora presente: refactor "
            f"RAM-only incompleto."
        )
