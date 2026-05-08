"""
Architecture-guard test suite — BE-GO-READY-ARCH-GUARDS-001.

Source-bound AST guardrails che bloccano la deriva legacy ad alto
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Dict, Iterable, List, Set, Tuple

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent  # .../backend
_APP_ROOT = _BACKEND_ROOT / "app"
_TESTS_ROOT = _BACKEND_ROOT / "tests2"


def _rel(p: Path) -> str:
    """Relative-to-backend path con forward-slash, indipendente dal SO."""
    return str(p.relative_to(_BACKEND_ROOT)).replace("\\", "/")


def _iter_py(root: Path) -> Iterable[Path]:
    for p in root.rglob("*.py"):
        yield p


def _parse(p: Path) -> ast.AST | None:
    try:
        return ast.parse(p.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return None


# Rule 1 — HTTPException(detail=str(e)) cross-reference


def test_value_error_str_e_lock_exists_in_catalog_test():
    """Cross-reference: la lock per file di
    `HTTPException(detail=str(e))` vive in
    `tests2/shared/test_error_codes_catalog.py` come
    `test_value_error_str_e_callsite_lock`. Questo meta-test impedisce
    rimozione silenziosa di quella protezione lockando il nome del
    test target nel file catalog."""
    catalog_test = _BACKEND_ROOT / "tests2" / "shared" / "test_error_codes_catalog.py"
    assert catalog_test.exists(), (
        f"Atteso: {catalog_test.relative_to(_BACKEND_ROOT)} esiste "
        f"(BE-GO-READY-ERROR-CODES-015 closure)."
    )
    text = catalog_test.read_text(encoding="utf-8")
    assert "def test_value_error_str_e_callsite_lock" in text, (
        "Atteso: `test_value_error_str_e_callsite_lock` definito in "
        "test_error_codes_catalog.py. Se rimosso, il drift detection "
        "AST per `HTTPException(detail=str(e))` non e' piu' attivo. "
        "Ripristina o rimuovi questa cross-reference dichiaratamente."
    )
    assert "EXPECTED_VALUE_ERROR_STR_E_COUNTS" in text, (
        "Atteso: tabella `EXPECTED_VALUE_ERROR_STR_E_COUNTS` per il "
        "lock per-file. Vedi BE-GO-READY-ERROR-CODES-015."
    )


# Rule 2 — os.getenv allowed only at boundary modules


# Allowlist per `os.getenv` in `backend/app/`.
#
# Razionale per file (ADR-0021 + BE_RULES sez. 6 "Config boundary"):
# - `app/config.py`: Settings.DEFAULTS infrastructure entry point.
# - `app/main.py`: startup boot, env diagnostics.
# - `app/db/migrations_runner.py`: bootstrap migrations runner
#   (deve distinguere "user did set" vs "default" ⇒ os.getenv diretto).
# - `app/db/pg_connection.py`: DB connection bootstrap (DATABASE_URL
#   fail-fast).
# - `app/shared/cloud_backends.py`: boot-time storage validation;
#   `validate_storage_config` deve distinguere "user explicitly set"
#   vs "Settings default" — Settings.get swallows malformed input.
#   Documentato come eccezione esplicita ADR-0021.
# - `app/shared/crypto.py`: secret/key loading at module load.
# - `app/shared/jwt_token.py`: JWT secret loading at module load.
# - `app/shared/request_utils.py`: TRUSTED_PROXIES infra config (1
#   read, lru_cache su raw value).
# - `app/utils/zip_safe.py`: zip uncompressed-bytes cap env reads
#   (operator-tunable).
# - `app/core/admin_backups/service.py`: 1 env read residua per
#   `AWS_REGION` (DEPLOY_INFRA). I 4 reads `BACKUP_BUCKET` /
#   `BACKUP_S3_BUCKET` / `BACKUP_SCHEDULE_CRON` /
#   `BACKUP_SCHEDULE_DESCRIPTION` sono stati chiusi in
#   BE-GO-READY-CONFIG-BOUNDARY-001 (BACKUP_BUCKET delega a
#   `cloud_backends._resolve_backup_bucket`; le 2 schedule string
#   sono migrate a `Settings.DEFAULTS`). Il legacy `BACKUP_S3_REGION`
#   era un dead alias e viene retirato. `AWS_REGION` resta DEPLOY_INFRA
#   env-only per coerenza con il pattern in `cloud_backends._build_object_storage`.
ALLOWLIST_GETENV_COUNTS: Dict[str, int] = {
    "app/config.py": 5,
    "app/main.py": 2,
    "app/db/migrations_runner.py": 1,
    "app/db/pg_connection.py": 3,
    "app/shared/cloud_backends.py": 30,
    "app/shared/crypto.py": 4,
    "app/shared/jwt_token.py": 3,
    "app/shared/request_utils.py": 1,
    "app/utils/zip_safe.py": 1,
    "app/core/admin_backups/service.py": 1,
}


def _is_os_getenv(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    f = node.func
    if not isinstance(f, ast.Attribute):
        return False
    if f.attr != "getenv":
        return False
    return isinstance(f.value, ast.Name) and f.value.id == "os"


def _scan_getenv() -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for p in _iter_py(_APP_ROOT):
        tree = _parse(p)
        if tree is None:
            continue
        n = sum(1 for node in ast.walk(tree) if _is_os_getenv(node))
        if n:
            counts[_rel(p)] = n
    return counts


def test_os_getenv_only_at_boundary_modules():
    """Lock: `os.getenv` puo' essere usato SOLO nei moduli boundary
    elencati in `ALLOWLIST_GETENV_COUNTS`. Drift in piu' (nuovo file
    o piu' occorrenze in un file allowlistato) -> failure forza
    classificazione esplicita; drift in meno -> failure forza
    aggiornamento del lock per riflettere la nuova baseline.

    Ogni nuovo `os.getenv` in core/services/router/db/services deve
    invece passare per `Settings.DEFAULTS` + `settings.get(...)`
    (ADR-0021)."""
    found = _scan_getenv()
    assert found == ALLOWLIST_GETENV_COUNTS, (
        "Drift `os.getenv` rilevato in `backend/app/`.\n"
        f"Atteso: {ALLOWLIST_GETENV_COUNTS}\n"
        f"Trovato: {found}\n"
        "Action: se il sito e' boundary legittimo, aggiorna l'allowlist "
        "con razionale; altrimenti migra a `Settings.DEFAULTS` + "
        "`settings.get(...)` (ADR-0021)."
    )


# Rule 3 — `app.db.pg_connection` direct access from routers


# Allowlist: router legacy che importano `app.db.pg_connection`
# direttamente. Razionale per file:
# - `app/core/intermediate/router.py`: endpoint admin di
#   intermediate runs/nodes; query read-only su `runs`/`node_meta`.
# - `app/core/logs/router.py` + `runs_router.py`: log query
#   read-only paginate.
#
# Migrazioni gia' chiuse (la baseline scende man mano):
# - `app/core/projects/router.py` (BE-GO-READY-CONNECTION-ENGINE-003):
#   project-id lookup migrato a
#   `app.core.projects.db.list_owned_project_ids`.
# - `app/core/imdf/router.py` (BE-GO-READY-CONNECTION-ENGINE-003):
#   project-id-by-group lookup migrato a
#   `app.core.projects.db.list_project_ids_for_group`.
# - `app/core/orchestrator/router.py` (BE-GO-READY-CONNECTION-ENGINE-006):
#   single `DELETE log_runs` migrato a
#   `app.db.log_runs_db_service.delete_log_run_by_run_id`.
#
# Lock: SET di file allowlistati. Nuovi router NON possono importare
# pg_connection senza esplicita aggiunta + razionale.
ALLOWLIST_PG_CONNECTION_ROUTER_FILES: Set[str] = {
    "app/core/intermediate/router.py",
    "app/core/logs/router.py",
    "app/core/logs/runs_router.py",
}


def _is_router_path(rel_path: str) -> bool:
    return rel_path.endswith("/router.py") or rel_path.endswith("_router.py")


def _scan_pg_connection_in_routers() -> Set[str]:
    hits: Set[str] = set()
    for p in _iter_py(_APP_ROOT):
        rel = _rel(p)
        if not _is_router_path(rel):
            continue
        tree = _parse(p)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module == "app.db.pg_connection" or node.module.startswith(
                    "app.db.pg_connection."
                ):
                    hits.add(rel)
                    break
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "app.db.pg_connection":
                        hits.add(rel)
                        break
    return hits


def test_pg_connection_not_imported_from_new_routers():
    """Lock: solo i router elencati in
    `ALLOWLIST_PG_CONNECTION_ROUTER_FILES` possono importare
    `app.db.pg_connection`. Nuovi router devono delegare al service
    layer / repository module per persistenza.

    Drift in piu' (nuovo router con import) -> failure;
    drift in meno (un router esistente migrato a service layer) ->
    aggiornare l'allowlist."""
    found = _scan_pg_connection_in_routers()
    extra = found - ALLOWLIST_PG_CONNECTION_ROUTER_FILES
    missing = ALLOWLIST_PG_CONNECTION_ROUTER_FILES - found
    assert not extra, (
        "Nuovi router con import diretto di `app.db.pg_connection`: "
        f"{sorted(extra)}.\n"
        "Action: estrai la query in un service/repository module e "
        "fai il router consumare quello, oppure aggiungi il file "
        "all'allowlist con razionale esplicito."
    )
    assert not missing, (
        f"Router presenti nell'allowlist ma non piu' rilevati: "
        f"{sorted(missing)}.\n"
        "Action: aggiorna `ALLOWLIST_PG_CONNECTION_ROUTER_FILES` per "
        "riflettere la nuova baseline (probabilmente buon segno: il "
        "router e' stato migrato a service layer)."
    )


