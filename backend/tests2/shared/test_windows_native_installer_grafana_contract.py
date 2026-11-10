"""Grafana opzionale nell'installer Windows nativo (-InstallGrafana).

Contratto (piano Fase 5, «Installazione Grafana (11/11/2026)»):
- artefatti con URL e SHA-256 FISSI, verificati anche quando arrivano offline;
  spazio disco controllato prima di scaricare;
- senza -InstallGrafana nulla cambia (nessun /grafana nel Caddyfile);
- Caddy: /grafana toglie X-WEBAUTH-* PRIMA di forward_auth verso il backend,
  intestazioni proprie (dal modulo P31), proxy solo verso 127.0.0.1:3300;
- custom.ini: tabella dichiarativa delle impostazioni di sicurezza;
- .env: l'installer scrive SOLO i tre segreti, mai impostazioni;
- bootstrap (scripts/grafana_bootstrap.py) idempotente, provato contro un
  Grafana finto in memoria.
"""

from __future__ import annotations

import configparser
import importlib.util
import json
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import pytest

from tests2.shared._native_installer_render import (
    CADDY_VALUES,
    ROOT,
    installer_text,
    parse_caddyfile,
    render_caddyfile,
    render_grafana_ini,
)


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]

VALIDATOR = ROOT / "scripts" / "validate-windows-install.ps1"
BOOTSTRAP = ROOT / "scripts" / "grafana_bootstrap.py"
DOCS = ROOT / "docs" / "ops" / "install-windows.md"

PINNED = {
    "GrafanaVersion": "13.3.0",
    "GrafanaZipUrl": "https://dl.grafana.com/oss/release/grafana-13.3.0.windows-amd64.zip",
    "GrafanaZipSha256": "133f0a0c7b905a6c7e4f3df596cf4df535ca78f1e3ef7e5be5f3f2178a51f75d",
    "InfinityVersion": "4.1.6",
    "InfinityZipUrl": (
        "https://grafana.com/api/plugins/yesoreyeram-infinity-datasource/versions/4.1.6/"
        "download?os=windows&arch=amd64"
    ),
    "InfinityZipSha256": "a8c76c7e54ddd26adbdbc9e4a97568ebbf94316236acdba475a93c3a7a44ca18",
}


def _grafana_block(text: str) -> str:
    start = text.index("if ($InstallGrafana) {\n    Step \"Grafana OSS")
    end = text.index("# W4 — Frontend", start)
    return text[start:end]


def _function(text: str, name: str) -> str:
    start = text.index(f"function {name} {{")
    end = text.index("\n}\n", start)
    return text[start:end]


# ── Artefatti: URL e SHA-256 fissi ───────────────────────────────────────────

def test_grafana_artifacts_are_pinned_to_fixed_url_and_sha256():
    text = installer_text()
    for name, value in PINNED.items():
        assert f"${name} = '{value}'" in text, name
    # Nessun altro URL Grafana (niente «latest», niente versioni diverse).
    urls = set(re.findall(r"https://[^\s'\"]*grafana\.com[^\s'\"]*", text))
    assert urls == {PINNED["GrafanaZipUrl"], PINNED["InfinityZipUrl"]}


def test_every_archive_downloaded_or_offline_is_verified_against_the_pinned_hash():
    text = installer_text()
    block = _grafana_block(text)
    calls = re.findall(r"Get-VerifiedArchive (.+?)\n\s*-DownloadTo", block.replace("`\n", "\n"), re.S)
    assert len(calls) == 2
    assert "-LocalPath $GrafanaArchive" in calls[0] and "-Sha256 $GrafanaZipSha256" in calls[0]
    assert "-Url $GrafanaZipUrl" in calls[0]
    assert "-LocalPath $GrafanaInfinityArchive" in calls[1] and "-Sha256 $InfinityZipSha256" in calls[1]
    assert "-Url $InfinityZipUrl" in calls[1]
    # Ogni zip estratto e' quello appena verificato.
    assert block.count("Expand-ZipInto -Archive $zip") == 2
    assert "Invoke-WebRequest" not in block and "Expand-Archive" not in block

    verify = _function(text, "Get-VerifiedArchive")
    # L'hash si calcola DOPO il ramo offline/download, quindi vale per entrambi.
    assert verify.count("Get-FileHash") == 1
    assert verify.index("if ($LocalPath)") < verify.index("Get-FileHash")
    assert verify.index("Invoke-WebRequest") < verify.index("Get-FileHash")
    assert verify.count("return") == 1 and verify.rstrip().endswith("return $archive")


