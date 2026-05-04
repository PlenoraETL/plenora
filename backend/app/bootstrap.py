"""Bootstrap boundary — single explicit init for the FastAPI app.

BE-GO-READY-BOOTSTRAP-ENGINE-002-EXPLICIT-BOOTSTRAP-FUNCTION.

Replaces the implicit top-level init block in `app.main` with one
named call: `bootstrap()`. The four side-effecting steps that the
app needs at startup live here in their canonical order:

1. `settings.validate_startup()` — DATA_DIR writability, temp
   cleanup, Redis reachability gate (`SystemExit(1)` on failure).
2. `validate_storage_config()` — coherence of `STORAGE_POLICY` ×
   `CLOUD_PROVIDER` × bucket × thresholds × `LOG_ARCHIVE_*`
   (`StorageConfigError` on incoherent combos).
3. `init_all_dbs()` — PG pool init + idempotent migration apply
   (re-raises `psycopg2.Error` on connection failure).
4. `_repair_psycopg2_ssl_env()` — pop the three SSL-related env
   vars that psycopg2 tends to leak into the process environment.
   Must run AFTER step 3, otherwise the pop is a no-op.

Boundary contract
=================
- **Input**: none. Reads `app.config.settings` and the existing
  app-level helpers; no parameters, no return.
- **Output**: `None`. Side effects only.
- **Exceptions**: preserved exactly as raised by the underlying
  helpers. `bootstrap()` does not wrap, log, or remap.
- **Idempotency**: `validate_startup()` and `init_all_dbs()` are
  designed to be re-runnable; `_repair_psycopg2_ssl_env()` uses
  `pop(..., None)` and is naturally idempotent. So `bootstrap()`
  itself is idempotent in the same sense as its constituents.
- **No I/O outside this boundary**: callers that need to test app
  behavior without DB/Redis should stub `bootstrap` (see
  `tests2/conftest.py`), not the four constituents individually.

Out of scope (per task brief)
=============================
- No DI container introduced.
- No router registry, no `register_routers(app)` helper.
- No exception-handler extraction.
- No health-endpoint relocation.
- Teardown symmetry is intentionally narrow: `teardown()` owns the
  existing PostgreSQL pool close side effect and nothing else.
"""

from __future__ import annotations

import os


_PSYCOPG2_SSL_ENV_VARS: tuple[str, ...] = (
    "REQUESTS_CA_BUNDLE",
    "SSL_CERT_FILE",
    "CURL_CA_BUNDLE",
)


def _repair_psycopg2_ssl_env() -> None:
    """Pop the three SSL env vars that psycopg2 tends to set.

    Must run AFTER `init_all_dbs()`. Pre-init the pop is a no-op:
    nothing has set those vars yet. Post-init the env may carry
    stale CA paths that break `requests`/`urllib3` in unrelated code.

    Idempotent (`os.environ.pop(name, None)`).
    """
    for name in _PSYCOPG2_SSL_ENV_VARS:
        os.environ.pop(name, None)


def bootstrap() -> None:
    """Run the four boot side effects in their canonical order.

    See module docstring for contract details.
    """
    # Local imports keep the module load cheap (no transitive
    # `app.config` / `app.shared.cloud_backends` / `app.db.db_service`
    # work until the function is actually invoked). Important for
    # test ergonomics: tests can `import app.bootstrap` without
    # paying for the full settings/storage/db graph.
    from app.config import settings
    from app.db.db_service import init_all_dbs
    from app.shared.cloud_backends import validate_storage_config

    # 1. Verifica prerequisiti di sistema (DATA_DIR scrivibile, ecc.).
    # Interrompe l'avvio con messaggio chiaro se la configurazione e'
    # incompleta.
    settings.validate_startup()

    # 2. Coerenza STORAGE_POLICY + provider + bucket + soglie.
    # Fail-fast qui evita che un task Celery scopra al primo upload
    # oltre soglia che il bucket non esiste o che il provider non e'
    # implementato.
    validate_storage_config()

    # 3. Inizializza database PostgreSQL (pool + tabelle).
    init_all_dbs()

    # 4. Fix SSL certificate issue caused by PostgreSQL.
    # Deve girare DOPO `init_all_dbs()` perche' la pollution dell'env
    # avviene quando psycopg2 apre la prima connessione.
    _repair_psycopg2_ssl_env()


def teardown() -> None:
    """Run shutdown side effects in their canonical order.

    BE-GO-READY-BOOTSTRAP-ENGINE-006 keeps this deliberately narrow:
    it preserves the current `main.lifespan()` behavior by closing only
    the PostgreSQL pool. Do not add new shutdown side effects here
    without a dedicated audit/contract update.
    """
    from app.db.pg_connection import close_pool

    close_pool()