# Rule 4 — `app.main` imports inside shared/unit tests


# Allowlist test file che importano `app.main`. Razionale:
# - `tests2/security/unit/test_c8_observability_contract.py`: il test
#   esplicitamente verifica che il middleware/observability wiring
#   in `main.py` sia integro. Patcha le check di
#   postgres/redis/pool/migrations e spawna un TestClient sull'app
#   reale. Documentato sez. C8 dell'observability contract.
ALLOWLIST_APP_MAIN_TEST_FILES: Set[str] = {
    "tests2/security/unit/test_c8_observability_contract.py",
}


def _is_shared_or_unit_test(rel_path: str) -> bool:
    """Restringe lo scan ai test in `tests2/shared/` e ovunque sotto
    una sotto-cartella `unit/` (convention codebase)."""
    return "/shared/" in rel_path or "/unit/" in rel_path


def _scan_app_main_in_unit_tests() -> Set[str]:
    hits: Set[str] = set()
    for p in _iter_py(_TESTS_ROOT):
        rel = _rel(p)
        if not _is_shared_or_unit_test(rel):
            continue
        tree = _parse(p)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "app.main":
                hits.add(rel)
                break
            if isinstance(node, ast.Import):
                if any(alias.name == "app.main" for alias in node.names):
                    hits.add(rel)
                    break
    return hits


