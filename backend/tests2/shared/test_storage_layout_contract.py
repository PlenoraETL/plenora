"""
Contract test per `app.shared.resources.storage_layout`.

Verifica che:
"""

from __future__ import annotations

import pytest

from app.shared.resources import storage_layout


# Naming rules

def test_all_prefixes_are_non_empty_strings():
    for prefix in storage_layout.ALL_PREFIXES:
        assert isinstance(prefix, str), prefix
        assert prefix, "prefix vuoto non ammesso"


def test_all_prefixes_pass_is_valid_prefix():
    for prefix in storage_layout.ALL_PREFIXES:
        assert storage_layout.is_valid_prefix(prefix), (
            f"prefix {prefix!r} non rispetta le regole di naming"
        )


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "Results",       # uppercase
        "results/",      # slash
        "results name",  # space
        "results_x",     # underscore
        "-results",      # leading dash
        "results-",      # trailing dash
        "ré",            # non-ASCII
    ],
)
def test_is_valid_prefix_rejects_invalid_values(bad: str):
    assert not storage_layout.is_valid_prefix(bad)


# Uniqueness + non-prefix-of-another

def test_all_prefixes_are_unique():
    assert len(storage_layout.ALL_PREFIXES) == len(set(storage_layout.ALL_PREFIXES)), (
        f"duplicati in ALL_PREFIXES: {storage_layout.ALL_PREFIXES}"
    )


def test_no_prefix_is_prefix_of_another():
    """Garantisce che `list_objects("<X>/")` non raccolga oggetti di un
    altro dominio che inizia con la stessa stringa."""
    prefixes = list(storage_layout.ALL_PREFIXES)
    for i, a in enumerate(prefixes):
        for b in prefixes[i + 1:]:
            # Confronto simmetrico: nessuno deve essere prefisso dell'altro
            # con un boundary `/` o `-` immediatamente dopo (un nome che
            # NON e' realmente un prefisso di matching e' safe).
            assert not (a + "/").startswith(b + "/"), (
                f"{a!r} sovrascrive {b!r}"
            )
            assert not (b + "/").startswith(a + "/"), (
                f"{b!r} sovrascrive {a!r}"
            )


def test_all_prefixes_matches_declared_constants():
    """Zero drift fra le constants dichiarate e ALL_PREFIXES."""
    declared = {
        storage_layout.RESULTS_PREFIX,
        storage_layout.FILE_SOURCES_PREFIX,
        storage_layout.INTERMEDIATE_PREFIX,
        storage_layout.IMDF_BUNDLES_PREFIX,
        storage_layout.ESEGUIBILI_OUTPUTS_PREFIX,
    }
    assert declared == set(storage_layout.ALL_PREFIXES)


# Call site usage — i moduli storage usano gli alias dichiarati qui

def test_file_storage_dispatch_uses_shared_prefix():
    from app.core.files import storage_dispatch
    assert storage_dispatch._OBJECT_STORAGE_PREFIX == storage_layout.FILE_SOURCES_PREFIX


def test_imdf_storage_uses_shared_prefix():
    from app.core.imdf import storage as imdf_storage
    assert imdf_storage.PREFIX == storage_layout.IMDF_BUNDLES_PREFIX


def test_eseguibili_storage_uses_shared_prefix():
    from app.core.eseguibili import storage as eseguibili_storage
    assert eseguibili_storage.PREFIX == storage_layout.ESEGUIBILI_OUTPUTS_PREFIX
