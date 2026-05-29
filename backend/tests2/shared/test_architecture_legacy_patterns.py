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


def _import_targets(node: ast.AST) -> list[str]:
    if isinstance(node, ast.ImportFrom) and node.module is not None:
        return [node.module]
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    return []


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


# Rule 1b — shared/db layering boundaries


ALLOWLIST_SHARED_TO_CORE_IMPORTS: Set[str] = set()


# Migrazione completata: i vecchi shim `app/db/query_*` (relocati in
# app.core.query_execution) sono stati rimossi — nessun caller residuo. `app.db`
# e' ora fondazione pura: nessun import verso `app.core`.
ALLOWLIST_DB_TO_CORE_IMPORTS: Set[str] = set()


def _files_importing_prefix(root: Path, prefix: str) -> Set[str]:
    hits: Set[str] = set()
    for p in _iter_py(root):
        tree = _parse(p)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if any(target == prefix or target.startswith(f"{prefix}.")
                   for target in _import_targets(node)):
                hits.add(_rel(p))
                break
    return hits


def test_shared_package_does_not_import_core_modules():
    """Shared must remain a leaf package: no app.shared -> app.core imports."""
    found = _files_importing_prefix(_APP_ROOT / "shared", "app.core")
    extra = found - ALLOWLIST_SHARED_TO_CORE_IMPORTS
    missing = ALLOWLIST_SHARED_TO_CORE_IMPORTS - found
    assert not extra, (
        "app/shared files importano app.core, violando il leaf boundary: "
        f"{sorted(extra)}."
    )
    assert not missing, (
        "Shared->core allowlist stale: "
        f"{sorted(missing)}."
    )


def test_db_foundation_does_not_import_core_modules():
    """app.db is foundation code; query execution lives under app.core."""
    found = _files_importing_prefix(_APP_ROOT / "db", "app.core")
    extra = found - ALLOWLIST_DB_TO_CORE_IMPORTS
    missing = ALLOWLIST_DB_TO_CORE_IMPORTS - found
    assert not extra, (
        "app/db files importano app.core fuori dagli shim consentiti: "
        f"{sorted(extra)}. Sposta la capability sopra app.db o passa da "
        "un protocol/port."
    )
    assert not missing, (
        "DB->core allowlist stale: "
        f"{sorted(missing)}. Se uno shim non importa piu' app.core, "
        "rimuovilo dall'allowlist."
    )


# Rule 2 — os.getenv allowed only at boundary modules


# Allowlist per `os.getenv` in `backend/app/`.
#
# Razionale per file (ADR-0021 + BE_RULES sez. 6 "Config boundary"):
# - `app/config.py`: Settings.DEFAULTS infrastructure entry point. The direct
#   reads left here are `Settings.get` and `Settings.get_raw`, so runtime
#   config overrides and boot validators keep one typed/raw path.
# - `app/config_env_loader.py`: dotenv/bootstrap env loading boundary. It owns
#   the raw `ENVIRONMENT`/`ENV` distinction needed before Settings exists.
# - `app/startup_validation.py`: explicit bootstrap validation boundary.
# - `app/db/migrations_runner.py`: bootstrap migrations runner
#   (deve distinguere "user did set" vs "default" ⇒ os.getenv diretto).
# - `app/db/pg_connection.py`: DB connection bootstrap (DATABASE_URL
#   fail-fast).
# - `app/infrastructure/security/crypto.py`: secret/key loading at module load.
# - `app/core/auth/jwt_token.py`: JWT secret loading at module load.
# - `app/shared/request_utils.py`: TRUSTED_PROXIES infra config (1
#   read, lru_cache su raw value).
# - `app/utils/zip_safe.py`: zip uncompressed-bytes cap env reads
#   (operator-tunable).
# - `app/core/admin_backups/service.py`: chiuso a zero raw env read; bucket,
#   region, schedule e trigger policy passano da Settings/storage boundary.
# - `app/core/auth/smoke_seed.py`: docker smoke seed boundary; deve
#   distinguere variabili esplicitamente settate dal default runtime.
ALLOWLIST_GETENV_COUNTS: Dict[str, int] = {
    "app/config.py": 2,
    "app/environment.py": 1,
}


