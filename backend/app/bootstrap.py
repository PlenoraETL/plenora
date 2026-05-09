"""
Bootstrap boundary — single explicit init for the FastAPI app.

"""

from __future__ import annotations

import os


_PSYCOPG2_SSL_ENV_VARS: tuple[str, ...] = (
    "REQUESTS_CA_BUNDLE",
    "SSL_CERT_FILE",
    "CURL_CA_BUNDLE",
)


_BOOT_COMPLETE: bool = False


def is_bootstrapped() -> bool:
    """Return True iff `bootstrap()` has completed successfully.

    Read-only boundary. Writers MUST go through `bootstrap` /
    `teardown`; consumers should treat the value as a transient
    process-local fact (process restart resets to False, then the
    next `bootstrap` flips it).

    Use cases:
      - observability probes (e.g. `/ready` can refuse traffic until
        bootstrap completes — not wired here, owner of `/ready` is
        `app.observability.health` and integrates as a follow-up);
      - test harness (assert the bootstrap path actually flipped
        the flag after a fake-stubbed run).

    The flag does NOT replace the underlying steps' own readiness
    semantics: PG pool init / Redis ping / storage config validity
    each have their own observability surface. `is_bootstrapped`
    is the single source of truth for "the four-step boundary has
    been crossed at least once in this process".
    """
    return _BOOT_COMPLETE


def _repair_psycopg2_ssl_env() -> None:
    """Pop the three SSL env vars that psycopg2 tends to set.

    Must run AFTER `init_all_dbs`. Pre-init the pop is a no-op:
    nothing has set those vars yet. Post-init the env may carry
    stale CA paths that break `requests`/`urllib3` in unrelated code.

    Idempotent (`os.environ.pop(name, None)`).
    """
    for name in _PSYCOPG2_SSL_ENV_VARS:
        os.environ.pop(name, None)


def bootstrap() -> None:
    """Run the four boot side effects in their canonical order.

    See module docstring for contract details.

     on successful completion,
    flips `_BOOT_COMPLETE` to True (read via `is_bootstrapped`).
    On exception from any step the flag stays at its current value —
    a partial bootstrap is NOT marked complete, and a re-run after
    a transient failure can still flip it on success.
    """
    global _BOOT_COMPLETE

    # Local imports keep the module load cheap (no transitive
    # `app.config` / `app.shared.cloud_backends` / runtime DB init
    # work until the function is actually invoked). Important for
    # test ergonomics: tests can `import app.bootstrap` without
    # paying for the full settings/storage/db graph
    from app.config import settings
    from app.core.runtime.db_init import init_all_dbs
    from app.shared.cloud_backends import validate_storage_config
    from app.startup_validation import validate_startup

    # 1. Verifica prerequisiti di sistema (DATA_DIR scrivibile, ecc
    # Interrompe l'avvio con messaggio chiaro se la configurazione e'
    # incompleta
    validate_startup(settings)

    # 2. Coerenza STORAGE_POLICY + provider + bucket + soglie
    # Fail-fast qui evita che un task Celery scopra al primo upload
    # oltre soglia che il bucket non esiste o che il provider non e'
    # implementato
    validate_storage_config()

    # 3. Inizializza database PostgreSQL (pool + tabelle
    init_all_dbs()

    # 4. Fix SSL certificate issue caused by PostgreSQL
    # Deve girare DOPO `init_all_dbs` perche' la pollution dell'env
    # avviene quando psycopg2 apre la prima connessione
    _repair_psycopg2_ssl_env()

    # 5. Mark boundary crossed. Set LAST so any exception from
    # steps 1-4 leaves the flag at its prior value — there is no
    # path that can flip the flag with an incomplete bootstrap
    _BOOT_COMPLETE = True


def teardown() -> None:
    """Run shutdown side effects in their canonical order.

     keeps this deliberately narrow:
    it preserves the current `main.lifespan` behavior by closing only
    the PostgreSQL pool. Do not add new shutdown side effects here
    without a dedicated audit/contract update.

     resets `_BOOT_COMPLETE` so
    `is_bootstrapped` correctly reflects "post-shutdown" state.
    Test harnesses that exercise the full lifecycle can therefore
    observe the flag transitions False → True → False.
    """
    global _BOOT_COMPLETE

    from app.db.pg_connection import close_pool

    close_pool()
    _BOOT_COMPLETE = False
