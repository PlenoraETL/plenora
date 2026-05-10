"""
Contract test R-25 mode-resolution contract — resolve_intermediate_mode.

`resolve_intermediate_mode(default_mode, override_mode=None)` e' una pure
"""

from __future__ import annotations

import pytest

from app.core.intermediate.policy import (
    MODE_FULL_ARTIFACT,
    MODE_FULL_PG,
    MODE_METADATA_ONLY,
    MODE_PREVIEW_ONLY,
    IntermediateSettingsError,
    SUPPORTED_PERSISTENCE_MODES,
    resolve_intermediate_mode,
)


pytestmark = [pytest.mark.unit, pytest.mark.intermediate, pytest.mark.contract]


# 1. Override None -> ritorna default (bit-compat)

@pytest.mark.parametrize(
    "default_mode",
    [MODE_METADATA_ONLY, MODE_PREVIEW_ONLY, MODE_FULL_PG, MODE_FULL_ARTIFACT],
)
def test_override_none_returns_default(default_mode):
    """Override None deve essere bit-compatible col default-path pre-PR.

    Caller esistenti (pre-propagation contract) chiamano senza override_mode; il
    risultato deve essere identico al default globale, altrimenti la PR
    introduce regression sul path produzione.
    """
    assert resolve_intermediate_mode(default_mode) == default_mode
    assert resolve_intermediate_mode(default_mode, None) == default_mode
    assert resolve_intermediate_mode(default_mode, override_mode=None) == default_mode


# 2-3. Override valido sovrascrive il default

@pytest.mark.parametrize(
    "default_mode,override_mode,expected",
    [
        (MODE_METADATA_ONLY, MODE_PREVIEW_ONLY, MODE_PREVIEW_ONLY),
        (MODE_METADATA_ONLY, MODE_FULL_PG, MODE_FULL_PG),
        (MODE_METADATA_ONLY, MODE_FULL_ARTIFACT, MODE_FULL_ARTIFACT),
        (MODE_PREVIEW_ONLY, MODE_METADATA_ONLY, MODE_METADATA_ONLY),
        (MODE_PREVIEW_ONLY, MODE_FULL_PG, MODE_FULL_PG),
        (MODE_FULL_PG, MODE_METADATA_ONLY, MODE_METADATA_ONLY),
        (MODE_FULL_ARTIFACT, MODE_METADATA_ONLY, MODE_METADATA_ONLY),
        # Idempotenza: override == default ritorna default invariato.
        (MODE_METADATA_ONLY, MODE_METADATA_ONLY, MODE_METADATA_ONLY),
        (MODE_FULL_PG, MODE_FULL_PG, MODE_FULL_PG),
    ],
)
def test_override_valid_sovrascrive_default(default_mode, override_mode, expected):
    assert resolve_intermediate_mode(default_mode, override_mode) == expected


# 4. Override invalido -> raise IntermediateSettingsError (fail-loud)

def test_override_stringa_vuota_solleva():
    with pytest.raises(IntermediateSettingsError) as exc_info:
        resolve_intermediate_mode(MODE_METADATA_ONLY, "")
    assert "''" in str(exc_info.value) or "empty" in str(exc_info.value).lower() \
        or "non riconosciuto" in str(exc_info.value).lower()


def test_override_stringa_sconosciuta_solleva():
    with pytest.raises(IntermediateSettingsError) as exc_info:
        resolve_intermediate_mode(MODE_METADATA_ONLY, "INVALID_MODE")
    msg = str(exc_info.value)
    # Il messaggio deve aiutare a debug: contiene il valore offending
    # E la lista dei mode ammessi.
    assert "INVALID_MODE" in msg
    assert "metadata_only" in msg  # parte della lista valori ammessi


def test_override_case_mismatch_solleva():
    """Validazione case-sensitive: 'FULL_PG' != 'full_pg'."""
    with pytest.raises(IntermediateSettingsError):
        resolve_intermediate_mode(MODE_METADATA_ONLY, "FULL_PG")
    with pytest.raises(IntermediateSettingsError):
        resolve_intermediate_mode(MODE_METADATA_ONLY, "Metadata_Only")


