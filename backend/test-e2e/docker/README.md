# Plenora E2E — Docker stack

Stack docker dei **servizi che plenora consuma** (sorgenti dati,
destinazioni, API esterne fake). **NON include l'app plenora**:
backend/worker/beat/frontend/postgres-applicativo/redis stanno nel
compose root del repo (`/docker-compose.yml`).

I 2 stack si parlano via la network esterna **`plenora-e2e`** che va
creata una volta sola:

```fish
docker network create plenora-e2e
```

Poi accendi i 2 stack indipendentemente (l'ordine non conta):

```fish
# stack app plenora
cd /home/marcob/Scrivania/plenora && docker compose up -d

# stack fixture e2e (questo)
cd /home/marcob/Scrivania/plenora/backend/test-e2e/docker && docker compose up -d
```

Dall'app plenora, le sorgenti dati di test sono raggiungibili via DNS
interno docker (es. `plenora-e2e-postgres:5432`, `plenora-e2e-mysql:3306`,
`plenora-e2e-fake-rest:8080`).

⚠️ **Distinzione importante**:
- `postgres` (compose root) = DB applicativo plenora (users, groups,
  projects, schedules, audit_log).
- `plenora-e2e-postgres` (questo compose) = DB **sorgente dati di test**
  con seed (regions, cities, customers, orders, ...). Plenora si
  collega qui via `connection_id`, come per qualsiasi DB esterno.
- `redis` (compose root) = unico, condiviso tra app e test. Niente
  duplicato qui.

## Servizi

| Servizio        | Image                                      | Porta host | Profilo       | Note |
|-----------------|--------------------------------------------|-----------:|---------------|------|
| `postgres`      | `postgis/postgis:17-3.5`                   |       5433 | `db`, default | Sostituisce il `plenora-test-postgres` storico. PostGIS attivo. Seed auto via `postgres/init/`. |
| `mysql`         | `mysql:8.4`                                |       3307 | `db`          | SRID nativo + CTE ricorsiva. Seed auto via `mysql/init/`. |
| `sqlserver`     | `mcr.microsoft.com/mssql/server:2022-latest` |     1434 | `db`          | Seed via container sidecar `sqlserver-init` (entrypoint MSSQL non supporta initdb.d). |
| `oracle`        | `gvenzl/oracle-free:23-slim-faststart`     |       1522 | `db-heavy`    | Spatial / Locator inclusi. Pesante (~4 GB). Profilo separato. |
| `minio`         | `minio/minio:latest`                       | 9000, 9001 | `cloud`       | S3-compatibile. Bucket pre-creato via `minio-init`. |
| `azurite`       | `mcr.microsoft.com/azure-storage/azurite`  | 10000-2    | `cloud`       | Azure Blob/Queue/Table fake ufficiale Microsoft. |
| `localstack`    | `localstack/localstack:latest`             |       4566 | `cloud`, `full` | AWS SSM Parameter Store + S3 fake. Init via `localstack/init/01_seed_ssm.sh`. |
| `ftp`           | `delfer/alpine-ftp-server:latest`          | 21, 21000-21010 | `files`  | Plain FTP. User `tester` / password in `.env`. |
| `sftp`          | `atmoz/sftp:alpine`                        |       2222 | `files`       | OpenSSH SFTP. Auth: password OR SSH key (vedi `sftp/keys/`). |
| `fake-rest`     | build da `../fake-rest/`                   |       8080 | `core`, default | FastAPI single-process che emula auth, pagination, errori, ArcGIS, ecc. |

Le porte host sono tutte spostate (`+1` rispetto al default) per non
collidere con eventuali servizi locali sulle porte standard.

## Profili

| Profilo     | Cosa avvia                                                              |
|-------------|-------------------------------------------------------------------------|
| `default`   | `postgres` + `redis` + `fake-rest` (minimo per la maggior parte dei test) |
| `db`        | tutti i DB tranne Oracle                                                |
| `db-heavy`  | aggiunge Oracle (~4 GB image, startup ~3 min)                           |
| `cloud`     | `minio` + `azurite`                                                     |
| `files`     | `ftp` + `sftp`                                                          |
| `full`      | tutto                                                                   |

Esempi:

```fish
# stack minimo (uso quotidiano)
docker compose -f test-e2e/docker/docker-compose.yml up -d

# DB cross-dialect (no Oracle)
docker compose -f test-e2e/docker/docker-compose.yml --profile db up -d

# tutto
docker compose -f test-e2e/docker/docker-compose.yml --profile full up -d
```

## Credenziali

Tutte le credenziali sono in `.env` (copia da `.env.example`). Sono
fisse e checkate-in: questo stack è **solo per test**, non riusarlo in
contesti reali.

## Seed dei dati

Il seed SQL si trova in `test-e2e/sql/<dialect>/`. I container DB lo
caricano automaticamente al primo avvio (volume vuoto). Per ricaricare:

```fish
docker compose -f test-e2e/docker/docker-compose.yml down -v
docker compose -f test-e2e/docker/docker-compose.yml up -d
```

`down -v` rimuove i volumi → al prossimo `up` il seed viene rieseguito.

## Fixture file pre-popolate

I fixture di `test-e2e/files/` sono auto-disponibili nei container di
trasporto file:

| Container | Path interno                            | Modalità   |
|-----------|-----------------------------------------|------------|
| `ftp`     | `/home/tester/fixtures/`                | bind, RO   |
| `sftp`    | `/home/tester/fixtures/`                | bind, RO   |
| `minio`   | `s3://plenora-test/fixtures/`           | `mc cp` da `minio-init` (idempotente) |
| `azurite` | _vuoto_                                 | i test caricano via SDK Python |

Esempio: dal codice plenora, il connettore SFTP punta a
`sftp://tester@plenora-e2e-sftp:22/fixtures/csv/customers_basic.csv`.

## Healthcheck

Ogni servizio ha un healthcheck. Lo stack è pronto quando tutti i
container sono `(healthy)`:

```fish
docker compose -f test-e2e/docker/docker-compose.yml ps
```

## LocalStack — SSM Parameter Store & S3 buckets

Il container `localstack` (profili `cloud`, `full`) emula i servizi AWS
usati da plenora per il caricamento secret e i backup admin. Endpoint
unico: `http://plenora-e2e-localstack:4566` (host: `http://localhost:4566`).

### SSM Parameter Store (seedato via `localstack/init/01_seed_ssm.sh`)

| Nome parametro                  | Tipo           | Valore               |
|---------------------------------|----------------|----------------------|
| `/plenora/test/db_password`     | `SecureString` | `plenora_test_pwd`   |
| `/plenora/test/api_key`         | `SecureString` | `PLENORA_TEST_KEY`   |
| `/plenora/test/oauth_secret`    | `SecureString` | `plenora_secret`     |

Prefisso: `SSM_PARAMETER_PREFIX=/plenora/test/`.

### Bucket S3

| Bucket                | Uso                                  |
|-----------------------|--------------------------------------|
| `plenora-backups`     | Backup admin (DB dump, snapshot, ...) |
| `plenora-log-archive` | Archivio log applicativi             |

### Credenziali AWS

Tutto il valori "test"/"test" — sono i default LocalStack. Vedi
`.env.example` (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`,
`AWS_ENDPOINT_URL`, ...).

## SFTP — autenticazione

Il container `sftp` accetta entrambe le modalità:

- **Password**: user `${SFTP_USER}` / pass `${SFTP_PASS}` (default
  `tester` / `plenora_sftp_pwd`).
- **Chiave SSH**: chiavi pubbliche montate in `/home/tester/.ssh/keys/`
  (caricate automaticamente da `atmoz/sftp`). Le coppie chiave
  pubblica/privata sono in `sftp/keys/`:
  - `id_rsa_test` / `id_rsa_test.pub` — RSA 2048, **nessuna passphrase**
  - `id_rsa_test_passphrase` / `id_rsa_test_passphrase.pub` — RSA 2048,
    passphrase `plenora_passphrase_test`

Le chiavi sono **fake test-only**, generate per questo repo e committate
intenzionalmente. Vedi [sftp/keys/README.md](sftp/keys/README.md).

## Cosa manca / opzionale

- **fake-gcs-server** (Google Cloud Storage): non incluso, da aggiungere
  se i test toccano GCS. Image: `fsouza/fake-gcs-server`.
- **MailHog**: non incluso. Plenora attualmente non invia email dal core.
- **SharePoint**: emulato dentro il `fake-rest` (route dedicate), non
  containerizzato a parte.
