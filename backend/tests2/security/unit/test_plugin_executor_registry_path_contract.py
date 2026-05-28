"""
Difesa in profondità: `PluginExecutor(registry_path=...)` deve

rifiutare qualunque path che non sia dentro `app/core/manipola/`.
"""

from pathlib import Path

import pytest

from app.core.manipola.plugin_executor import PluginExecutor


pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.contract, pytest.mark.regression]


_MANIPOLA_ROOT = Path(__file__).resolve().parents[3] / "app" / "core" / "manipola"


def test_default_registry_path_loads():
    """Caso di default: nessun arg → usa il registry del modulo."""
    executor = PluginExecutor()

    assert executor.registry_path == _MANIPOLA_ROOT / "transforms_registry.json"
    assert len(executor.registry) > 0  # il registry reale non è vuoto


def test_explicit_registry_within_module_is_accepted(tmp_path, monkeypatch):
    """Un path esplicito che risolve dentro app/core/manipola/ e' accettato."""
    # Creiamo un registry minimale dentro il perimetro del modulo.
    fake_registry = _MANIPOLA_ROOT / "_test_registry.json"
    fake_registry.write_text('{"transforms": {}, "categories": {}}')

    try:
        executor = PluginExecutor(registry_path=str(fake_registry))
        assert executor.registry == {}
    finally:
        fake_registry.unlink(missing_ok=True)


def test_registry_path_outside_manipola_is_rejected(tmp_path):
    """Un path esplicito fuori dal perimetro DEVE sollevare ValueError."""
    evil_registry = tmp_path / "evil_registry.json"
    evil_registry.write_text('{"transforms": {"pwn": {"function": "joins.op_join"}}}')

    with pytest.raises(ValueError, match="fuori dal perimetro"):
        PluginExecutor(registry_path=str(evil_registry))


def test_registry_path_traversal_is_rejected(tmp_path):
    """Un path con `..` che risolve fuori dal perimetro DEVE sollevare."""
    traversal_path = str(_MANIPOLA_ROOT / ".." / ".." / ".." / "etc" / "passwd")

    with pytest.raises(ValueError, match="fuori dal perimetro"):
        PluginExecutor(registry_path=traversal_path)


def test_rejection_happens_before_file_read(tmp_path):
    """Il guard deve scattare PRIMA di aprire il file — il percorso
    fuori perimetro non deve essere nemmeno aperto (evita timing
    oracle o side-effect da file speciali tipo /dev/random)."""
    # Path a un file che non esiste fuori dal perimetro: se il guard
    # scatta prima, solleva ValueError. Se scattasse dopo, solleverebbe
    # FileNotFoundError (l'eccezione diversa segnala l'ordine sbagliato).
    nonexistent_outside = tmp_path / "does_not_exist.json"

    with pytest.raises(ValueError, match="fuori dal perimetro"):
        PluginExecutor(registry_path=str(nonexistent_outside))


def test_no_legacy_plugin_dispatch_compatibility_layer():
    """Manipola dispatches directly through PluginExecutor."""
    import app.core.manipola.plugin_executor as plugin_mod

    assert not hasattr(plugin_mod, "PluginExecutorCompatibility")
