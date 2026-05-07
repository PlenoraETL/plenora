"""
Contract test per `app.core.datasources.local.LocalDataSource` — edge case.

Copre gap reali rispetto ai test esistenti:
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.core.datasources.interface import (
    BackendConfig,
    DataSourcePermissionError,
)
from app.core.datasources.local import LocalDataSource


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.shared]


def _make_source(root_path) -> LocalDataSource:
    return LocalDataSource(
        BackendConfig(id="ds-edge", type="local", root_path=str(root_path))
    )


def test_abs_rejects_windows_drive_letter_even_on_linux_runner(tmp_path):
    """Blinda §1.6 fail-closed: input con drive letter Windows (``C:``,
    ``D:``) DEVE essere rifiutato con ``DataSourcePermissionError``.

    Questa è una branch separata del check in ``_abs`` (condizione
    ``relative[1] == ':'``), non coperta dal test ``/etc/passwd``
    che copre solo la branch ``startswith('/', '\\')``.

    Regressione: un refactor che unificasse le branch potrebbe saltare
    il drive letter case → su runner Linux processando config di client
    Windows, il path ``C:\\data\\leak.txt`` come relative passerebbe e
    verrebbe trattato come relativo, generando output non intenzionali.
    """
    ds = _make_source(tmp_path)

    for evil in ("C:\\Windows\\System32", "D:\\secret\\data.csv", "c:dir\\f.txt"):
        with pytest.raises(
            DataSourcePermissionError, match="Drive letter non consentita",
        ):
            ds._abs(evil)


def test_abs_with_empty_string_returns_root_absolute_path(tmp_path):
    """Blinda contract operazionale: ``_abs("")`` ritorna root_abs.

    Caso d'uso reale: ``service.list_files(ds_id, path="")`` chiama
    ``_abs("")`` per enumerare la root. Se il metodo sollevasse su
    empty, l'intera UI di browse si romperebbe.

    Edge case importante perché il ramo ``if not relative`` è una
    early-return separata da tutto il resto della policy.
    """
    ds = _make_source(tmp_path)

    result = ds._abs("")

    # Il risultato DEVE essere il path assoluto della root, niente di più.
    assert Path(result) == Path(str(tmp_path)).resolve(), (
        f"_abs('') deve ritornare root_abs, ottenuto {result!r}"
    )


def test_abs_joins_deep_nested_relative_path_under_root(tmp_path):
    """Blinda normalizzazione su nesting profondo (3+ livelli).

    Il test esistente ``test_abs_keeps_paths_inside_root`` copre 1 livello
    (``nested/data.csv``). Qui valido che nesting arbitrario resta
    dentro root e produce un path ben-formato.

    Regressione: se qualcuno introducesse un max-depth check o una
    normalizzazione che collapse segmenti, il nesting canonico si
    romperebbe.
    """
    ds = _make_source(tmp_path)

    resolved = ds._abs("a/b/c/d/file.csv")
    resolved_path = Path(resolved)

    # DEVE essere sotto root
    root_abs = Path(str(tmp_path)).resolve()
    assert str(resolved_path).startswith(str(root_abs)), (
        f"resolved {resolved!r} NON sotto root {root_abs!r}"
    )
    # DEVE preservare i segmenti (non collapsare)
    parts_under_root = resolved_path.relative_to(root_abs).parts
    assert parts_under_root == ("a", "b", "c", "d", "file.csv"), (
        f"nesting non preservato, parts: {parts_under_root!r}"
    )


def test_abs_rejects_symlink_that_resolves_outside_root(tmp_path):
    """Blinda §1.6 fail-closed contro symlink escape.

    Scenario: root contiene un symlink ``evil_link → /outside_dir``.
    Un client chiama ``_abs("evil_link/secret.txt")``. Il contract:
    ``assert_path_within`` fa ``Path.resolve()`` (segue symlink),
    ``relative_to(root)`` fallisce → ``DataSourcePermissionError``.

    Regressione: sostituire ``resolve()`` con ``absolute()`` (che NON
    segue symlink) permetterebbe a un attaccante con write su root di
    piantare un symlink e leggere/scrivere qualunque path del filesystem.

    Test skippato se l'OS runner non permette la creazione di symlink
    (Windows senza developer mode / admin).
    """
    # Preparazione: directory "fuori" con file sensibile
    outside_dir = tmp_path.parent / f"outside_{tmp_path.name}"
    outside_dir.mkdir(exist_ok=True)
    (outside_dir / "secret.txt").write_text("sensitive")

    root = tmp_path / "ds_root"
    root.mkdir()

    evil_link = root / "evil_link"
    try:
        os.symlink(str(outside_dir), str(evil_link), target_is_directory=True)
    except (OSError, NotImplementedError) as e:
        pytest.skip(
            f"Symlink non supportato su questo runner (serve dev-mode/admin): {e}"
        )

    ds = _make_source(root)

    with pytest.raises(DataSourcePermissionError, match="traversal"):
        ds._abs("evil_link/secret.txt")


def test_rel_normalizes_separators_to_forward_slash(tmp_path):
    """Blinda portability Windows↔Linux: ``_rel`` ritorna sempre path
    con forward slash, indipendentemente dall'OS.

    I path relativi tornano al frontend (UI file browser) e alle API
    REST come parte di ``FileInfo.path``. Frontend JS normalizza su
    ``/`` → se il backend Windows emettesse ``nested\\data.csv``, la UI
    mostrerebbe backslash letterale nei breadcrumb. Bug estetico ma
    visibile.

    Implementazione: ``os.path.relpath().replace(os.sep, "/")``.
    Regressione se qualcuno rimuove il ``replace``.
    """
    ds = _make_source(tmp_path)

    # Costruisce un path nested reale sotto root usando os.sep per simulare
    # il come verrebbe emesso da os.scandir/os.walk
    nested_abs = os.path.join(str(tmp_path), "a", "b", "file.csv")

    rel = ds._rel(nested_abs)

    assert "\\" not in rel, (
        f"_rel DEVE normalizzare separatori: backslash non consentiti in "
        f"output, ottenuto {rel!r}"
    )
    assert rel == "a/b/file.csv", f"atteso 'a/b/file.csv', ottenuto {rel!r}"


def test_file_exists_returns_false_for_directories(tmp_path):
    """Blinda il contract semantico: ``file_exists`` ritorna True SOLO
    per file regolari, False per directory (usa ``os.path.isfile``).

    Regressione: un refactor che usasse ``os.path.exists`` restituirebbe
    True anche per directory, che è semanticamente sbagliato — l'API
    pubblica si chiama ``file_exists``, non ``path_exists``. Il caller
    che fa ``if ds.file_exists(p): ds.read_file(p)`` crasherebbe su
    ``read_file`` di una directory.
    """
    ds = _make_source(tmp_path)

    # Prepara: file reale + subdirectory
    (tmp_path / "real_file.txt").write_text("hello")
    (tmp_path / "subdir").mkdir()

    assert ds.file_exists("real_file.txt") is True
    assert ds.file_exists("subdir") is False, (
        "file_exists su directory DEVE ritornare False (os.path.isfile, "
        "non os.path.exists)"
    )
    assert ds.file_exists("nonexistent.txt") is False


def test_write_file_creates_missing_parent_directories(tmp_path):
    """Blinda il contract ``write_file``: crea automaticamente parent
    directory mancanti (``os.makedirs(exist_ok=True)``).

    Caso d'uso: upload di un file in una subdirectory nuova (``nuovo/
    sotto/report.csv``) senza che il client abbia pre-creato la
    subdirectory. Essenziale per UX upload da frontend.

    Regressione: rimuovere il makedirs (o passare ``exist_ok=False``)
    farebbe fallire upload su directory mai toccate, rompendo pipeline
    che scrivono output in struttura incrementale.
    """
    ds = _make_source(tmp_path)

    rel_path = "nuovo/sotto/report.csv"
    content = b"hello world"

    ds.write_file(rel_path, content)

    expected_file = tmp_path / "nuovo" / "sotto" / "report.csv"
    assert expected_file.exists(), (
        f"write_file DEVE creare parent dir mancanti. File atteso: "
        f"{expected_file}"
    )
    assert expected_file.read_bytes() == content
