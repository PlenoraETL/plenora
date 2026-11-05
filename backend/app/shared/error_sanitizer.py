"""
Sanitizer per messaggi di errore esposti al client HTTP o salvati (esito del
«Test connessione», ``app/shared/connection_tests/results.py``).

6.2 (error-handling-governance, Hardening).

Prima regola, sempre e in entrambi i livelli: via i caratteri di controllo
(tranne ``\\t`` e ``\\n``) e i surrogati isolati (``strip_control_chars``).

Due livelli, da applicare in quest'ordine:

1. ``redact_known_secrets(text, secrets)``: sostituzione ESATTA dei valori
   segreti noti della risorsa (``collect_secret_values``) in ogni forma della
   tabella ``SECRET_VARIANTS`` (percent-encoding maiuscolo e minuscolo,
   escape JSON/repr, Base64, escape HTML/XML; Base64 di ``utente:password``
   per Basic auth da ``collect_secret_values``). Copre qualunque forma in
   cui il segreto compaia, anche dove il livello 2 non riconosce il
   contesto (corpo HTML, testo libero). Segreti di una risorsa: i valori dei
   campi e dei parametri (dict ``nome: valore`` o lista ``{name, value |
   default_value}``) il cui nome e' sensibile per ``SENSITIVE_NAME``.
2. ``sanitize_http_detail(text)``: regole generiche, senza conoscere i
   segreti. URL con schema -> ``[URL]``; qualunque query string (anche di un
   percorso senza schema, ``url: /v1/data?key=...``) -> ``?[REDACTED]``;
   credenziali in ``utente:password@host`` e ``utente/password@host``
   (Oracle); coppie ``nome=valore``, ``nome: valore``, ``"nome": "valore"``
   e ``nome='valore con spazi'`` con nome sensibile (``SENSITIVE_NAME``);
   ``PWD={...}`` ODBC; ``Bearer``/``Basic`` + credenziale; JWT; percorsi.
"""

from __future__ import annotations

import base64
import html
import json
import re
from dataclasses import asdict, is_dataclass
from typing import Any, Callable, Iterable
from urllib.parse import quote, quote_plus
from xml.sax.saxutils import escape as xml_escape


_DEFAULT_MAX_LEN = 500
_TRUNCATE_SUFFIX = "...[troncato]"
_FALLBACK_EMPTY = "Errore non specificato"
REDACTED = "[REDACTED]"

# L'UNICA lista dei nomi sensibili, per i due livelli: chiave di query,
# header, campo JSON, parametro di DSN nel messaggio (livello 2) e nome del
# campo o del parametro della risorsa il cui valore e' un segreto (livello 1,
# ``is_sensitive_name``). Un prefisso composto e' ammesso: ``x-api-key``,
# ``Ocp-Apim-Subscription-Key``, ``aws_secret_access_key``.
SENSITIVE_NAME = (
    r"(?:[A-Za-z0-9]+[-_.])*"
    r"(?:api[-_]?key|app[-_]?key|access[-_]?key|secret[-_]?key|subscription[-_]?key"
    r"|auth[-_]?key|private[-_]?key|key[-_]?value|key"
    r"|secret[-_]?access[-_]?key|private[-_]?key[-_]?passphrase"
    r"|access[-_]?token|refresh[-_]?token|id[-_]?token|auth[-_]?token|sas[-_]?token|token"
    r"|client[-_]?secret|secret|password|passwd|pwd|passphrase"
    r"|authorization|auth|bearer|sig|signature|appid|app[-_]?id"
    r"|credentials?|session[-_]?id|connection[-_]?string)"
)
_SENSITIVE_NAME_RE = re.compile(SENSITIVE_NAME, flags=re.IGNORECASE)
# Caratteri di controllo (tranne ``\t`` e ``\n``) e surrogati isolati: un
# byte NUL o un corpo binario non arrivano mai a PostgreSQL (DataError) ne'
# al client; tolti PRIMA di ogni altra regola, cosi' un segreto spezzato da
# un carattere di controllo torna riconoscibile.
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f\ud800-\udfff]")
_NAME = rf"(?<![A-Za-z0-9])(?P<key>{SENSITIVE_NAME})(?![A-Za-z0-9])"
# Un valore gia' oscurato da una regola precedente non si riprende.
_NOT_REDACTED = r"(?!\[REDACTED\])"
# Schema di autorizzazione che precede la credenziale (``auth: Basic abc``).
_AUTH_SCHEME = r"(?:(?:bearer|basic|digest|token|negotiate|ntlm|apikey|sharedkey)\s+)?"