def _get_os_getenv_call_names(tree: ast.AST) -> Tuple[Set[str], Set[str]]:
    """Return accepted module aliases and direct function aliases.

    This deliberately catches bypasses such as `import os as _os` and
    `from os import getenv as env`. New raw env reads must still be
    classified in `ALLOWLIST_GETENV_COUNTS`.
    """
    os_module_names = {"os"}
    getenv_names: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "os":
                    os_module_names.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module == "os":
            for alias in node.names:
                if alias.name == "getenv":
                    getenv_names.add(alias.asname or alias.name)
    return os_module_names, getenv_names


def _is_os_getenv(
    node: ast.AST,
    *,
    os_module_names: Set[str],
    getenv_names: Set[str],
) -> bool:
    if not isinstance(node, ast.Call):
        return False
    f = node.func
    if isinstance(f, ast.Name):
        return f.id in getenv_names
    if not isinstance(f, ast.Attribute):
        return False
    if f.attr != "getenv":
        return False
    return isinstance(f.value, ast.Name) and f.value.id in os_module_names


def _scan_getenv() -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for p in _iter_py(_APP_ROOT):
        tree = _parse(p)
        if tree is None:
            continue
        os_module_names, getenv_names = _get_os_getenv_call_names(tree)
        n = sum(
            1
            for node in ast.walk(tree)
            if _is_os_getenv(
                node,
                os_module_names=os_module_names,
                getenv_names=getenv_names,
            )
        )
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


# Rule 3 — `threading.local` legacy adapter inventory


# Inventory esplicito per `threading.local` in `backend/app/`.
# Baseline pulita: `pg_connection.unit_of_work()` non mantiene stato thread-local.
ALLOWLIST_THREADING_LOCAL_COUNTS: Dict[str, int] = {}


def _get_threading_local_call_names(tree: ast.AST) -> Tuple[Set[str], Set[str]]:
    threading_module_names = {"threading"}
    local_names: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "threading":
                    threading_module_names.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module == "threading":
            for alias in node.names:
                if alias.name == "local":
                    local_names.add(alias.asname or alias.name)
    return threading_module_names, local_names


def _is_threading_local(
    node: ast.AST,
    *,
    threading_module_names: Set[str],
    local_names: Set[str],
) -> bool:
    if not isinstance(node, ast.Call):
        return False
    f = node.func
    if isinstance(f, ast.Name):
        return f.id in local_names
    if not isinstance(f, ast.Attribute):
        return False
    if f.attr != "local":
        return False
    return isinstance(f.value, ast.Name) and f.value.id in threading_module_names


def _scan_threading_local() -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for p in _iter_py(_APP_ROOT):
        tree = _parse(p)
        if tree is None:
            continue
        threading_module_names, local_names = _get_threading_local_call_names(tree)
        n = sum(
            1
            for node in ast.walk(tree)
            if _is_threading_local(
                node,
                threading_module_names=threading_module_names,
                local_names=local_names,
            )
        )
        if n:
            counts[_rel(p)] = n
    return counts


def test_threading_local_only_legacy_adapters():
    """Lock/inventory: `threading.local` resta confinato ai legacy
    adapter documentati in `ALLOWLIST_THREADING_LOCAL_COUNTS`.

    Nuovi usi sono sospetti in ottica Go-ready: spesso nascondono
    stato process-local/thread-local che non scala fra repliche.
    Classificare esplicitamente ogni nuovo caso o migrare a
    ContextVar, parametri espliciti, DB/Redis/object storage."""
    found = _scan_threading_local()
    assert found == ALLOWLIST_THREADING_LOCAL_COUNTS, (
        "Drift `threading.local` rilevato in `backend/app/`.\n"
        f"Atteso: {ALLOWLIST_THREADING_LOCAL_COUNTS}\n"
        f"Trovato: {found}\n"
        "Action: se e' un legacy adapter inevitabile, aggiorna "
        "l'inventory con razionale; altrimenti rimuovi lo stato "
        "thread-local dal path runtime Go-ready."
    )