def test_app_main_not_imported_from_new_unit_tests():
    """Lock: solo i test in `ALLOWLIST_APP_MAIN_TEST_FILES` possono
    importare `app.main` da test sotto `tests2/shared/` o
    `tests2/**/unit/`. Altri test devono usare TestClient mini-app
    (vedi `test_c8_observability_contract::_build_middleware_app`)
    o monkeypatch chirurgici per evitare boot full-DB/Redis."""
    found = _scan_app_main_in_unit_tests()
    extra = found - ALLOWLIST_APP_MAIN_TEST_FILES
    missing = ALLOWLIST_APP_MAIN_TEST_FILES - found
    assert not extra, (
        f"Nuovi test importano `app.main`: {sorted(extra)}.\n"
        "Action: usa TestClient su mini-app FastAPI con i soli "
        "middleware necessari, oppure aggiungi il file all'allowlist "
        "con razionale (es. observability/wiring contract)."
    )
    assert not missing, (
        f"Test in allowlist non piu' rilevati: {sorted(missing)}.\n"
        "Action: aggiorna `ALLOWLIST_APP_MAIN_TEST_FILES` (probabile "
        "buon segno: il test e' stato migrato a mini-app)."
    )


# Rule 5 — `raise_typed_http(code="...")` literal vs catalog constant


