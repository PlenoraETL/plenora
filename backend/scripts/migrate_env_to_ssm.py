"""
migrate_env_to_ssm.py — migra secret dai .env a AWS SSM Parameter Store.

Scope
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# Secret whitelist: nomi env che vanno in SSM.
# Ordine: il primo che risolve non-vuoto vince (retro-compat per aliasing).
#
# `URBY_ENCRYPTION_KEY` come secondo source per `PLENORA_ENCRYPTION_KEY`
# e' **input-only legacy tolerance**: lo script accetta `.env` non ancora
# normalizzati (es. backup pre-migration o dev env non aggiornati) e
# scrive comunque su SSM sotto il nome canonico. Il RUNTIME applicativo
# non legge piu' l'alias dal 2026-04-23 (D4 chiuso, `app/infrastructure/security/crypto.py`,
# tombstoned in `tests2/crypto/test_crypto_key_sourcing_contract.py`).
# Una volta che tutti i `.env` operativi sono normalizzati a
# `PLENORA_ENCRYPTION_KEY`, l'alias puo' sparire anche da qui.
SECRET_ENV_SPECS: List[Tuple[str, List[str]]] = [
    # (nome canonico SSM, [env names ammessi come source, in ordine di priorita'])
    ("DATABASE_URL",            ["DATABASE_URL"]),
    ("PLENORA_ENCRYPTION_KEY",  ["PLENORA_ENCRYPTION_KEY", "URBY_ENCRYPTION_KEY"]),
    ("JWT_SECRET_KEY",          ["JWT_SECRET_KEY"]),
]


def log(level: str, msg: str) -> None:
    print(f"[{level}] {msg}", flush=True, file=sys.stderr if level in ("ERROR", "WARN") else sys.stdout)


def _value_fingerprint(value: str) -> str:
    """Hash non reversibile per log audit (8 char di sha256 hex).

    Sufficiente a verificare cross-run che il valore migrato e' lo stesso,
    senza esporre il secret in clear su log/journalctl.
    """
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]


def load_env_file(path: Path) -> Dict[str, str]:
    """Legge un file .env minimale (K=V per riga, # commento, no quoting complex).

    Non usa python-dotenv per evitare una dep in piu' e per comportamento
    deterministico: niente espansione variabili, niente multi-line. Se serve
    un .env complesso, preprocessarlo prima.
    """
    out: Dict[str, str] = {}
    if not path.is_file():
        raise FileNotFoundError(f"env file non trovato: {path}")
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            log("WARN", f"{path}:{lineno} riga ignorata (no '='): {line!r}")
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip()
        # Rimuovi quoting esterno se presente ('x' o "x"), mantieni interno
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
            val = val[1:-1]
        out[key] = val
    return out


def resolve_secrets(sources: Dict[str, str]) -> Dict[str, str]:
    """Per ogni SECRET_ENV_SPEC, scegli la prima env source non vuota.

    Ritorna {canonical_name: value}. Skippa i canonical senza source
    risolta (log INFO); non e' un errore perche' alcuni deployment
    possono non avere ancora definito JWT_SECRET_KEY in dev.
    """
    resolved: Dict[str, str] = {}
    for canonical, env_names in SECRET_ENV_SPECS:
        chosen: Optional[str] = None
        for name in env_names:
            v = sources.get(name, "")
            if v:
                chosen = v
                if name != canonical:
                    log("INFO", f"{canonical}: uso alias {name}")
                break
        if chosen is None:
            log("INFO", f"{canonical}: nessuna source definita, skip")
            continue
        resolved[canonical] = chosen
    return resolved


def _ssm_path(prefix: str, name: str) -> str:
    return prefix.rstrip("/") + "/" + name


def migrate(
    prefix: str,
    resolved: Dict[str, str],
    region: str,
    dry_run: bool,
    force: bool,
    kms_key_id: Optional[str],
) -> int:
    """Esegue la migrazione vero AWS SSM Parameter Store.

    Ritorna:
        0 tutti i secret in sync (creati o gia' coincidenti)
        3 drift rilevato su almeno un secret e --force non passato
    """
    try:
        import boto3  # type: ignore
        from botocore.exceptions import ClientError  # type: ignore
    except ImportError:
        log("ERROR", "boto3 non installato. `pip install boto3` o eseguire da venv con deps.")
        return 1

    client = boto3.client("ssm", region_name=region)

    drift_found = False
    for name, value in sorted(resolved.items()):
        full_path = _ssm_path(prefix, name)
        fingerprint = _value_fingerprint(value)

        # 1. Read-before-write per idempotenza.
        try:
            resp = client.get_parameter(Name=full_path, WithDecryption=True)
            existing = resp["Parameter"]["Value"]
            existing_fp = _value_fingerprint(existing)
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            if code == "ParameterNotFound":
                existing = None
                existing_fp = None
            else:
                log("ERROR", f"{full_path}: GetParameter fallita ({code}): {e}")
                return 1

        if existing is None:
            # Create
            action = "DRY-RUN CREATE" if dry_run else "CREATE"
            log("INFO", f"{full_path} [{action}] fp={fingerprint}")
            if not dry_run:
                put_kwargs = {
                    "Name": full_path,
                    "Value": value,
                    "Type": "SecureString",
                    "Overwrite": False,
                }
                if kms_key_id:
                    put_kwargs["KeyId"] = kms_key_id
                client.put_parameter(**put_kwargs)
            continue

        if existing == value:
            log("INFO", f"{full_path} [SKIP] already_in_sync fp={fingerprint}")
            continue

        # Drift: esistente ma diverso.
        if not force:
            log(
                "WARN",
                f"{full_path} [DRIFT] existing_fp={existing_fp} new_fp={fingerprint} "
                f"-> SKIP (passa --force per sovrascrivere, ATTENZIONE: rotazione)",
            )
            drift_found = True
            continue

        action = "DRY-RUN OVERWRITE" if dry_run else "OVERWRITE"
        log("INFO", f"{full_path} [{action}] existing_fp={existing_fp} new_fp={fingerprint}")
        if not dry_run:
            put_kwargs = {
                "Name": full_path,
                "Value": value,
                "Type": "SecureString",
                "Overwrite": True,
            }
            if kms_key_id:
                put_kwargs["KeyId"] = kms_key_id
            client.put_parameter(**put_kwargs)

    return 3 if drift_found else 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Migra secret dai .env a AWS SSM Parameter Store (idempotente).",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        help="Path al file .env da leggere. Se omesso, legge da os.environ.",
    )
    parser.add_argument(
        "--prefix",
        default=os.getenv("SECRETS_PREFIX", "/plenora/prod/"),
        help="Prefix SSM (default: $SECRETS_PREFIX o /plenora/prod/)",
    )
    parser.add_argument(
        "--region",
        default=os.getenv("AWS_REGION", "eu-west-1"),
        help="AWS region (default: $AWS_REGION o eu-west-1)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Stampa il piano senza chiamare AWS PutParameter.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Sovrascrivi param esistenti con valore differente (rotazione).",
    )
    parser.add_argument(
        "--kms-key-id",
        default=os.getenv("SSM_KMS_KEY_ID"),
        help="KMS key per SecureString (default: alias/aws/ssm del provider).",
    )
    args = parser.parse_args()

    # Source: env file se specificato, altrimenti os.environ.
    if args.env_file:
        try:
            sources = load_env_file(args.env_file)
            log("INFO", f"Caricate {len(sources)} key(s) da {args.env_file}")
        except FileNotFoundError as e:
            log("ERROR", str(e))
            return 2
    else:
        sources = dict(os.environ)
        log("INFO", "Source: os.environ (pass --env-file per usare un file)")

    resolved = resolve_secrets(sources)
    if not resolved:
        log("WARN", "Nessun secret risolto: niente da migrare.")
        return 0

    log(
        "INFO",
        f"Piano: {len(resolved)} secret(s) verso prefix={args.prefix!r} "
        f"region={args.region!r} dry_run={args.dry_run} force={args.force}",
    )
    for name, value in sorted(resolved.items()):
        log("INFO", f"  - {name} (fp={_value_fingerprint(value)})")

    return migrate(
        prefix=args.prefix,
        resolved=resolved,
        region=args.region,
        dry_run=args.dry_run,
        force=args.force,
        kms_key_id=args.kms_key_id,
    )


if __name__ == "__main__":
    sys.exit(main())