# Rule 4 — concrete executor construction inside service `__init__`


# Inventory dei service che costruiscono executor concreti nel proprio
# `__init__`. Questo pattern rende il wiring meno Go-ready perche' lega
# lifecycle e composizione a un service invece che alla composition root.
#
# Baseline corrente: pulita. `OrchestratorService.__init__` e' migrato
# a injection opzionale; eventuali default builder restano fuori
# dall'inizializzatore e non devono rientrare nel constructor path.
ALLOWLIST_SERVICE_INIT_EXECUTOR_CALLS: Set[Tuple[str, str, str, int]] = set()


def _call_name(node: ast.Call) -> str | None:
    f = node.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return None


def _is_service_class_name(name: str) -> bool:
    return name.endswith("Service") or name == "OrchestratorService"


def _scan_service_init_executor_calls() -> Set[Tuple[str, str, str, int]]:
    hits: Set[Tuple[str, str, str, int]] = set()
    for p in _iter_py(_APP_ROOT):
        tree = _parse(p)
        if tree is None:
            continue
        rel = _rel(p)
        for class_node in ast.walk(tree):
            if not isinstance(class_node, ast.ClassDef):
                continue
            if not _is_service_class_name(class_node.name):
                continue
            init_node = next(
                (
                    item
                    for item in class_node.body
                    if isinstance(item, ast.FunctionDef) and item.name == "__init__"
                ),
                None,
            )
            if init_node is None:
                continue
            for node in ast.walk(init_node):
                if not isinstance(node, ast.Call):
                    continue
                name = _call_name(node)
                if name and name.endswith("Executor"):
                    hits.add((rel, class_node.name, name, node.lineno))
    return hits


def test_service_init_concrete_executor_inventory_locked():
    """Lock: i service non devono introdurre nuove istanziazioni di
    executor concreti dentro `__init__`.

    I domini gia' migrati devono comporre executor nella composition
    root (`app.runtime.services`) e iniettarli. La baseline rimasta e'
    un inventory legacy esplicito; drift in piu' segnala regressione,
    drift in meno richiede aggiornamento del lock."""
    found = _scan_service_init_executor_calls()
    assert found == ALLOWLIST_SERVICE_INIT_EXECUTOR_CALLS, (
        "Drift service `__init__` -> concrete executor rilevato.\n"
        f"Atteso: {sorted(ALLOWLIST_SERVICE_INIT_EXECUTOR_CALLS)}\n"
        f"Trovato: {sorted(found)}\n"
        "Action: sposta la costruzione nella runtime composition "
        "factory e inietta un protocol/dispatch, oppure aggiorna "
        "l'inventory con razionale se il caso resta legacy."
    )


# Rule 5 — `app.db.pg_connection` direct access from routers


# Allowlist: router legacy che importano `app.db.pg_connection`
# direttamente.
#
# Baseline corrente: vuota. I router legacy intermediate/logs sono stati
# migrati a repository/store di dominio; nuovi router devono restare
# dipendenti dal service layer o da repository locali, non da pg_connection.
#
# Migrazioni gia' chiuse (la baseline scende man mano):
# - `app/core/projects/router.py` (BE-GO-READY-CONNECTION-ENGINE-003):
#   project-id lookup migrato a
#   `app.core.projects.db.list_owned_project_ids`.
# - `app/core/imdf/router.py` (BE-GO-READY-CONNECTION-ENGINE-003):
#   project-id-by-group lookup migrato a
#   `app.core.projects.db.list_project_ids_for_group`.
# - `app/core/orchestrator/router.py` (BE-GO-READY-CONNECTION-ENGINE-006):
#   single `DELETE log_runs` migrato al boundary
#   `app.core.orchestrator.log_cleanup.delete_task_log_runs`.
#
# Lock: SET di file allowlistati. Nuovi router NON possono importare
# pg_connection senza esplicita aggiunta + razionale.
ALLOWLIST_PG_CONNECTION_ROUTER_FILES: Set[str] = set()


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


