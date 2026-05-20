# Plenora E2E v2 Host Tests

Questi test si lanciano dall'host, non dentro il container backend.

Struttura:

- `functional/`: test funzionali API/runtime Python.
- `test-playwright/`: test browser reali Playwright.

I test funzionali usano:

- API HTTP Plenora: default `http://127.0.0.1:5004/api`
- Docker CLI verso Postgres applicativo per preparare fixture `output_contract`

## Credenziali

Crea un file locale:

```bash
cp backend/test-e2e-v2/test/.env.example backend/test-e2e-v2/test/.env
```

Poi compila una delle due opzioni.

Token pronto:

```bash
PLENORA_E2E_TOKEN=...
```

Oppure username/password:

```bash
PLENORA_E2E_USERNAME=...
PLENORA_E2E_PASSWORD=...
```

Il file `.env` è ignorato da git.

## Variabili opzionali

```bash
export PLENORA_E2E_API_BASE_URL="http://127.0.0.1:5004/api"
export PLENORA_E2E_DB_CONTAINER="plenora-postgres-1"
export PLENORA_E2E_DB_USER="plenora"
export PLENORA_E2E_DB_NAME="plenora"
export PLENORA_E2E_KEEP_ON_SUCCESS=1  # non cancellare i progetti se il test passa
```

## Esecuzione

Suite funzionale completa:

```bash
python backend/test-e2e-v2/test/run_all.py
```

Suite funzionale con database cross-dialect, dopo aver avviato
`docker compose --profile cross-db up -d --build` da `backend/test-e2e-v2`:

```bash
PLENORA_E2E_CROSS_DB=1 python backend/test-e2e-v2/test/run_all.py
```

Solo un dialect:

```bash
PLENORA_E2E_CROSS_DB_DIALECTS=mysql python backend/test-e2e-v2/test/functional/test_307.py
PLENORA_E2E_CROSS_DB_DIALECTS=mssql python backend/test-e2e-v2/test/functional/test_308.py
PLENORA_E2E_CROSS_DB_DIALECTS=oracle python backend/test-e2e-v2/test/functional/test_309.py
```

Singolo test funzionale:

```bash
python backend/test-e2e-v2/test/functional/test_001.py
```

Suite Playwright:

```bash
frontend/node_modules/.bin/playwright test -c backend/test-e2e-v2/test/test-playwright/playwright.config.js
```

Se un test passa, cancella i progetti che ha creato. Se fallisce, li lascia nel
database e stampa gli ID per ispezionarli dalla GUI.
