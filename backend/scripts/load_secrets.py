"""load_secrets.py — wrapper exec che popola os.environ da secrets backend.

Chiude C1-residuo: i systemd unit del backup/test-restore non dipendono
piu' da `backend/.env` su disco. I secret (DATABASE_URL) vivono in un
backend cloud-agnostic (AWS SSM oggi, potenzialmente Azure Key Vault /
GCP Secret Manager domani via CLOUD_PROVIDER env); questo wrapper li
fetcha al task start, li scrive in `os.environ`, poi `os.execv` il
target script.

Pattern: `python3 load_secrets.py /path/to/target.py [args...]`

Il backend cloud e' scelto dalla factory in `cloud_backends.py`
(default: AWS SSM con prefix `/plenora/prod/`). Per migrare cloud:
aggiungi una classe in cloud_backends.py, setta CLOUD_PROVIDER=azure
(o gcp), nessun change qui.

Flusso:
  1. `get_secrets_backend().get_secrets(PREFIX)` — ritorna dict
     {key: value} decifrato
  2. Popola os.environ (setdefault: env pre-esistente NON viene
     sovrascritto — utile in dev)
  3. `os.execv /usr/bin/python3 <target> <args>` — sostituisce il
     processo. systemd Type=oneshot vede lo stesso PID.

Usato da:
  - plenora-backup.service (ExecStart)
  - plenora-backup-test.service (ExecStart)

Exit codes:
    (quelli del target script dopo execv)
    1 errore backend (network, permessi, ecc.)
    2 argomenti invalidi
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict


# Script standalone (invocato da systemd o CLI): aggiungi backend root
# a sys.path cosi' `app.shared.*` e' importabile come package normale.
# Un unico hack al top del modulo, non scattered nelle funzioni.
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))


# Prefix del path su SecretsBackend. Stesso valore per tutti i provider:
# su AWS SSM e' un path `/plenora/prod/...`; su Azure Key Vault sarebbe
# un naming prefix; su GCP Secret Manager idem.
PREFIX = os.getenv("SECRETS_PREFIX", "/plenora/prod/")


def log(level: str, msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[{ts}] [{level}] {msg}", flush=True, file=sys.stderr)


def fetch_secrets() -> Dict[str, str]:
    """Delega al SecretsBackend configurato (factory in app.shared.cloud_backends)."""
    from app.shared.cloud_backends import get_secrets_backend

    try:
        backend = get_secrets_backend()
        return backend.get_secrets(PREFIX)
    except Exception as e:
        log("ERROR", f"Fetch secrets fallito: {type(e).__name__}: {e}")
        sys.exit(1)


def inject_into_environ(secrets: Dict[str, str]) -> int:
    """Popola os.environ. NON sovrascrive key gia' presenti (env ha priorita').

    Ritorna count key iniettati (non contati quelli gia' in env).
    """
    injected = 0
    for key, value in secrets.items():
        if key not in os.environ:
            os.environ[key] = value
            injected += 1
    return injected


def main() -> None:
    if len(sys.argv) < 2:
        log("ERROR", "Uso: load_secrets.py <target-script> [args...]")
        sys.exit(2)

    target = sys.argv[1]
    target_args = sys.argv[2:]

    if not os.path.isfile(target):
        log("ERROR", f"Target script non esiste: {target}")
        sys.exit(2)

    provider = os.getenv("CLOUD_PROVIDER", "aws")
    log("INFO", f"Fetch secrets (provider={provider}, prefix={PREFIX})")
    secrets = fetch_secrets()
    injected = inject_into_environ(secrets)
    log("INFO", f"{len(secrets)} param(s) letti, {injected} iniettati in os.environ")

    if len(secrets) == 0:
        log("WARN", f"Nessun param su {PREFIX} — il target potrebbe fallire su env mancanti.")

    log("INFO", f"Exec: {target} {' '.join(target_args)}".strip())
    # execv sostituisce il processo: il target eredita os.environ.
    # systemd vede lo stesso PID. Type=oneshot gestisce tutto correttamente.
    # Usa `sys.executable` (l'interprete corrente) invece di hardcodare
    # `/usr/bin/python3`: permette a systemd di invocare lo script col
    # Python del venv (che ha psycopg2, boto3 ecc. per run tracking DB).
    python_exe = sys.executable
    os.execv(python_exe, [python_exe, target] + target_args)


if __name__ == "__main__":
    main()
