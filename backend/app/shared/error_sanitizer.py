"""
Sanitizer per messaggi di errore esposti al client HTTP.

6.2 (error-handling-governance, Hardening).
"""

from __future__ import annotations

import re


_DEFAULT_MAX_LEN = 500
_TRUNCATE_SUFFIX = "...[troncato]"
_FALLBACK_EMPTY = "Errore non specificato"

# URL completi di qualsiasi schema usato dal backend (ArcGIS, upstream REST
# SFTP, ecc.). Il ``\S+`` greedy cattura anche query string con token
_URL_RE = re.compile(
    r"\b(?:https?|ftps?|sftp|ssh)://\S+",
    flags=re.IGNORECASE,
)

# JWT standalone: 3 sezioni base64url separate da ``.``. Il prefisso ``eyJ``
# e' l'header Base64 di ``{"`` (inizio JSON) — marker stabile per JWT
_JWT_RE = re.compile(
    r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b",
)

# Coppia ``<key><sep><value>`` dove key e' in blacklist e sep e' ``=`` o ``:``
# Cattura ``password=abc``, ``token: xyz``, ``Api-Key = foo``, ecc
_SENSITIVE_KV_RE = re.compile(
    r"(?P<key>token|api[_-]?key|password|secret|auth|bearer|client[_-]?secret)"
    r"(?P<sep>\s*[=:]\s*)"
    r"(?P<val>\S+)",
    flags=re.IGNORECASE,
)

# Path Windows: drive letter + ``:\\`` + almeno un char non-whitespace
_WIN_PATH_RE = re.compile(
    r"\b[A-Za-z]:\\[^\s'\"]+",
)

# Path POSIX assoluti in directory "sensibili" per un deploy Linux/Mac
# ``/projects/x`` NON matcha (nessuna directory sensibile). ``/home/...``
# e simili si'. Match-case: senza IGNORECASE per evitare falsi positivi su
# testo in italiano
_POSIX_PATH_RE = re.compile(
    r"/(?:home|Users|usr|var|tmp|etc|opt|root|mnt)/[^\s'\"]*",
)


def sanitize_http_detail(
    message: str | None,
    max_len: int = _DEFAULT_MAX_LEN,
) -> str:
    """Sanitizza ``message`` per l'esposizione via ``HTTPException.detail``.

    L'ordine di applicazione e' normativo: URL prima delle key=value
    (un URL con ``?token=...`` viene stripato intero al passo 1); JWT
    prima del truncate per non spezzare il marker a meta'. Le regole
    sono chiuse — ne' aggiunte ne' rimozioni senza ratifica.

    Input:
        message: stringa da sanitizzare, o ``None``.
        max_len: lunghezza massima dell'output (default 500).

    Output:
        Stringa sempre non vuota. Se ``message`` e' ``None`` o stringa
        vuota, ritorna ``"Errore non specificato"``.
    """
    if message is None:
        return _FALLBACK_EMPTY
    text = str(message)
    if not text:
        return _FALLBACK_EMPTY

    # 1. URL completi (mangia eventuali token in query string
    text = _URL_RE.sub("[URL]", text)
    # 2. JWT standalone
    text = _JWT_RE.sub("[REDACTED]", text)
    # 3. Key=value sensibili (preserva la chiave, redige il valore
    text = _SENSITIVE_KV_RE.sub(
        lambda m: f"{m.group('key')}{m.group('sep')}[REDACTED]",
        text,
    )
    # 4. Path Windows
    text = _WIN_PATH_RE.sub("[PATH]", text)
    # 5. Path POSIX sensibili
    text = _POSIX_PATH_RE.sub("[PATH]", text)

    # 6. Truncate dopo sanitization per non spezzare i marker a meta'
    if len(text) > max_len:
        cut = max(0, max_len - len(_TRUNCATE_SUFFIX))
        text = text[:cut] + _TRUNCATE_SUFFIX

    return text