def test_offline_parameters_exist_and_are_checked_up_front():
    text = installer_text()
    params = text[text.index("param("): text.index("\n)\n")]
    for name in ("[switch]$InstallGrafana,", "[string]$GrafanaArchive = ''",
                 "[string]$GrafanaInfinityArchive = ''", "[string]$GrafanaRootUrl = ''"):
        assert name in params
    # Il primo controllo e' prima di qualunque passo che modifica la macchina.
    assert text.index("-InstallGrafana richiede -InstallFrontend") < text.index("Step 'Verifica prerequisiti di base'")
    assert text.index("Archivio offline Grafana non trovato") < text.index("Step 'Verifica prerequisiti di base'")


def test_free_disk_space_is_checked_before_any_download():
    text = installer_text()
    assert "$GrafanaMinFreeBytes = 1.5GB" in text
    block = _grafana_block(text)
    assert block.index("AvailableFreeSpace -lt $GrafanaMinFreeBytes") < block.index("Get-VerifiedArchive")


# ── Senza -InstallGrafana nulla cambia ───────────────────────────────────────

def test_grafana_is_opt_in_and_every_grafana_step_is_gated():
    text = installer_text()
    assert "[switch]$InstallGrafana = $true" not in text
    # Validazione, blocco servizio, file di intestazioni, route Caddy: tutti
    # dentro `if ($InstallGrafana)`; nessun altro punto tocca Grafana.
    assert text.count("if ($InstallGrafana) {") == 4
    assert "if ($InstallGrafana) { $headerOutputs['caddy-grafana'] = $grafanaHeadersCaddy }" in text
    route_def = text.index('$grafanaRoute = @"')
    assert text.rindex("if ($InstallGrafana) {", 0, route_def) > text.index("$grafanaRoute = ''")


def test_caddyfile_without_grafana_has_no_grafana_route():
    caddyfile = render_caddyfile(grafana=False)
    assert "grafana" not in caddyfile.lower()
    assert "3300" not in caddyfile and "forward_auth" not in caddyfile


# ── Caddy: il blocco /grafana ────────────────────────────────────────────────

def _site(caddyfile: str):
    root = parse_caddyfile(caddyfile)
    assert len(root.children) == 1
    return root.children[0]


def test_grafana_route_strips_identity_then_asks_plenora_then_proxies_to_loopback():
    site = _site(render_caddyfile(grafana=True))
    lines = [child.line for child in site.children]
    assert "@grafana path /grafana /grafana/*" in lines
    handle = next(child for child in site.children if child.line == "handle @grafana")
    assert [child.line for child in handle.children] == ["route"]
    route = handle.children[0]
    assert [child.name for child in route.children] == [
        "import", "request_header", "forward_auth", "reverse_proxy",
    ]
    imp, strip, auth, proxy = route.children
    # Intestazioni per prime (e `defer` nel file): coprono anche il 401.
    assert imp.line == f"import {CADDY_VALUES['grafanaHeadersCaddy']}"
    assert strip.line == "request_header -X-WEBAUTH-*"
    assert auth.line == "forward_auth 127.0.0.1:5004"
    assert [c.line for c in auth.children] == [
        "uri /api/grafana/auth",
        "copy_headers X-WEBAUTH-USER X-WEBAUTH-NAME X-WEBAUTH-ROLE",
    ]
    assert proxy.line == "reverse_proxy 127.0.0.1:3300"
    # Mai le intestazioni di Plenora (X-Frame-Options DENY) su /grafana.
    for block, _ in handle.walk():
        assert CADDY_VALUES["headersCaddy"] not in block.line