# 1. URL con schema (http, sftp, postgresql, oracle, mongodb, redis, ...):
#    intero URL, quindi anche userinfo e query string.
_URL_RE = re.compile(r"\b[A-Za-z][A-Za-z0-9+.\-]{1,20}://\S+")

# 2. Query string di un percorso SENZA schema (messaggi di requests:
#    ``Max retries exceeded with url: /v1/data?key=...``).
_QUERY_RE = re.compile(r"\?(?=[^\s'\"<>]*=)[^\s'\"<>#)]+")

# 3. JWT standalone: 3 sezioni base64url, header ``eyJ`` (= ``{"``).
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")

# 4. Credenziali senza schema: ``utente:password@host`` e Oracle
#    ``utente/password@host:1521/servizio``.
_USERINFO_RE = re.compile(r"(?<![\w.%+\-:])(?P<user>[\w.%+\-]*):(?P<pw>[^\s:@/'\"]+)@(?=[\w\[])")
_ORACLE_RE = re.compile(r"(?<![\w/.:\-])(?P<user>[\w$#.\-]+)/(?P<pw>[^\s/@'\"]+)@(?=[\w\[(])")

# 5. ``"nome": "valore"`` / ``'nome': 'valore'`` (JSON, repr Python), anche
#    con apici escapati (JSON dentro una stringa).
_QUOTED_KEY_QUOTED_VAL_RE = re.compile(
    rf"(?P<q>\\?[\"'])(?P<key>{SENSITIVE_NAME})(?P=q)(?P<sep>\s*[:=]\s*)"
    r"(?P<vq>\\?[\"'])(?P<val>.*?)(?P=vq)",
    flags=re.IGNORECASE,
)
# 6. ``"nome": 12345`` (valore non tra apici).
_QUOTED_KEY_BARE_VAL_RE = re.compile(
    rf"(?P<q>\\?[\"'])(?P<key>{SENSITIVE_NAME})(?P=q)(?P<sep>\s*[:=]\s*)"
    rf"{_NOT_REDACTED}(?P<val>{_AUTH_SCHEME}[^\s,;}}\]\"'\\]+)",
    flags=re.IGNORECASE,
)
# 7. ``nome='valore con spazi'``.
_BARE_KEY_QUOTED_VAL_RE = re.compile(
    rf"{_NAME}(?P<sep>\s*[:=]\s*)(?P<vq>\\?[\"'])(?P<val>.*?)(?P=vq)",
    flags=re.IGNORECASE,
)
# 8. ODBC ``PWD={valore;con;separatori}`` (``}}`` e' l'escape di ``}``).
_BARE_KEY_BRACED_VAL_RE = re.compile(
    rf"{_NAME}(?P<sep>\s*=\s*)\{{(?:[^}}]|\}}\}})*\}}",
    flags=re.IGNORECASE,
)
# 9. Stringa di connessione ``Password=a b;`` (valore fino al ``;``).
_BARE_KEY_SEMICOLON_VAL_RE = re.compile(
    rf"{_NAME}(?P<sep>\s*=\s*){_NOT_REDACTED}(?P<val>[^;\r\n'\"]{{1,256}})(?=;)",
    flags=re.IGNORECASE,
)
# 10. ``nome=valore`` / ``nome: valore`` / ``Authorization: Bearer valore``.
_BARE_KEY_BARE_VAL_RE = re.compile(
    rf"{_NAME}(?P<sep>\s*[:=]\s*){_NOT_REDACTED}(?P<val>{_AUTH_SCHEME}[^\s&;,'\"<>)}}\]]+)",
    flags=re.IGNORECASE,
)
# 11. ``Bearer <credenziale>`` e ``Basic <base64>`` fuori da un header.
_BEARER_RE = re.compile(r"\b(?P<scheme>bearer)\s+[A-Za-z0-9\-._~+/]+=*", flags=re.IGNORECASE)
_BASIC_RE = re.compile(
    r"\b(?P<scheme>basic)\s+(?=[A-Za-z0-9+/]*[0-9+/=])[A-Za-z0-9+/]{8,}={0,2}",
    flags=re.IGNORECASE,
)

