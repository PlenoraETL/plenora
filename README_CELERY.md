# 🚀 Setup Celery + Redis per ARRICCHISCI

## Prerequisiti

```bash
# Installa dipendenze Python
pip install celery>=5.3.0 redis>=5.0.0
```

## 1. Avvia Redis

### Opzione A: Docker (Consigliato)

```bash
# Avvia solo Redis dal docker-compose
docker-compose up -d redis

# Verifica che sia attivo
docker ps | grep redis
docker logs dataflow-redis
```

### Opzione B: Redis locale

```bash
# macOS
brew install redis
brew services start redis

# Ubuntu/Debian
sudo apt install redis-server
sudo systemctl start redis

# Windows
# Scarica da https://github.com/microsoftarchive/redis/releases
```

### Verifica connessione

```bash
redis-cli ping
# Deve rispondere: PONG
```

## 2. Avvia il Backend (FastAPI)

```bash
# Terminale 1
cd backend
python main.py

# oppure con uvicorn
uvicorn main:app --reload --port 8000
```

## 3. Avvia il Worker Celery

```bash
# Terminale 2
cd backend
celery -A celery_app worker --loglevel=info

# Con più worker (parallelo)
celery -A celery_app worker --loglevel=info --concurrency=4
```

## 4. (Opzionale) Monitoraggio con Flower

```bash
# Installa flower
pip install flower

# Terminale 3
celery -A celery_app flower --port=5555

# Apri http://localhost:5555 per dashboard
```

## Architettura

```
┌─────────────────────────────────────────────────────────────────┐
│                         FRONTEND                                 │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ ArricchisciEditor                                         │   │
│  │  └── ResultNode [Esegui] ──────────────────────────────▶ │   │
│  └──────────────────────────────────────────────────────────┘   │
│                              │                                   │
│                              ▼                                   │
│  POST /api/projects/{id}/enrich-async                           │
│       → Ritorna task_id                                          │
│                              │                                   │
│  GET /api/tasks/{task_id}    │                                   │
│       → Ritorna progress %   ◀────── Polling ogni 1s            │
│                              │                                   │
└─────────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│                         BACKEND (FastAPI)                        │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │ POST /api/tasks         → crea task, ritorna task_id    │    │
│  │ GET  /api/tasks/{id}    → stato + progress %            │    │
│  │ DELETE /api/tasks/{id}  → annulla task                  │    │
│  │ GET /api/tasks/{id}/result → download risultato         │    │
│  └─────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│                       CELERY + REDIS                             │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │ Redis (Broker + Result Backend)                          │    │
│  │ - Coda task                                              │    │
│  │ - Stato/Progress task                                    │    │
│  │ - Risultati task                                         │    │
│  └─────────────────────────────────────────────────────────┘    │
│                              │                                   │
│  ┌───────────┐  ┌───────────┐  ┌───────────┐                    │
│  │ Worker 1  │  │ Worker 2  │  │ Worker N  │  ← scalabile       │
│  │           │  │           │  │           │                    │
│  │ Esegue    │  │ Esegue    │  │ Esegue    │                    │
│  │ enrichment│  │ enrichment│  │ enrichment│                    │
│  └───────────┘  └───────────┘  └───────────┘                    │
└─────────────────────────────────────────────────────────────────┘
```

## API Endpoints

### Esecuzione Asincrona

```bash
# Avvia enrichment asincrono
curl -X POST http://localhost:8000/api/projects/{project_id}/enrich-async \
  -H "Content-Type: application/json" \
  -d '{"force_recalculate": false}'

# Risposta:
{
  "success": true,
  "async": true,
  "task_id": "abc123-def456",
  "status": "pending",
  "message": "Task avviato. Usa GET /api/tasks/{task_id} per monitorare."
}
```

### Monitoraggio Progress

```bash
# Controlla stato
curl http://localhost:8000/api/tasks/{task_id}

# Risposta durante esecuzione:
{
  "task_id": "abc123-def456",
  "status": "progress",
  "progress": 45,
  "message": "Nominatim: 450/1000 righe (440 OK, 10 errori)"
}

# Risposta completamento:
{
  "task_id": "abc123-def456",
  "status": "success",
  "progress": 100,
  "message": "Completato",
  "result": {
    "rows_processed": 1000,
    "output_file": "output.csv",
    "elapsed_time": 125.5
  }
}
```

### Annulla Task

```bash
curl -X DELETE http://localhost:8000/api/tasks/{task_id}
```

### Download Risultato

```bash
curl http://localhost:8000/api/tasks/{task_id}/download -o result.csv
```

## Configurazione

Le configurazioni sono in `config.py`:

```python
# Redis
CELERY_BROKER_URL = "redis://localhost:6379/0"
CELERY_RESULT_BACKEND = "redis://localhost:6379/0"

# Timeout
CELERY_TASK_TIMEOUT = 3600  # 1 ora max
CELERY_RESULT_EXPIRES = 86400  # Risultati salvati 24h

# Enrichment
ENRICHMENT_BATCH_SIZE = 100
ENRICHMENT_MAX_WORKERS = 5
```

## Troubleshooting

### Redis non si connette

```bash
# Verifica Redis è attivo
redis-cli ping

# Verifica porta
netstat -an | grep 6379
```

### Worker non riceve task

```bash
# Controlla log worker
celery -A celery_app worker --loglevel=debug

# Verifica connessione broker
celery -A celery_app inspect ping
```

### Task bloccato

```bash
# Lista task attivi
celery -A celery_app inspect active

# Termina tutti i task
celery -A celery_app purge
```

## Prossimi Passi

1. ✅ Infrastruttura Celery + Redis
2. ⬜ Frontend: Progress bar con polling
3. ⬜ Frontend: Pulsante Annulla
4. ⬜ Template System (Nominatim, OpenAPI)
5. ⬜ Deduplicazione chiamate API
6. ⬜ Preview su campione