def test_grafana_matcher_is_the_exact_prefix_not_a_glob():
    site = _site(render_caddyfile(grafana=True))
    matchers = [c.line for c in site.children if c.line.startswith("@grafana")]
    # `/grafana*` prenderebbe anche /grafanaqualcosa: il confine e' /grafana e /grafana/.
    assert matchers == ["@grafana path /grafana /grafana/*"]


def test_grafana_headers_file_is_generated_by_the_p31_module_before_the_caddyfile():
    text = installer_text()
    assert "$grafanaHeadersCaddy = Join-Path $caddySvcDir 'grafana-headers.caddy'" in text
    assert text.index("python -m app.core.security.frontend_headers") < text.index("$caddyfile = Join-Path")


# ── custom.ini: tabella dichiarativa ─────────────────────────────────────────

EXPECTED_INI = {
    ("server", "http_addr"): "127.0.0.1",
    ("server", "http_port"): "3300",
    ("server", "serve_from_sub_path"): "true",
    ("server", "router_logging"): "false",
    ("security", "allow_embedding"): "true",
    ("security", "disable_initial_admin_creation"): "false",
    ("users", "allow_sign_up"): "false",
    ("users", "allow_org_create"): "false",
    ("users", "auto_assign_org"): "true",
    ("users", "auto_assign_org_id"): "1",
    ("users", "auto_assign_org_role"): "Viewer",
    ("users", "viewers_can_edit"): "false",
    ("auth", "disable_login_form"): "true",
    ("auth.basic", "enabled"): "false",
    ("auth.anonymous", "enabled"): "false",
    ("auth.proxy", "enabled"): "true",
    ("auth.proxy", "header_name"): "X-WEBAUTH-USER",
    ("auth.proxy", "header_property"): "username",
    ("auth.proxy", "whitelist"): "127.0.0.1, ::1",
    # Ruolo e nome dalle intestazioni del forward_auth a OGNI accesso: un
    # superadmin retrocesso torna Viewer (provato dal vivo su Grafana 13.3;
    # senza intestazione di ruolo Grafana conserva quello vecchio). Il login
    # (X-WEBAUTH-USER) e' l'ID Plenora, il nome visualizzato viaggia a parte.
    ("auth.proxy", "headers"): "Name:X-WEBAUTH-NAME Role:X-WEBAUTH-ROLE",
    ("auth.proxy", "auto_sign_up"): "true",
    ("auth.proxy", "sync_ttl"): "15",
    ("auth.proxy", "enable_login_token"): "false",
    ("snapshots", "enabled"): "false",
    ("public_dashboards", "enabled"): "false",
    ("explore", "enabled"): "false",
    ("unified_alerting", "enabled"): "false",
    ("plugins", "plugin_admin_enabled"): "false",
    ("plugins", "preinstall_disabled"): "true",
    ("plugins", "public_key_retrieval_disabled"): "true",
    ("analytics", "reporting_enabled"): "false",
    ("analytics", "check_for_updates"): "false",
    ("analytics", "check_for_plugin_updates"): "false",
    ("analytics", "feedback_links_enabled"): "false",
    ("news", "news_feed_enabled"): "false",
    ("live", "max_connections"): "0",
}


def _ini(**values) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None, comment_prefixes=(";",), strict=True)
    parser.read_string(render_grafana_ini(values or None))
    return parser


def test_grafana_ini_matches_the_declared_security_table():
    ini = _ini()
    for (section, key), expected in EXPECTED_INI.items():
        assert ini.get(section, key) == expected, f"[{section}] {key}"


def test_grafana_ini_root_url_is_under_grafana_and_cookies_follow_the_scheme():
    ini = _ini()
    assert ini.get("server", "root_url").endswith("/grafana/")
    text = installer_text()
    assert "$grafanaCookieSecure = if ($grafanaRoot -like 'https://*') { 'true' } else { 'false' }" in text
    assert "$GrafanaRootUrl -notmatch '^https?://[^/\\s\"]+/grafana/$'" in text