# Path Windows: drive letter + ``:\\`` + almeno un char non-whitespace
_WIN_PATH_RE = re.compile(r"\b[A-Za-z]:\\[^\s'\"]+")

# Path POSIX assoluti in directory "sensibili" per un deploy Linux/Mac
# ``/projects/x`` NON matcha (nessuna directory sensibile). Match-case:
# senza IGNORECASE per evitare falsi positivi su testo in italiano
_POSIX_PATH_RE = re.compile(r"/(?:home|Users|usr|var|tmp|etc|opt|root|mnt)/[^\s'\"]*")


def _keep_key(match: re.Match) -> str:
    groups = match.groupdict()
    q = groups.get("q") or ""
    vq = groups.get("vq") or ""
    return f"{q}{match.group('key')}{q}{match.group('sep')}{vq}{REDACTED}{vq}"


def strip_control_chars(text: str) -> str:
    """Prima regola di entrambi i livelli (``_CONTROL_CHARS_RE``)."""
    return _CONTROL_CHARS_RE.sub("", text)


def _sanitize_rules(text: str) -> str:
    text = strip_control_chars(text)
    text = _URL_RE.sub("[URL]", text)
    text = _QUERY_RE.sub(f"?{REDACTED}", text)
    text = _JWT_RE.sub(REDACTED, text)
    text = _USERINFO_RE.sub(lambda m: f"{m.group('user')}:{REDACTED}@", text)
    text = _ORACLE_RE.sub(lambda m: f"{m.group('user')}/{REDACTED}@", text)
    text = _QUOTED_KEY_QUOTED_VAL_RE.sub(_keep_key, text)
    text = _QUOTED_KEY_BARE_VAL_RE.sub(_keep_key, text)
    text = _BARE_KEY_QUOTED_VAL_RE.sub(_keep_key, text)
    text = _BARE_KEY_BRACED_VAL_RE.sub(_keep_key, text)
    text = _BARE_KEY_SEMICOLON_VAL_RE.sub(_keep_key, text)
    text = _BARE_KEY_BARE_VAL_RE.sub(_keep_key, text)
    text = _BEARER_RE.sub(lambda m: f"{m.group('scheme')} {REDACTED}", text)
    text = _BASIC_RE.sub(lambda m: f"{m.group('scheme')} {REDACTED}", text)
    text = _WIN_PATH_RE.sub("[PATH]", text)
    text = _POSIX_PATH_RE.sub("[PATH]", text)
    return text


def sanitize_http_detail(
    message: str | None,
    max_len: int = _DEFAULT_MAX_LEN,
) -> str:
    """Sanitizza ``message`` per l'esposizione via ``HTTPException.detail``.

    L'ordine delle regole e' normativo (``_sanitize_rules``): URL e query
    string prima delle coppie nome/valore, tutto prima del troncamento per
    non spezzare un segreto a meta' (la parte rimasta non sarebbe piu'
    riconoscibile).

    Output: stringa sempre non vuota (``"Errore non specificato"`` per
    ``None`` o stringa vuota), lunga al massimo ``max_len``.
    """
    if message is None:
        return _FALLBACK_EMPTY
    text = _sanitize_rules(str(message))
    if not text:
        return _FALLBACK_EMPTY

    if len(text) > max_len:
        cut = max(0, max_len - len(_TRUNCATE_SUFFIX))
        text = text[:cut] + _TRUNCATE_SUFFIX

    return text


# Segreti noti di una risorsa (livello 1)

