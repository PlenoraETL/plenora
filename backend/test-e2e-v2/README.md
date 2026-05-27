# Plenora Test E2E V2

Stack Docker autoconsistente per le fixture di test. Questa e' la suite E2E
attiva e sostituisce il vecchio harness `backend/test-e2e`.

## Servizi

| Container | Porta host | Porta interna | Uso |
| --- | ---: | ---: | --- |
| `plenora-test-postgres` | `55432` | `5432` | PostgreSQL/PostGIS con seed SQL |
| `plenora-test-mysql` | `53306` | `3306` | MySQL 8.4 con seed SQL |
| `plenora-test-sqlserver` | `51433` | `1433` | SQL Server 2022 Developer con seed SQL |
| `plenora-test-oracle` | `51521` | `1521` | Oracle Free 23 full con seed SQL anche Spatial |
| `plenora-test-fake-rest` | `58080` | `8080` | API REST fake |
| `plenora-e2e-minio` | `59000` | `9000` | MinIO object storage per profilo hybrid/object storage |
| `plenora-test-ftp` | `2121` | `21` | FTP con fixture file |
| `plenora-test-sftp` | `2222` | `22` | SFTP con fixture file |

FTP e SFTP restano due container separati per avere log, healthcheck e
restart indipendenti. Sono pero' parte dello stesso stack e montano gli stessi
file fixture in `/home/tester/fixtures`.

MySQL, SQL Server e Oracle sono dietro profilo `cross-db`: non partono con il
solo `docker compose up` per evitare download e startup pesanti quando si
testano solo Postgres/REST/file transfer. Oracle usa l'immagine `full` per
avere anche i tipi Spatial `MDSYS.SDO_GEOMETRY`.

## Avvio

Comando consigliato da root repo: ricrea le fixture, avvia anche il profilo
cross-db e collega backend/worker/beat Plenora alla rete e2e.

```bash
bash backend/test-e2e-v2/recreate.sh
```

Per il set leggero senza MySQL, SQL Server e Oracle:

```bash
bash backend/test-e2e-v2/recreate.sh --no-cross-db
```

Lo script elimina solo i volumi della suite e2e. Non elimina i volumi
applicativi Plenora.

```bash
cd backend/test-e2e-v2
cp .env.example .env
docker compose up -d --build
docker compose ps
```

Per avviare anche i database cross-dialect:

```bash
cd backend/test-e2e-v2
docker compose --profile cross-db up -d --build
docker compose ps
```

La rete Docker viene creata automaticamente come `plenora-test`.

## Endpoint interni Docker

Quando backend/worker Plenora sono collegati alla rete `plenora-test`, i
servizi sono raggiungibili con questi host:

```text
plenora-test-postgres:5432
plenora-test-mysql:3306
plenora-test-sqlserver:1433
plenora-test-oracle:1521/plenora_test
plenora-test-fake-rest:8080
plenora-e2e-minio:9000
plenora-test-ftp:21
plenora-test-sftp:22
```

Per collegare lo stack Plenora alla rete test senza usare
`docker-compose.override.yml` automatici:

```bash
cd /home/marcob/Scrivania/plenora
docker compose -f docker-compose.yml \
  -f backend/test-e2e-v2/docker-compose.plenora-app.yml \
  up -d --no-deps backend worker
```

L'override assegna al DB applicativo l'alias `plenora-db`, cosi' il backend
continua a usare il suo Postgres interno anche se nella rete test esiste un
altro servizio PostgreSQL.

Credenziali fixture:

```text
Postgres: plenora / plenora_test_pwd / plenora_test
MySQL:    plenora / plenora_test_pwd / plenora_test
SQLServer: sa / PlenoraTest!2026 / plenora_test
Oracle:   plenora / plenora_test_pwd / plenora_test
FTP:      tester / plenora_ftp_pwd
SFTP:     tester / plenora_sftp_pwd
```

## Reset dati

```bash
cd backend/test-e2e-v2
docker compose down -v
docker compose up -d --build
```

`down -v` elimina i volumi test e forza il reload dei seed SQL.
