# First PC Validation Pack

This is the operator copy/paste pack for Marco's first clean Linux PC test.

Use it after reading:

- [Non-Docker install](install-non-docker.md)
- [Docker install](install-docker.md)
- [First PC validation report template](first-pc-validation-report-template.md)

## Order

1. Clone the repo and record the commit hash.
2. Run the non-Docker install path.
3. Run the backend smoke checks.
4. Start the frontend and verify it reaches the backend.
5. Run Docker validation.
6. Fill the report template with exact command output.

## Non-Docker Commands

```bash
git clone https://github.com/PlenoraETL/plenora.git ~/plenora
cd ~/plenora
git rev-parse HEAD
```

Then follow [install-non-docker.md](install-non-docker.md) sections 1-8.

Minimum outputs to paste back:

```bash
python3.11 --version
psql --version
redis-server --version
node --version
npm --version
curl -fsS http://127.0.0.1:5004/health
curl -fsS http://127.0.0.1:5004/ready
cd ~/plenora/backend
source venv/bin/activate
python -c "from workers.celery_app import celery_app; print(celery_app.send_task('cleanroom.ping').get(timeout=30))"
```

Expected Celery output: `pong`.

## Docker Commands

Run only after the non-Docker path has produced useful output.

```bash
cd ~/plenora
cp .env.docker.example .env.docker
python3 -c "from cryptography.fernet import Fernet; print('PLENORA_ENCRYPTION_KEY=' + Fernet.generate_key().decode())" >> .env.docker
python3 -c "import secrets; print('JWT_SECRET_KEY=' + secrets.token_urlsafe(48))" >> .env.docker
chmod 600 .env.docker
docker compose config
docker compose build backend
docker compose up -d postgres redis
docker compose up -d --wait backend
docker compose up -d worker beat
curl -fsS http://127.0.0.1:5004/health
curl -fsS http://127.0.0.1:5004/ready
docker compose exec worker python -c "from workers.celery_app import celery_app; print(celery_app.send_task('cleanroom.ping').get(timeout=30))"
docker compose ps
```

Expected Celery output: `pong`.

## Stop Conditions

Stop and paste output when:

- `docker compose config` fails;
- backend image build fails;
- `/health` fails;
- `/ready` returns 503;
- `cleanroom.ping` times out;
- frontend cannot reach backend.

Do not keep retrying with untracked local changes. The value of this test is
the exact failure output from a clean machine.

## Cleanup

Non-destructive:

```bash
docker compose stop
```

Destructive, only for disposable test state:

```bash
docker compose down -v
rm -f .env.docker
```
