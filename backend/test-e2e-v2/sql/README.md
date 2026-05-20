# PostgreSQL Test Seed

Questa V2 contiene per ora solo il seed PostgreSQL/PostGIS usato dal
container `plenora-test-postgres`.

## File

| File | Uso |
| --- | --- |
| `postgres/00_schema.sql` | schema e tabelle |
| `postgres/01_seed.sql` | dataset deterministico base + edge case |
| `postgres/02_volume.sql` | righe volume per paginazione/performance |

Il compose monta `./sql/postgres` in `/seed` e lo script
`../postgres/init/01_load.sh` carica i tre file al primo avvio del volume.

Per ricaricare il seed:

```bash
cd backend/test-e2e-v2
docker compose down -v
docker compose up -d
```