def test_grafana_secrets_in_the_ini_are_random_persisted_and_acl_protected():
    text = installer_text()
    block = _grafana_block(text)
    for key, var in (("admin_user", "grafanaAdminLogin"), ("secret_key", "grafanaSecretKey"),
                     ("admin_password", "grafanaAdminPassword")):
        # Riletti dal custom.ini precedente: cambiarli romperebbe database e segreti.
        assert f"${var} = Get-IniValue -File $grafanaIni -Key '{key}'" in block
        assert block.index(f"Get-IniValue -File $grafanaIni -Key '{key}'") < block.index("Set-Content -Encoding ascii $grafanaIni")
    assert "'plenora-grafana-admin-' + (New-RandomSecret)" in block
    assert "icacls $grafanaIni /inheritance:r /grant:r '*S-1-5-32-544:F' '*S-1-5-18:F'" in block


# ── .env: solo segreti ───────────────────────────────────────────────────────

def test_installer_writes_only_the_three_grafana_secrets_to_env():
    text = installer_text()
    block = _grafana_block(text)
    literal_keys = set(re.findall(r"Set-EnvValue -File \$envFile -Key '([A-Z_]+)'", block))
    assert literal_keys == {"GRAFANA_SERVICE_TOKEN"}
    assert "foreach ($secretKey in @('GRAFANA_DATA_SECRET', 'DASHBOARD_PASS_SECRET'))" in block
    assert re.search(
        r"if \(-not \(Get-EnvFileValue -File \$envFile -Key \$secretKey\)\) \{\s*"
        r"Set-EnvValue -File \$envFile -Key \$secretKey -Value \(New-RandomSecret\)",
        block,
    ), "i segreti si generano solo se assenti (riuso dei valori esistenti)"
    assert block.count("Set-EnvValue") == 2
    # Regola config boundary: GRAFANA_ENABLED e ogni impostazione restano in config.py.
    assert not re.search(r"-Key '?GRAFANA_ENABLED|GRAFANA_ENABLED=", text)


def test_bootstrap_secrets_travel_by_environment_never_on_the_command_line():
    block = _grafana_block(installer_text())
    call = block[block.index("& $condaBat run"): block.index("$bootstrapExit = $LASTEXITCODE")]
    assert "python scripts\\grafana_bootstrap.py" in call
    assert "--backend-url 'http://127.0.0.1:5004'" in call
    assert "$env:" not in call and "Secret" not in call and "Token" not in call
    cleanup = block[block.index("} finally {", block.index("& $condaBat run")):]
    for name in ("GRAFANA_BOOTSTRAP_ADMIN_LOGIN", "GRAFANA_DATA_SECRET", "GRAFANA_SERVICE_TOKEN"):
        assert f"Env:\\{name}" in cleanup.split("\n", 2)[1]


def test_grafana_service_listens_only_on_loopback_and_is_waited_for():
    block = _grafana_block(installer_text())
    assert "Install-WinSWService -Id 'plenora-grafana'" in block
    assert "<arguments>server --homepath $grafanaHome --config $grafanaIni</arguments>" in block
    assert "Test-NetConnection 127.0.0.1 -Port $GrafanaPort" in block
    assert _ini().get("server", "http_addr") == "127.0.0.1"


# ── Bootstrap contro un Grafana finto ────────────────────────────────────────

def _load_bootstrap():
    spec = importlib.util.spec_from_file_location("grafana_bootstrap", BOOTSTRAP)
    module = importlib.util.module_from_spec(spec)
    # Le dataclass del modulo risolvono le annotazioni da sys.modules.
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    return module


ADMIN = "plenora-grafana-admin-test"