ALLOWLIST_PG_CONNECTION_SERVICE_FILES: Set[str] = set()


def _is_service_path(rel_path: str) -> bool:
    return rel_path.endswith("/service.py") or rel_path.endswith("_service.py")


def _scan_pg_connection_in_services() -> Set[str]:
    hits: Set[str] = set()
    for p in _iter_py(_APP_ROOT):
        rel = _rel(p)
        if not _is_service_path(rel):
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
                if node.module == "app.db" and any(
                    alias.name == "pg_connection" for alias in node.names
                ):
                    hits.add(rel)
                    break
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "app.db.pg_connection":
                        hits.add(rel)
                        break
    return hits


def test_pg_connection_not_imported_from_vertical_services():
    """Vertical services must route persistence through local DB boundaries."""
    found = _scan_pg_connection_in_services()
    extra = found - ALLOWLIST_PG_CONNECTION_SERVICE_FILES
    missing = ALLOWLIST_PG_CONNECTION_SERVICE_FILES - found
    assert not extra, (
        "Service verticali con import diretto di `app.db.pg_connection`: "
        f"{sorted(extra)}.\n"
        "Action: sposta la query in `db.py`, `*_store.py` o "
        "`*_repository.py` del dominio."
    )
    assert not missing, (
        "Service presenti nell'allowlist ma non piu' rilevati: "
        f"{sorted(missing)}. Aggiorna la baseline."
    )


# Rule 6 — `app.main` imports inside shared/unit tests


# Allowlist test file che importano `app.main`. Razionale:
# - `tests2/security/unit/test_c8_observability_contract.py`: il test
#   esplicitamente verifica che il middleware/observability wiring
#   in `main.py` sia integro. Patcha le check di
#   postgres/redis/pool/migrations e spawna un TestClient sull'app
#   reale. Documentato sez. C8 dell'observability contract.
# - `tests2/shared/test_doc_endpoints_policy_contract.py`: locka la
#   policy `_compute_doc_endpoints` e fa uno smoke minimale su
#   `/openapi.json`; importare `app.main` e' il comportamento sotto test.
ALLOWLIST_APP_MAIN_TEST_FILES: Set[str] = {
    "tests2/security/unit/test_c8_observability_contract.py",
    "tests2/shared/test_doc_endpoints_policy_contract.py",
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


# Rule 7 — `raise_typed_http(code="...")` literal vs catalog constant


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
    (config.py, environment.py). Se questo fail, lo scanner e' rotto e i
    drift sopra sono illusori."""
    found = _scan_getenv()
    assert "app/config.py" in found
    assert "app/environment.py" in found


def test_scanner_threading_local_baseline_is_empty():
    """Sanity: lo scanner Rule 3 mantiene pulita la baseline."""
    found = _scan_threading_local()
    assert found == ALLOWLIST_THREADING_LOCAL_COUNTS


def test_scanner_service_init_executor_baseline_is_empty():
    """Sanity: lo scanner Rule 4 mantiene pulita la baseline dei
    service `__init__`."""
    found = _scan_service_init_executor_calls()
    assert found == set()


def test_scanner_pg_connection_router_baseline_is_empty():
    """Sanity: lo scanner Rule 3 mantiene baseline router pulita."""
    found = _scan_pg_connection_in_routers()
    assert found == set()


def test_scanner_app_main_finds_observability_test():
    """Sanity: lo scanner Rule 4 trova il test allowlistato di
    observability."""
    found = _scan_app_main_in_unit_tests()
    assert "tests2/security/unit/test_c8_observability_contract.py" in found
