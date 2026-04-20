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

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException


pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.contract]


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
    from app.core.auth import auth as auth_mod

    monkeypatch.setattr(auth_mod.cfg_db, "get_group", lambda gid: {"id": gid, "name": "g"})

    boom = RuntimeError("simulated cascade failure")

    def _raising_cascade(gid):
        raise boom

    monkeypatch.setattr(auth_mod.cfg_db, "delete_group_cascade", _raising_cascade)

    spy_logger = MagicMock()
    monkeypatch.setattr(auth_mod, "logger", spy_logger)

    with pytest.raises(HTTPException) as excinfo:
        auth_mod.delete_group("some_group", _superadmin_user())

    assert excinfo.value.status_code == 500

    assert spy_logger.error.called, (
        "REGRESSIONE S5: delete_group_cascade ha sollevato ma il logger.error "
        "non e' stato chiamato → silent swallow, diagnostica lato server persa."
    )
    call_args, call_kwargs = spy_logger.error.call_args
    assert call_kwargs.get("exc_info") is True, (
        "REGRESSIONE S5: logger.error chiamato senza exc_info=True, lo stack "
        "trace dell'eccezione originale non viene registrato."
    )
    logged_msg = call_args[0] if call_args else ""
    assert "some_group" in logged_msg, (
        "REGRESSIONE S5: il messaggio di log non include il group_id → "
        "difficile correlare il crash a una specifica operazione."
    )