# REGOLA (una sola lista dei nomi sensibili): il valore di un campo o di un
# parametro della risorsa e' un segreto se il suo nome e' sensibile per
# ``SENSITIVE_NAME`` (``is_sensitive_name``), in qualunque forma: campo
# ``nome: valore`` di un dict (config, ``static_parameters``, header) o
# elemento ``{name|key: <nome>, value|default_value: <valore>}`` di una lista
# (``parameters`` dei connettori API, query param, header).
_PARAM_NAME_FIELDS = frozenset({"name", "key", "param", "parameter", "header"})
_PARAM_VALUE_FIELDS = frozenset({"value", "defaultvalue", "default"})
# Campi ``value`` segreti anche senza nome sensibile dentro questi blocchi
# (auth generica dei connettori API: ``{"type": "custom", "value": ...}``).
_SECRET_VALUE_PARENTS = frozenset({"auth", "authentication", "credentials"})
# Campi utente/segreto che formano ``Basic base64(utente:segreto)``.
_BASIC_USER_FIELDS = frozenset({"username", "user", "uid", "login", "clientid"})
_BASIC_SECRET_FIELDS = frozenset({"password", "passwd", "pwd", "clientsecret"})
# Valori che non sono segreti (maschere, segnaposto) o troppo corti per una
# sostituzione esatta senza cancellare testo qualunque.
_NOT_SECRETS = frozenset({"***", "****", "********", "none", "null", "true", "false"})
_MIN_SECRET_LEN = 3
_MAX_DEPTH = 8
# ``clientSecret`` -> ``client_Secret``: i nomi camelCase dei modelli hanno
# lo stesso confine di parola dei nomi con separatore.
_CAMEL_BOUNDARY_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def _norm(name: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


def is_sensitive_name(name: Any) -> bool:
    """True se ``name`` (campo, parametro, header) e' un nome sensibile per
    ``SENSITIVE_NAME``, la stessa lista del livello 2."""
    if not isinstance(name, str) or not name:
        return False
    return _SENSITIVE_NAME_RE.fullmatch(_CAMEL_BOUNDARY_RE.sub("_", name)) is not None


def is_secret_field(name: Any, parent: Any = None) -> bool:
    """True se il campo ``name`` (dentro il blocco ``parent``) contiene un segreto."""
    if is_sensitive_name(name):
        return True
    return _norm(name) == "value" and _norm(parent or "") in _SECRET_VALUE_PARENTS


def _param_name(node: dict) -> Any:
    """Il nome del parametro se ``node`` e' un elemento ``{name, value}``."""
    fields = {_norm(k): v for k, v in node.items()}
    if not _PARAM_VALUE_FIELDS & fields.keys():
        return None
    for field in sorted(_PARAM_NAME_FIELDS):
        if isinstance(fields.get(field), str):
            return fields[field]
    return None


def _as_mapping(node: Any) -> Any:
    dump = getattr(node, "model_dump", None)
    if callable(dump):
        return dump()
    if is_dataclass(node) and not isinstance(node, type):
        return asdict(node)
    return node


def _usable(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value.strip()) >= _MIN_SECRET_LEN
        and value.strip().lower() not in _NOT_SECRETS
        and not value.startswith("enc:")
    )


def _is_secret_entry(name: Any, parent: Any, param_name: Any) -> bool:
    if param_name is None:
        return is_secret_field(name, parent)
    key = _norm(name)
    if key in _PARAM_NAME_FIELDS:
        # Il nome del parametro (``{"key": "apikey", ...}``) non e' un segreto.
        return False
    if key in _PARAM_VALUE_FIELDS:
        return is_sensitive_name(param_name) or is_secret_field(name, parent)
    return is_secret_field(name, parent)


