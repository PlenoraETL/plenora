# Deploy profiles

Profili **public** per ambiente di deploy. Selezionati da `deploy.sh` via env
`PLENORA_DEPLOY_PROFILE` o flag `--profile=<name>`. Default: `prod`.

Lo scopo e' rendere `deploy.sh` un wrapper parametrico: stesso script, stessa
logica, parametri per-environment. Cosi' lo stesso codice puo' deployare
verso `prod`, `staging`, o un futuro ambiente cliente senza modifiche.

## Profili presenti

| Profilo | File | Descrizione |
| --- | --- | --- |
| `prod` | `prod.conf` | Server di produzione (lab.plenora.it). Unico profilo concreto in batch 1. |

## Selezione profilo

```bash
# Default (prod)
bash deploy.sh

# Tramite env
PLENORA_DEPLOY_PROFILE=prod bash deploy.sh

# Tramite flag CLI
bash deploy.sh --profile=prod
```

L'ordine di precedenza: `--profile=` flag > env `PLENORA_DEPLOY_PROFILE` > default `prod`.

## Variabili obbligatorie

Lo script fallisce esplicitamente se una di queste manca nel profilo:

| Variabile | Esempio | Note |
| --- | --- | --- |
| `SERVER` | `ubuntu@host.example.com` | Target SSH |
| `REMOTE_BACKEND_PATH` | `/home/ubuntu/plenora-etl/backend` | Path checkout backend sul server |
| `REMOTE_FRONTEND_PATH` | `/home/ubuntu/plenora-etl/frontend` | Path checkout frontend sul server |
| `APP_HEALTH_URL` | `http://127.0.0.1:5004/health` | Endpoint liveness (PG + Redis) |
| `APP_READY_URL` | `http://127.0.0.1:5004/ready` | Endpoint readiness rigoroso |
| `SYSTEMD_UNITS` | `plenora-backend plenora-celery plenora-beat` | Space-separated, restart + is-active |
| `ALLOWED_BRANCHES` | `main` | Allowlist branch deploy permessi |

## Variabili opzionali

| Variabile | Default | Note |
| --- | --- | --- |
| `APP_BASE_URL` | — | URL pubblico (per smoke esterni futuri, non usato in batch 1) |
| `BACKEND_PORT` | — | Solo informativo |
| `READY_TIMEOUT_SEC` | `60` | Timeout polling `/ready` post-restart |
| `READY_POLL_INTERVAL_SEC` | `2` | Intervallo polling |
| `RETAIN_FRONTEND_VERSIONS` | `3` | Retain N versioni `dist-{sha}/` (usato da batch 2 P4) |

## Cosa NON va dentro un profilo

I file `<profile>.conf` sono **versionati** e **public**. Non devono mai contenere:

- **PEM path** → resta env `PLENORA_DEPLOY_PEM` per-operatore
- **AWS access keys / credentials** → mai. Il server usa IAM role attached, gli script backup usano SSM Parameter Store
- **Contenuto `.env`** (`DATABASE_URL`, `JWT_SECRET_KEY`, `PLENORA_ENCRYPTION_KEY`, ecc.) → resta sul server target in `${REMOTE_BACKEND_PATH}/.env`
- **Valori SSM Parameter Store** → mai
- **Token o secret di terze parti** → mai

Review della PR di un profilo deve verificare l'assenza di queste classi.

## Come aggiungere un profilo

```bash
cp deploy/profiles/prod.conf deploy/profiles/<nuovo>.conf
# Edita SERVER / path / URL / ALLOWED_BRANCHES per il nuovo ambiente
git add deploy/profiles/<nuovo>.conf
```

Il primo profilo aggiuntivo richiede infra reale (EC2, DNS, DB, bucket S3, SSM
namespace) — non e' parte di Step 0 batch 1. Vedi
`orchestrate/phase_0_consolidation/phase_0_5x_final_synthesis.md` §6.3 (Step 2).

## Profili locali (non versionati)

I file `*.local.conf` sono ignorati da git (vedi `.gitignore`). Possono essere
usati da un operatore per esperimenti locali (es. simulare un target offline).
Non sostituiscono mai un profilo pubblico per deploy reali.

## Format

I profili sono **shell-source-able** (`source <file>`). Niente nuove dipendenze
tool (no yq, jq, python wrapper). Sintassi: `KEY="value"` per riga, commenti
con `#`. Validazione in `deploy.sh:load_profile`.