class FakeGrafana:
    """Le sole API usate dal bootstrap, con lo stato che serve a provarle."""

    def __init__(self, *, admin_is_server_admin: bool = True) -> None:
        self.admin_is_server_admin = admin_is_server_admin
        self.org_name = "Main Org."
        self.accounts: dict[int, dict] = {}
        self.tokens: dict[int, dict] = {}
        self.datasources: dict[str, dict] = {}
        self.calls: list[tuple[str, str, dict]] = []
        self._next = 1

    def _id(self) -> int:
        self._next += 1
        return self._next

    def handle(self, method: str, path: str, headers: dict, body):
        self.calls.append((method, path, headers))
        assert path.startswith("/grafana/api/"), path
        path = path[len("/grafana"):]
        assert "basic" not in headers.get("authorization", "").lower(), "basic auth mai usata"
        bearer = headers.get("authorization", "")
        if bearer:
            key = bearer.removeprefix("Bearer ")
            if path == "/api/org" and any(t["key"] == key for t in self.tokens.values()):
                return 200, {"id": 1, "name": self.org_name}
            return 401, {"message": "invalid API key"}
        if path == "/api/health":
            return 200, {"database": "ok"}
        if headers.get("x-webauth-user") != ADMIN:
            return 401, {"message": "unauthorized"}
        if path == "/api/user":
            return 200, {"id": 1, "login": ADMIN, "isGrafanaAdmin": self.admin_is_server_admin}
        if path == "/api/orgs/1":
            if method == "PUT":
                self.org_name = body["name"]
            return 200, {"id": 1, "name": self.org_name}
        if path.startswith("/api/serviceaccounts/search"):
            return 200, {"serviceAccounts": list(self.accounts.values())}
        if path == "/api/serviceaccounts" and method == "POST":
            sa = {"id": self._id(), "name": body["name"], "role": body["role"]}
            self.accounts[sa["id"]] = sa
            return 201, sa
        match = re.fullmatch(r"/api/serviceaccounts/(\d+)/tokens(?:/(\d+))?", path)
        if match:
            if method == "GET":
                return 200, [{"id": t["id"], "name": t["name"]} for t in self.tokens.values()]
            if method == "POST":
                token = {"id": self._id(), "name": body["name"], "key": f"glsa_{self._next}"}
                self.tokens[token["id"]] = token
                return 200, token
            if method == "DELETE":
                del self.tokens[int(match.group(2))]
                return 200, {}
        match = re.fullmatch(r"/api/datasources(?:/uid/([\w-]+))?", path)
        if match:
            uid = match.group(1)
            if method == "GET":
                return (200, self.datasources[uid]) if uid in self.datasources else (404, {})
            self.datasources[body["uid"]] = body
            return 200, {"datasource": body}
        return 404, {"message": f"non simulato: {method} {path}"}


@pytest.fixture
def grafana():
    fake = FakeGrafana()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _serve(self):
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length)) if length else None
            headers = {k.lower(): v for k, v in self.headers.items()}
            status, payload = fake.handle(self.command, urlsplit(self.path).path, headers, body)
            raw = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = _serve

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    fake.url = f"http://127.0.0.1:{server.server_port}/grafana"
    yield fake
    server.shutdown()
    server.server_close()


def _run(module, fake, token=""):
    client = module.Grafana(fake.url, admin_login=ADMIN, timeout=5)
    return module.bootstrap(client, backend_url="http://127.0.0.1:5004", data_secret="s3greto",
                            current_token=token, wait_seconds=5)


def test_bootstrap_first_run_creates_org_account_token_and_datasource(grafana):
    module = _load_bootstrap()
    result = _run(module, grafana)
    assert result["token_created"] is True
    assert grafana.org_name == "Plenora Dashboard"
    assert [(a["name"], a["role"]) for a in grafana.accounts.values()] == [("plenora", "Admin")]
    assert [t["key"] for t in grafana.tokens.values()] == [result["service_token"]]
    ds = grafana.datasources["plenora-infinity"]
    assert ds["type"] == "yesoreyeram-infinity-datasource"
    assert ds["jsonData"]["allowedHosts"] == ["http://127.0.0.1:5004/api/dash/"]
    assert ds["jsonData"]["httpHeaderName1"] == "X-Grafana-Secret"
    assert ds["secureJsonData"] == {"httpHeaderValue1": "s3greto"}
    assert "s3greto" not in json.dumps(ds["jsonData"])


