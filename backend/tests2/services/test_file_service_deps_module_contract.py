"""
Smoke contract: Protocols + Defaults del dominio files.

Verifica le invarianti minime del refactor:
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


_PROTOCOLS = ("LoggerProtocol", "FileSystemProtocol", "DataLoaderProtocol", "TimeProviderProtocol")
_DEFAULTS = ("NullLogger", "DefaultTimeProvider", "DefaultFileSystem", "DefaultDataLoader")
_ALL = _PROTOCOLS + _DEFAULTS


# 1. Esposizione + identity dal core service


def test_file_service_deps_module_exposes_eight_symbols():
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


# 4. DefaultFileSystem (smoke su tmp_path)


def test_default_file_system_smoke_mkdir_write_read(tmp_path: Path):
    from app.core.files.service_deps import DefaultFileSystem

    fs = DefaultFileSystem()
    sub = tmp_path / "sub"
    fs.mkdir(sub)
    assert fs.exists(sub)
    assert fs.is_dir(sub)
    assert not fs.is_file(sub)

    target = sub / "hello.txt"
    fs.write_text(target, "ciao")
    assert fs.is_file(target)
    assert fs.read_text(target) == "ciao"
    assert fs.stat_size(target) == len("ciao".encode("utf-8"))

    items = fs.iterdir(sub)
    assert any(p.name == "hello.txt" for p in items)


def test_default_file_system_unlink_and_rmtree(tmp_path: Path):
    from app.core.files.service_deps import DefaultFileSystem

    fs = DefaultFileSystem()
    f = tmp_path / "x.txt"
    fs.write_text(f, "x")
    assert fs.exists(f)
    fs.unlink(f)
    assert not fs.exists(f)

    sub = tmp_path / "deletable"
    fs.mkdir(sub)
    fs.write_text(sub / "a.txt", "a")
    fs.rmtree(sub)
    assert not fs.exists(sub)


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


def test_default_data_loader_save_load_csv_round_trip_via_pandas(tmp_path: Path):
    """Save -> Load garantisce che i delimitatori scelti dal save_csv
    sono compatibili col detect_sep del load_csv (sniff csv)."""
    from app.core.files.service_deps import DefaultDataLoader

    loader = DefaultDataLoader()
    src = pd.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]})
    target = tmp_path / "rt.csv"
    loader.save_csv(src, target)

    loaded = loader.load_csv(target)
    assert list(loaded.columns) == ["a", "b"]
    assert len(loaded) == 3
    assert loaded.iloc[2]["b"] == "z"
