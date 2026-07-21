# Inventario della configurazione

La configurazione applicativa backend ha un solo catalogo di default tipati:
`backend/app/config.py`, classe `Settings`. I valori effettivi arrivano dalle
variabili d'ambiente; non esiste un `configuration.py` o un `config.json`.

## Risultato audit 2026-07-11

- Default backend prima dell'audit: 128.
- Chiavi senza un effetto runtime rimosse: 18.
- Default PostgreSQL prima duplicati in `pg_connection.py` centralizzati: 4.
- Altri default sparsi centralizzati (`DB_SCHEMA`, broker, cloud provider): 3.
- Default canonici dopo l'audit e l'acceleratore XLSX: 120.
- Alias legacy Celery/Redis conservati: 11, per non rompere installazioni
  esistenti. Devono essere rimossi solo con una migrazione di release.

Le 18 chiavi rimosse erano:

```text
DB_CONNECT_TIMEOUT
LOG_DB_CONNECT_TIMEOUT
LOG_DB_BUSY_TIMEOUT
SUPPORTED_DB_TYPES
TASK_PROGRESS_POLL_INTERVAL
TASK_TIMEOUT
TASK_PREVIEW_TIMEOUT
TASK_PREVIEW_HARD_LIMIT
TASK_SCHEDULE_TIMEOUT
SUPPORTED_OUTPUT_FORMATS
DEFAULT_OUTPUT_FORMAT
SUPPORTED_FILE_FORMATS
SUPPORTED_PROJECT_TYPES
MAX_PROJECT_NAME_LENGTH
MAX_CONNECTION_NAME_LENGTH
ENRICHMENT_BATCH_SIZE
ENRICHMENT_MAX_WORKERS
ENRICHMENT_CACHE_TTL
```

Quattordici non avevano consumatori; le quattro chiavi timeout `TASK_*`
comparivano solo nella dashboard/config display ma Dramatiq non le applicava. Modificarle
non produceva quindi alcun effetto. Le enum e i limiti reali restano posseduti
dai domini che li validano, evitando due fonti divergenti.

## Classificazione

| Categoria | Esempi | Regola |
|---|---|---|
| Obbligatoria | `DATABASE_URL`, chiavi JWT/cifratura | Nessun default insicuro; il bootstrap fallisce se manca. |
| Default applicativo | code, timeout, limiti RAM, pool PG | Definito una volta in `Settings.DEFAULTS`, override env opzionale. |
| Required-if-enabled | bucket, credenziali MinIO, log archive | Obbligatoria solo quando la capability e' attiva. |
| Deployment | `ENVIRONMENT`, URL pubblica frontend, network Compose | Fornita da `.env`, Docker o systemd. |
| Legacy | alias `CELERY_*`, `*_REDIS_*` | Compatibilita' temporanea con warning. |
| Test/tooling | credenziali E2E, flag cleanroom | Fuori dal catalogo runtime prodotto. |

Le letture raw dei gate TLS/SSRF restano intenzionalmente nei boundary di
sicurezza: devono vedere l'ambiente senza fallback applicativi. I segreti e i
parametri degli script backup possono essere letti dai relativi entry point,
ma il codice di dominio non deve introdurre nuovi `os.getenv`.

## Frontend

Le uniche variabili compilate nel browser sono dichiarate in
`frontend/src/config/runtimeEnv.js`:

- `VITE_API_URL`;
- `VITE_SENTRY_DSN`;
- `VITE_SENTRY_TRACES_SAMPLE_RATE`;
- `VITE_USE_MOCKS` (forzata off nelle build production).

Le variabili `VITE_*` sono pubbliche nel bundle e non devono contenere segreti.
La pagina superadmin non mantiene piu' un elenco duplicato di chiavi
essenziali: usa il campo `essential` restituito dal backend. Anche la topologia
dev (`runtime._dev_topology`) e il conteggio task (`workers.running_tasks`)
seguono ora il contratto effettivo dell'API.

## Sorgenti di deployment

- Nativo Windows/Linux: `backend/.env` derivato da `backend/.env.example`.
- Docker Windows/Linux: `.env.docker` derivato da `.env.docker.example`.
- Frontend nativo: `frontend/.env.*`.
- Frontend Docker: build args `FRONTEND_VITE_*` in Compose.
- Linux systemd: lo stesso file env backend caricato dalle unit.

I test di contratto impediscono di reintrodurre le chiavi morte, duplicare i
default del pool PG o aggiungere letture `import.meta.env` fuori dal boundary
frontend.