def test_bootstrap_is_idempotent_and_reuses_a_valid_token(grafana):
    module = _load_bootstrap()
    first = _run(module, grafana)
    second = _run(module, grafana, token=first["service_token"])
    assert second == {"service_token": first["service_token"], "token_created": False}
    assert len(grafana.accounts) == 1 and len(grafana.tokens) == 1
    assert list(grafana.datasources) == ["plenora-infinity"]
    assert ("PUT", "/grafana/api/datasources/uid/plenora-infinity") in [(m, p) for m, p, _ in grafana.calls]


def test_bootstrap_replaces_a_lost_token_and_revokes_the_old_ones(grafana):
    module = _load_bootstrap()
    first = _run(module, grafana)
    again = _run(module, grafana, token="glsa_perso")
    assert again["token_created"] is True and again["service_token"] != first["service_token"]
    assert [t["key"] for t in grafana.tokens.values()] == [again["service_token"]]


def test_bootstrap_refuses_an_admin_login_that_is_not_the_server_admin(grafana):
    module = _load_bootstrap()
    grafana.admin_is_server_admin = False
    with pytest.raises(module.BootstrapError, match="non e' amministratore"):
        _run(module, grafana)
    assert grafana.accounts == {} and grafana.datasources == {}


def test_bootstrap_authenticates_only_via_auth_proxy_or_the_service_token(grafana):
    module = _load_bootstrap()
    _run(module, grafana)
    for method, path, headers in grafana.calls:
        auth = headers.get("authorization", "")
        assert auth.startswith("Bearer ") or headers.get("x-webauth-user") == ADMIN or path.endswith("/health")


def test_bootstrap_takes_secrets_only_from_the_environment(monkeypatch, capsys):
    module = _load_bootstrap()
    source = BOOTSTRAP.read_text(encoding="utf-8")
    options = re.findall(r'add_argument\("(--[\w-]+)"', source)
    assert options == ["--grafana-url", "--backend-url", "--wait-seconds"]
    monkeypatch.delenv("GRAFANA_BOOTSTRAP_ADMIN_LOGIN", raising=False)
    assert module.main([]) == 2
    assert module.DASH_API_PREFIX == "/api/dash/" and module.DATASOURCE_UID == "plenora-infinity"


# ── Validatore ───────────────────────────────────────────────────────────────

def test_validator_checks_grafana_service_auth_and_plenora_headers():
    from app.core.security import frontend_headers as fh

    validator = VALIDATOR.read_text(encoding="utf-8-sig")
    assert "[switch]$RequireGrafana" in validator
    assert "Test-OptionalService 'plenora-grafana' 'Plenora Grafana' -Required:$RequireGrafana" in validator
    proxy = _function(validator.replace("\r\n", "\n"), "Test-GrafanaProxy([string]$BaseUrl)")
    assert "if ($status -ne 401)" in proxy
    assert "'X-WEBAUTH-USER' = 'validate-windows-install'" in proxy
    for directive in ("frame-ancestors 'self'", "default-src 'self'", "img-src 'self' data: blob:",
                      "connect-src 'self'"):
        assert f'"{directive}"' in proxy
        assert directive in dict(fh.GRAFANA_PROXY_HEADERS)["Content-Security-Policy"]
    assert '$csp -notlike "*$directive*"' in proxy
    assert "Test-FrontendPage -Url ($BaseUrl.TrimEnd('/') + '/grafana-confine')" in proxy
    assert "notin @('127.0.0.1', '::1')" in proxy
    assert "frame-ancestors 'none'" in _function(validator.replace("\r\n", "\n"), "Test-FrontendPage([string]$Url)")


def test_runbook_documents_install_offline_disk_and_risks():
    docs = DOCS.read_text(encoding="utf-8")
    for needle in ("-InstallGrafana", "-GrafanaArchive", "-GrafanaInfinityArchive", "1,5 GB",
                   "X-WEBAUTH-USER", "GRAFANA_SERVICE_TOKEN", "query string", "plenora-grafana",
                   "-RequireGrafana"):
        assert needle in docs, needle