@pytest.mark.parametrize(
    "bad_override",
    [
        42,                     # int
        3.14,                   # float
        ["full_pg"],            # list
        ("full_pg",),           # tuple
        {"mode": "full_pg"},    # dict
        b"full_pg",             # bytes
        True,                   # bool (escluso esplicitamente)
        False,                  # bool (escluso esplicitamente)
        object(),               # arbitrary object
    ],
)
def test_override_tipo_non_str_solleva(bad_override):
    """Tipo non-str non puo' silently coerce: fail-loud sempre."""
    with pytest.raises(IntermediateSettingsError) as exc_info:
        resolve_intermediate_mode(MODE_METADATA_ONLY, bad_override)
    msg = str(exc_info.value)
    # Messaggio deve indicare il tipo errato (debug-friendly).
    assert "type=" in msg or "str" in msg.lower()


# 5. Override in PLANNED_PERSISTENCE_MODES -> raise

def test_override_planned_mode_solleva(monkeypatch):
    """Mode pianificate ma non implementate devono fail-loud."""
    from app.core.intermediate import policy as _policy

    fake_planned = frozenset({"future_mode_v2"})
    fake_supported = _policy.SUPPORTED_PERSISTENCE_MODES | fake_planned
    monkeypatch.setattr(_policy, "PLANNED_PERSISTENCE_MODES", fake_planned)
    monkeypatch.setattr(_policy, "SUPPORTED_PERSISTENCE_MODES", fake_supported)

    with pytest.raises(IntermediateSettingsError) as exc_info:
        _policy.resolve_intermediate_mode(MODE_METADATA_ONLY, "future_mode_v2")
    msg = str(exc_info.value)
    assert "future_mode_v2" in msg
    assert "non e' ancora" in msg or "implement" in msg.lower()


# 6. Capability runtime NON verificata (full_artifact senza object storage
#    NON solleva qui — quella check resta startup-side)

def test_full_artifact_override_non_verifica_capability(monkeypatch):
    """`full_artifact` come override deve passare l'enum-check anche se
    OBJECT_STORAGE_ENABLED=false. La capability check vive in
    `validate_intermediate_settings`, non in `resolve_intermediate_mode`.

    Razionale: separation of concerns. Questa fn valida l'enum;
    settings.validate fa il cross-check infrastrutturale; lo writer
    fail-closed al record-time se la capability manca.
    """
    # Patch di object_storage_enabled() per assicurare che NON venga
    # nemmeno chiamato (questa fn deve essere puramente sintattica).
    import app.infrastructure.storage.cloud_backends as _cb

    called = {"flag": False}

    def fake_capability():
        called["flag"] = True
        return False

    monkeypatch.setattr(_cb, "object_storage_enabled", fake_capability)

    # Non deve sollevare e non deve invocare object_storage_enabled.
    result = resolve_intermediate_mode(MODE_METADATA_ONLY, MODE_FULL_ARTIFACT)
    assert result == MODE_FULL_ARTIFACT
    assert called["flag"] is False, (
        "resolve_intermediate_mode ha invocato object_storage_enabled: "
        "sta facendo capability check oltre lo scope dell'enum-validator."
    )


# 7. default_mode invalido non e' validato dalla fn (caller responsability)

def test_default_mode_invalido_passa_se_override_none():
    """resolve_intermediate_mode NON valida default_mode quando override
    e' None: e' caller responsability (gia' validato startup-side da
    validate_intermediate_settings).

    Test serve a documentare il contratto: la fn non e' un secondo
    validator nascosto, e' un selettore enum-based.
    """
    # Stringa fuori dall'enum, ma fn accetta perche' nessun override
    assert resolve_intermediate_mode("not_a_real_mode") == "not_a_real_mode"
    assert resolve_intermediate_mode("not_a_real_mode", None) == "not_a_real_mode"


# 8. Coverage smoke: ogni mode supportato e' raggiungibile come override

def test_ogni_mode_supportato_e_overridable_da_metadata_only():
    """Sanity: il default produzione (metadata_only) puo' essere
    overridato in qualunque mode supportato senza errori."""
    for mode in SUPPORTED_PERSISTENCE_MODES:
        assert resolve_intermediate_mode(MODE_METADATA_ONLY, mode) == mode
