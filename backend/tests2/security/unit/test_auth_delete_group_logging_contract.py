"""Contract test S5 — delete_group route non deve silenziare il cascade.

Scope S5 (inventory_report.md Sez. 5.1):
  `backend/app/core/auth/auth.py:989-991` catturava `Exception as e` ma
  scartava `e` (no log, no chain) prima di sollevare un HTTPException 500
  generico. Questo e' "silent swallow" nel senso charter base 9: la
  diagnostica lato server spariva, e la policy fail-fast del modulo
  `app/core/auth/` (routes con HTTPException + log `[CRASH]` nei peer
  come arricchisci/router.py:499) veniva violata localmente.

Il fix minimo aggiunge `logger.error(..., exc_info=True)` prima del
raise. Questo test fissa quel contratto: se `delete_group_cascade`
solleva, il logger del modulo auth deve registrare l'errore con
stack trace prima che il client riceva il 500.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest
from fastapi import HTTPException


pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.contract]


# PR #34 P1-B: pattern B2.0 — handler stdlib agganciato al logger
# reale invece di monkeypatch del symbol module-level con MagicMock.
# `StructuredLogger` ha propagate=False, caplog non funziona out-of-
# the-box: usiamo handler custom (clone-da test_n4_formula_validator).
class _ListHandler(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.records = []

    def emit(self, record):
        self.records.append(record)


# BE-GO-READY-AUTH-ENGINE-004: la logica di `delete_group` (incluso
# `logger.error("[CRASH] delete_group_cascade ...")`) e' stata
# spostata da `app/core/auth/auth.py` a `app/core/auth/groups_router.py`.
# Il record ERROR e' ora emesso dal logger del modulo nuovo.
_AUTH_LOGGER = "app.core.auth.groups_router"


def _superadmin_user():
    return SimpleNamespace(
        user_id="root", username="root", group_id="", role="superadmin",
    )


def test_delete_group_cascade_failure_is_logged_then_raised_as_500(monkeypatch):
    """S5: quando delete_group_cascade solleva, il route handler DEVE
    loggare l'errore (con exc_info) e poi sollevare HTTPException 500.
    Il fallback pre-fix catturava `Exception as e` ma non usava mai `e`:
    il log diagnostico non veniva emesso → regressione di osservabilita'.
    """
    # BE-GO-READY-AUTH-ENGINE-021-USERS-GROUPS-REEXPORT-REMOVAL:
    # `delete_group` is no longer re-exported from `app.core.auth.auth`.
    # Call it via the canonical leaf module `app.core.auth.groups_router`
    # (where the handler actually lives since AUTH-ENGINE-004 extraction).
    #
    # BE-GO-READY-DB-ENGINE-003-GROUPS-CALLSITE-MIGRATION: groups_router
    # now binds the leaf as `groups_mod._groups`; patch that symbol
    # rather than the legacy `cfg_db` facade so the test exercises the
    # actual call path.
    from app.core.auth import groups_router as groups_mod

    monkeypatch.setattr(
        groups_mod._groups,
        "get_group",
        lambda gid: {"id": gid, "name": "g"},
    )

    boom = RuntimeError("simulated cascade failure")

    def _raising_cascade(gid):
        raise boom

    monkeypatch.setattr(groups_mod._groups, "delete_group_cascade", _raising_cascade)

    target_logger = logging.getLogger(_AUTH_LOGGER)
    handler = _ListHandler()
    target_logger.addHandler(handler)
    try:
        with pytest.raises(HTTPException) as excinfo:
            groups_mod.delete_group("some_group", _superadmin_user())
    finally:
        target_logger.removeHandler(handler)

    assert excinfo.value.status_code == 500

    error_records = [r for r in handler.records if r.levelno == logging.ERROR]
    assert error_records, (
        "REGRESSIONE S5: delete_group_cascade ha sollevato ma nessun record "
        "ERROR e' stato emesso → silent swallow, diagnostica lato server persa."
    )

    # Almeno un record ERROR deve avere exc_info popolato (= logger.error fu
    # chiamato con exc_info=True, oppure dentro un except con exception()).
    error_with_exc = [r for r in error_records if r.exc_info is not None]
    assert error_with_exc, (
        "REGRESSIONE S5: logger.error chiamato senza exc_info, lo stack "
        "trace dell'eccezione originale non viene registrato."
    )

    # Il messaggio di log deve menzionare il group_id per correlazione
    assert any("some_group" in r.getMessage() for r in error_records), (
        "REGRESSIONE S5: il messaggio di log non include il group_id → "
        "difficile correlare il crash a una specifica operazione."
    )
