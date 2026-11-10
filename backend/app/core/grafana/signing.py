"""Gettoni firmati della dashboard Grafana: lasciapassare e sessione.

Formato: ``<payload base64url>.<firma base64url>``; firma HMAC-SHA256 con una
chiave DERIVATA da ``DASHBOARD_PASS_SECRET`` per dominio (``pass`` /
``session``) e ``typ`` nel payload. Regole:

- la chiave non e' mai ``JWT_SECRET_KEY``: il JWT di login non ha ``aud`` ne'
  ``typ``, un gettone firmato con la stessa chiave varrebbe come login;
- un lasciapassare non vale come sessione e viceversa (chiave e ``typ``
  diversi); un JWT di login non vale come nessuno dei due (formato a tre
  parti, firma con altra chiave);
- senza segreto configurato non si emette e non si accetta nulla
  (``SigningUnavailable``).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Mapping, Optional

from . import settings as grafana_settings

PASS_TYPE = "plenora-dashboard-pass"
SESSION_TYPE = "plenora-grafana-session"
_DOMAIN_LABELS = {PASS_TYPE: b"plenora/dashboard-pass/v1", SESSION_TYPE: b"plenora/grafana-session/v1"}
MAX_TOKEN_LENGTH = 2048


class SigningUnavailable(RuntimeError):
    """``DASHBOARD_PASS_SECRET`` non configurato."""


class InvalidToken(ValueError):
    """Gettone assente, malformato, con firma errata, di altro tipo o scaduto."""


@dataclass(frozen=True)
class DashboardPass:
    task_id: str
    eseguibile_id: str
    user_id: str
    expires_at: int


@dataclass(frozen=True)
class GrafanaSession:
    user_id: str
    expires_at: int


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _key(token_type: str) -> bytes:
    secret = grafana_settings.pass_secret()
    if not secret:
        raise SigningUnavailable("DASHBOARD_PASS_SECRET non configurato")
    return hmac.new(secret.encode("utf-8"), _DOMAIN_LABELS[token_type], hashlib.sha256).digest()


def _sign(token_type: str, payload: Mapping[str, object]) -> str:
    body = _b64(json.dumps({"typ": token_type, **payload}, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    signature = _b64(hmac.new(_key(token_type), body.encode("ascii"), hashlib.sha256).digest())
    return f"{body}.{signature}"


def _verify(token_type: str, token: Optional[str], *, now: Optional[float] = None) -> Mapping[str, object]:
    if not token or not isinstance(token, str) or len(token) > MAX_TOKEN_LENGTH:
        raise InvalidToken("assente")
    parts = token.split(".")
    if len(parts) != 2 or not all(parts):
        raise InvalidToken("malformato")
    body, signature = parts
    expected = _b64(hmac.new(_key(token_type), body.encode("ascii"), hashlib.sha256).digest())
    if not hmac.compare_digest(expected, signature):
        raise InvalidToken("firma")
    try:
        data = json.loads(_unb64(body))
    except (ValueError, UnicodeDecodeError) as exc:
        raise InvalidToken("malformato") from exc
    if not isinstance(data, dict) or data.get("typ") != token_type:
        raise InvalidToken("tipo")
    expires_at = data.get("exp")
    if not isinstance(expires_at, int) or expires_at <= (time.time() if now is None else now):
        raise InvalidToken("scaduto")
    return data


def issue_pass(*, task_id: str, eseguibile_id: str, user_id: str, expires_at: int) -> str:
    return _sign(PASS_TYPE, {"t": task_id, "e": eseguibile_id, "u": user_id, "exp": int(expires_at)})


def verify_pass(token: Optional[str], *, now: Optional[float] = None) -> DashboardPass:
    data = _verify(PASS_TYPE, token, now=now)
    fields = (data.get("t"), data.get("e"), data.get("u"))
    if not all(isinstance(value, str) and value for value in fields):
        raise InvalidToken("malformato")
    return DashboardPass(
        task_id=str(data["t"]), eseguibile_id=str(data["e"]), user_id=str(data["u"]),
        expires_at=int(data["exp"]),
    )


def issue_session(*, user_id: str, expires_at: int) -> str:
    return _sign(SESSION_TYPE, {"u": user_id, "exp": int(expires_at)})


def verify_session(token: Optional[str], *, now: Optional[float] = None) -> GrafanaSession:
    data = _verify(SESSION_TYPE, token, now=now)
    user_id = data.get("u")
    if not isinstance(user_id, str) or not user_id:
        raise InvalidToken("malformato")
    return GrafanaSession(user_id=user_id, expires_at=int(data["exp"]))


__all__ = [
    "DashboardPass",
    "GrafanaSession",
    "InvalidToken",
    "PASS_TYPE",
    "SESSION_TYPE",
    "SigningUnavailable",
    "issue_pass",
    "issue_session",
    "verify_pass",
    "verify_session",
]