def _extract_code_arg(call: ast.Call) -> ast.AST | None:
    """Estrae il valore dell'argomento `code` da una chiamata
    `raise_typed_http(...)` (positional o keyword)."""
    if call.args:
        return call.args[0]
    for kw in call.keywords:
        if kw.arg == "code":
            return kw.value
    return None


def _scan_raise_typed_http_literals() -> List[Tuple[str, int, str]]:
    """Trova chiamate `raise_typed_http(code=<literal-str>, ...)` con
    `code` stringa literale invece di una costante catalog."""
    hits: List[Tuple[str, int, str]] = []
    for p in _iter_py(_APP_ROOT):
        tree = _parse(p)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            name = None
            if isinstance(f, ast.Name):
                name = f.id
            elif isinstance(f, ast.Attribute):
                name = f.attr
            if name != "raise_typed_http":
                continue
            code_arg = _extract_code_arg(node)
            if code_arg is None:
                continue
            if isinstance(code_arg, ast.Constant) and isinstance(code_arg.value, str):
                hits.append((_rel(p), node.lineno, code_arg.value))
    return hits


def test_raise_typed_http_uses_catalog_constant_only():
    """Lock: `raise_typed_http(code=...)` deve sempre usare una
    costante catalog (`error_codes.<CONST>`), mai un string literal.

    Baseline pulito: 0 hits. Drift in piu' = qualcuno ha hardcodato
    un codice; il drift detection del catalog
    (`test_router_error_literals_are_in_catalog`) potrebbe non
    coprire il caso `raise_typed_http(code="literal", ...)` perche'
    quello scanner cerca la shape `"error": "<x>"` nei dict-detail.
    Questo guard chiude il gap source-bound."""
    hits = _scan_raise_typed_http_literals()
    assert hits == [], (
        "Trovati `raise_typed_http(code=<literal>, ...)` con string "
        f"literal: {hits}\n"
        "Action: sostituisci con `error_codes.<CONST>` (aggiungi la "
        "costante al catalog se manca, vedi `app/shared/error_codes.py`)."
    )


# Sanity: scanner funzionano davvero (anti-regression)


def test_scanners_find_expected_baseline_files():
    """Sanity: lo scanner Rule 2 trova almeno i file core boundary
    (config.py, main.py). Se questo fail, lo scanner e' rotto e i
    drift sopra sono illusori."""
    found = _scan_getenv()
    assert "app/config.py" in found
    assert "app/main.py" in found


def test_scanner_pg_connection_finds_router_baseline():
    """Sanity: lo scanner Rule 3 trova almeno 1 router conosciuto
    nell'allowlist."""
    found = _scan_pg_connection_in_routers()
    assert "app/core/logs/runs_router.py" in found


def test_scanner_app_main_finds_observability_test():
    """Sanity: lo scanner Rule 4 trova il test allowlistato di
    observability."""
    found = _scan_app_main_in_unit_tests()
    assert "tests2/security/unit/test_c8_observability_contract.py" in found
