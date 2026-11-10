"""P31 — intestazioni di sicurezza del frontend: una definizione, tre server.

`app.core.security.frontend_headers` e' l'unica fonte della CSP. Questi test
fissano la regola (direttive, origini solo da configurazione, niente
'unsafe-*') e che ogni server la riceva da li': Caddy (installer Windows,
produzione), nginx (immagine Docker) e Vite (stesso file di nginx).
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from app.config import Settings
from app.core.security import frontend_headers as fh
from tests2.shared._native_installer_render import CADDY_VALUES, parse_caddyfile, render_caddyfile


pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.contract]

BACKEND = Path(__file__).resolve().parents[3]
ROOT = BACKEND.parent
FRONTEND = ROOT / "frontend"
NGINX_SNIPPET = FRONTEND / "security-headers.nginx.conf"
NGINX_CONF = FRONTEND / "nginx.conf"
DOCKERFILE = FRONTEND / "Dockerfile"
INSTALLER = ROOT / "scripts" / "install-native.ps1"
VALIDATOR = ROOT / "scripts" / "validate-windows-install.ps1"

CSP = "Content-Security-Policy"
DEFAULTS = fh.DefaultsOnly(Settings.DEFAULTS)


def _settings(**overrides):
    return fh.DefaultsOnly({**Settings.DEFAULTS, **overrides})


def _directives(policy: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for part in policy.split(";"):
        name, *sources = part.split()
        assert name not in out, f"direttiva ripetuta: {name}"
        out[name] = sources
    return out


# ── La regola ────────────────────────────────────────────────────────────────

def test_default_policy_is_exactly_the_declared_rule():
    csp = _directives(fh.build_frontend_security_headers(DEFAULTS)[CSP])
    assert csp == {
        "default-src": ["'self'"],
        "script-src": ["'self'"],
        "style-src": ["'self'"],
        "img-src": [
            "'self'", "data:", "blob:",
            "https://*.basemaps.cartocdn.com",
            "https://*.tile.openstreetmap.org",
            "https://server.arcgisonline.com",
        ],
        "font-src": ["'self'"],
        "connect-src": ["'self'"],
        # La vista dashboard incorpora /grafana (stesso origin); Plenora
        # stessa non si lascia incorporare da nessuno.
        "frame-src": ["'self'"],
        "object-src": ["'none'"],
        "base-uri": ["'self'"],
        "form-action": ["'self'"],
        "frame-ancestors": ["'none'"],
    }


def test_static_security_headers_are_served_with_the_policy():
    headers = fh.build_frontend_security_headers(DEFAULTS)
    assert list(headers) == [CSP, *[name for name, _ in fh.STATIC_HEADERS]]
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert headers["X-Frame-Options"] == "DENY"
    assert "camera=()" in headers["Permissions-Policy"]


@pytest.mark.parametrize(
    "overrides",
    [
        {},
        {"FRONTEND_SENTRY_DSN": "https://k@o1.ingest.sentry.io/2"},
        {"FRONTEND_MAP_TILE_ORIGINS": ""},
        {"FRONTEND_CSP_REPORT_ONLY": True},
    ],
)
def test_no_configuration_ever_produces_unsafe_sources(overrides):
    for name, value in fh.build_frontend_security_headers(_settings(**overrides), api_url="https://api.x.it").items():
        assert "unsafe-" not in value, name
        assert "*" not in value.replace("https://*.", ""), name


def test_external_origins_come_only_from_configuration():
    headers = fh.build_frontend_security_headers(
        _settings(
            FRONTEND_MAP_TILE_ORIGINS="https://tiles.example.org, http://*.osm.local:8080",
            FRONTEND_SENTRY_DSN="https://key@o42.ingest.sentry.io/7",
        ),
        api_url="https://api.example.org:8443/base/",
    )
    csp = _directives(headers[CSP])
    assert csp["img-src"] == ["'self'", "data:", "blob:", "https://tiles.example.org", "http://*.osm.local:8080"]
    assert csp["connect-src"] == ["'self'", "https://o42.ingest.sentry.io", "https://api.example.org:8443"]
    # Nient'altro si apre: le altre direttive restano solo 'self'/'none'.
    for name in ("default-src", "script-src", "style-src", "font-src", "frame-src"):
        assert csp[name] == ["'self'"]


def test_empty_tiles_and_same_origin_api_close_everything():
    csp = _directives(fh.build_frontend_security_headers(_settings(FRONTEND_MAP_TILE_ORIGINS=""), api_url="/api")[CSP])
    assert csp["img-src"] == ["'self'", "data:", "blob:"]
    assert csp["connect-src"] == ["'self'"]


def test_report_only_switch_renames_the_header():
    headers = fh.build_frontend_security_headers(_settings(FRONTEND_CSP_REPORT_ONLY=True))
    assert CSP not in headers
    assert fh.CSP_REPORT_ONLY_HEADER in headers
    assert fh.build_frontend_security_headers(DEFAULTS, report_only=True).keys() == headers.keys()


@pytest.mark.parametrize(
    "value",
    [
        "https://ok.org; script-src *",
        "https://ok.org/path",
        "'unsafe-inline'",
        "ftp://files.org",
        'https://a"b.org',
        "*",
        "https://*.*.org",
    ],
)
def test_malformed_tile_origins_are_rejected(value):
    with pytest.raises(ValueError, match="FRONTEND_MAP_TILE_ORIGINS"):
        fh.build_frontend_security_headers(_settings(FRONTEND_MAP_TILE_ORIGINS=value))


@pytest.mark.parametrize("dsn", ["not a url", "ftp://k@host/1", "https://k@ho st/1"])
def test_malformed_sentry_dsn_is_rejected(dsn):
    with pytest.raises(ValueError, match="FRONTEND_SENTRY_DSN"):
        fh.build_frontend_security_headers(_settings(FRONTEND_SENTRY_DSN=dsn))


def test_vite_build_env_carries_the_same_dsn_as_the_policy():
    settings = _settings(FRONTEND_SENTRY_DSN="https://k@o9.ingest.sentry.io/1")
    assert fh.vite_build_env(settings) == {"VITE_SENTRY_DSN": "https://k@o9.ingest.sentry.io/1"}
    assert "https://o9.ingest.sentry.io" in fh.build_frontend_security_headers(settings)[CSP]


# ── Ogni server la riceve da qui ─────────────────────────────────────────────

def test_committed_nginx_snippet_is_the_module_output_with_defaults():
    expected = fh.render_nginx(fh.build_frontend_security_headers(DEFAULTS))
    assert NGINX_SNIPPET.read_text(encoding="utf-8").replace("\r\n", "\n") == expected, (
        "frontend/security-headers.nginx.conf non e' aggiornato: rigeneralo con "
        "python -m app.core.security.frontend_headers --format nginx --defaults-only "
        "--output ../frontend/security-headers.nginx.conf"
    )


def _nginx_blocks(text: str) -> list[str]:
    """Il blocco server (senza le location) e ogni location, come testo."""
    blocks, server, depth, current = [], [], 0, None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("location ") and depth == 1:
            current = [line]
        elif current is not None:
            current.append(line)
        else:
            server.append(line)
        depth += line.count("{") - line.count("}")
        if current is not None and depth == 1:
            blocks.append("\n".join(current))
            current = None
    return ["\n".join(server), *blocks]


def test_nginx_never_writes_headers_by_hand_and_every_header_scope_includes_the_snippet():
    conf = NGINX_CONF.read_text(encoding="utf-8")
    include = "include /etc/nginx/plenora-security-headers.conf;"
    for name in [CSP, *[n for n, _ in fh.STATIC_HEADERS]]:
        assert f"add_header {name}" not in conf
    blocks = _nginx_blocks(conf)
    assert include in blocks[0], "il blocco server deve includere le intestazioni"
    # nginx non eredita gli add_header del server in una location che ne ha di
    # suoi: ognuna di queste deve includere di nuovo il file.
    for block in blocks[1:]:
        if "add_header" in block:
            assert include in block, block.splitlines()[0]


def test_docker_image_ships_the_snippet_and_refuses_what_its_policy_would_block():
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    assert "COPY frontend/security-headers.nginx.conf /etc/nginx/plenora-security-headers.conf" in dockerfile
    guard = dockerfile.index('if [ -n "$VITE_SENTRY_DSN" ]')
    assert dockerfile.index('case "$VITE_API_URL"') < guard < dockerfile.index("RUN npm run build")


def _installer() -> str:
    return INSTALLER.read_text(encoding="utf-8-sig").replace("\r\n", "\n")


def test_installer_generates_caddy_headers_from_the_module_before_the_build():
    installer = _installer()
    generate = "python -m app.core.security.frontend_headers"
    assert generate in installer
    assert '"--api-url=$FrontendApiUrl"' in installer
    assert "$env:VITE_SENTRY_DSN = $frontendBuildEnv.VITE_SENTRY_DSN" in installer
    assert installer.index(generate) < installer.index("& $npm run build")
    assert "Content-Security-Policy" not in installer


PLENORA_IMPORT = f"import {CADDY_VALUES['headersCaddy']}"
# Le sole direttive che servono una risposta nel Caddyfile.
TERMINAL_HANDLERS = {"reverse_proxy", "file_server"}


@pytest.mark.parametrize("grafana", [False, True], ids=["senza-grafana", "con-grafana"])
def test_every_non_grafana_response_of_the_generated_caddyfile_gets_the_plenora_headers(grafana):
    """Regola: OGNI risposta che non e' /grafana porta le intestazioni di Plenora.

    Il sito ha due soli rami esclusivi: `handle @grafana` (facoltativo, con le
    sue intestazioni) e un `handle` senza matcher che importa per prima cosa
    quelle di Plenora e contiene tutti gli altri. Il test enumera ogni handle
    e ogni direttiva che serve una risposta del Caddyfile generato: un handle
    nuovo fuori dal ramo Plenora, o un import spostato dopo, lo fanno fallire.
    """
    site = parse_caddyfile(render_caddyfile(grafana=grafana)).children[0]
    top_handles = [child.line for child in site.children if child.name == "handle"]
    assert top_handles == (["handle @grafana", "handle"] if grafana else ["handle"])
    # A livello del sito niente intestazioni: si applicherebbero anche a /grafana.
    assert all(child.name not in ("import", "header") for child in site.children)

    plenora = next(child for child in site.children if child.line == "handle")
    assert plenora.children[0].line == PLENORA_IMPORT, "import prima di ogni altro handle del ramo Plenora"

    served = 0
    for block, ancestors in site.walk():
        if block.name not in TERMINAL_HANDLERS and block.name != "handle":
            continue
        chain = [*ancestors, block]
        if any(b.line == "handle @grafana" for b in chain):
            assert all(PLENORA_IMPORT != c.line for b in chain for c in b.children)
            continue
        assert plenora in chain, f"{block.line!r} fuori dal ramo con le intestazioni Plenora"
        served += block.name in TERMINAL_HANDLERS
    assert served == 3  # API proxate, /assets, index.html (SPA)


def test_grafana_proxy_headers_allow_same_origin_embedding_only():
    caddy = fh.render_caddy_grafana()
    lines = [line.strip() for line in caddy.splitlines()]
    assert lines[1:4] == ["header {", "defer", "-X-Frame-Options"]
    headers = dict(fh.GRAFANA_PROXY_HEADERS)
    assert headers[CSP] == "frame-ancestors 'self'"
    # Il lasciapassare e' nell'URL della dashboard: non deve uscire come Referer.
    assert headers["Referrer-Policy"] == "no-referrer"
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert "X-Frame-Options" not in headers
    for name, value in headers.items():
        assert f'    {name} "{value}"' in caddy
        assert "unsafe-" not in value and '"' not in value


def test_validator_checks_the_headers_on_the_real_frontend_response():
    validator = VALIDATOR.read_text(encoding="utf-8-sig")
    assert "$response.Headers['Content-Security-Policy']" in validator
    assert "unsafe-inline|unsafe-eval" in validator
    assert "X-Content-Type-Options" in validator


def test_caddy_rendering_matches_the_headers():
    headers = fh.build_frontend_security_headers(DEFAULTS)
    caddy = fh.render_caddy(headers)
    assert re.search(r"^header \{$", caddy, re.M)
    for name, value in headers.items():
        assert f'    {name} "{value}"' in caddy


def test_cli_writes_every_format(tmp_path):
    for fmt in ("caddy", "caddy-grafana", "nginx", "json"):
        out = tmp_path / f"h.{fmt}"
        subprocess.run(
            [sys.executable, "-m", "app.core.security.frontend_headers", "--format", fmt,
             "--defaults-only", "--api-url=", "--output", str(out)],
            cwd=BACKEND, check=True,
        )
        text = out.read_text(encoding="ascii")
        if fmt == "json":
            data = json.loads(text)
            assert data["headers"] == fh.build_frontend_security_headers(DEFAULTS)
            assert data["vite_env"] == {"VITE_SENTRY_DSN": ""}
        elif fmt == "caddy-grafana":
            assert text == fh.render_caddy_grafana()
        else:
            assert "default-src 'self'" in text