def collect_secret_values(source: Any) -> frozenset[str]:
    """Tutti i valori segreti di una risorsa (config, modello Pydantic,
    dataclass), a qualunque profondita' (regola sopra), piu'
    ``base64(utente:segreto)`` di ogni blocco che ha sia l'utente sia il
    segreto (Basic auth)."""
    found: set[str] = set()

    def walk(node: Any, parent: Any, depth: int) -> None:
        if depth > _MAX_DEPTH:
            return
        node = _as_mapping(node)
        if isinstance(node, dict):
            param_name = _param_name(node)
            users = []
            secrets = []
            for name, value in node.items():
                key = _norm(name)
                if isinstance(value, str):
                    if _is_secret_entry(name, parent, param_name) and _usable(value):
                        found.add(value)
                    if key in _BASIC_USER_FIELDS:
                        users.append(value)
                    if key in _BASIC_SECRET_FIELDS and _usable(value):
                        secrets.append(value)
                else:
                    walk(value, name, depth + 1)
            for secret in secrets:
                for user in users or [""]:
                    found.add(base64.b64encode(f"{user}:{secret}".encode()).decode())
        elif isinstance(node, (list, tuple, set, frozenset)):
            for value in node:
                walk(value, parent, depth + 1)

    walk(source, None, 0)
    return frozenset(found)


_PERCENT_ESCAPE_RE = re.compile(r"%[0-9A-F]{2}")


def _lower_hex(encoded: str) -> str:
    return _PERCENT_ESCAPE_RE.sub(lambda m: m.group().lower(), encoded)


def _xml_escape(secret: str) -> str:
    return xml_escape(secret, {'"': "&quot;", "'": "&apos;"})


def _html_decimal(secret: str) -> str:
    return html.escape(secret, quote=False).replace('"', "&#34;").replace("'", "&#39;")


# REGOLA: le forme in cui un segreto noto puo' comparire in un messaggio sono
# QUESTA tabella (nome, codifica); ``redact_known_secrets`` le oscura tutte.
# ``base64(utente:segreto)`` della Basic auth e' a sua volta un segreto
# raccolto da ``collect_secret_values`` (e quindi ha le sue forme).
SECRET_VARIANTS: tuple[tuple[str, Callable[[str], str]], ...] = (
    ("raw", lambda s: s),
    ("strip", str.strip),
    ("percent", lambda s: quote(s, safe="")),
    ("percent_path", lambda s: quote(s)),
    ("percent_plus", quote_plus),
    ("percent_lower", lambda s: _lower_hex(quote(s, safe=""))),
    ("percent_path_lower", lambda s: _lower_hex(quote(s))),
    ("percent_plus_lower", lambda s: _lower_hex(quote_plus(s))),
    ("json", lambda s: json.dumps(s)[1:-1]),
    ("json_unicode", lambda s: json.dumps(s, ensure_ascii=False)[1:-1]),
    ("repr", lambda s: repr(s)[1:-1]),
    ("base64", lambda s: base64.b64encode(s.encode()).decode()),
    ("html", lambda s: html.escape(s, quote=True)),
    ("html_noquote", lambda s: html.escape(s, quote=False)),
    ("html_decimal", _html_decimal),
    ("xml", _xml_escape),
)


def secret_forms(secret: str) -> set[str]:
    """Le forme di ``secret`` (``SECRET_VARIANTS``) abbastanza lunghe da
    sostituire senza cancellare testo qualunque."""
    clean = strip_control_chars(secret)
    out = {encode(clean) for _, encode in SECRET_VARIANTS}
    return {v for v in out if len(v) >= _MIN_SECRET_LEN}


def redact_known_secrets(text: str, secrets: Iterable[str]) -> str:
    """Sostituisce con ``[REDACTED]`` ogni occorrenza dei segreti noti in
    ogni forma di ``SECRET_VARIANTS``, dopo aver tolto i caratteri di
    controllo. Le forme piu' lunghe prima: un segreto che ne contiene un
    altro non resta mezzo scoperto."""
    if not text:
        return text
    text = strip_control_chars(text)
    forms: set[str] = set()
    for secret in secrets:
        if _usable(secret):
            forms |= secret_forms(secret)
    for form in sorted(forms, key=len, reverse=True):
        text = text.replace(form, REDACTED)
    return text


def sanitize_with_secrets(
    message: str | None,
    secrets: Iterable[str],
    max_len: int = _DEFAULT_MAX_LEN,
) -> str:
    """I due livelli insieme: segreti noti, poi regole generiche e troncamento."""
    if message is None:
        return sanitize_http_detail(None, max_len=max_len)
    return sanitize_http_detail(redact_known_secrets(str(message), secrets), max_len=max_len)
