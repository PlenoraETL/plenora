# 🔌 ARRICCHISCI - Piano di Sviluppo Completo

**Data:** 2026-01-22  
**Stato attuale:** ~70%  
**Target:** 95% per conferenza ESRI (13-14 maggio 2026)

---

## 1. Panoramica

ARRICCHISCI è il modulo di DataFlow Manager per l'arricchimento dati da fonti esterne. Permette di aggiungere informazioni a dataset esistenti tramite chiamate API, esecuzione di script, o import da repository remoti.

### 1.1 Macro Categorie

| # | Categoria | Descrizione | Esempi |
|---|-----------|-------------|--------|
| 1 | **🌐 API REST** | Chiamate HTTP a servizi esterni | Nominatim, RE-VAL, API custom |
| 2 | **⚙️ Eseguibili** | Script/programmi locali | Python .py, .exe RETURN, modelli ML |
| 3 | **📁 Data Sources** | Dati da repository remoti | SharePoint, FTP, S3, Azure Blob |

### 1.2 Tempi di Esecuzione Tipici

| Tipo | Tempo per operazione | Gestione richiesta |
|------|----------------------|-------------------|
| API REST | 100ms - 5s per riga | Rate limiting, batch parallelo |
| Eseguibili | Secondi → Minuti/Ore | Task asincrono, progress, cancellazione |
| Data Sources | Variabile (download) | Streaming, progress |

---

## 2. Architettura

### 2.1 Schema Generale

```
┌─────────────────────────────────────────────────────────────────┐
│                         FRONTEND                                 │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ ArricchisciEditor (Canvas)                                │   │
│  │  ├── ProjectSourceNode / FileSourceNode                   │   │
│  │  ├── EnrichNode (REST | Eseguibile | DataSource)         │   │
│  │  └── ResultNode                                           │   │
│  └──────────────────────────────────────────────────────────┘   │
│                              │                                   │
│  [Esegui] ──────────────────▶│                                  │
│  [Progress Bar] ◀────────────│ (WebSocket/Polling)              │
│  [Annulla] ─────────────────▶│                                  │
└─────────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│                         BACKEND API                              │
│  POST /api/tasks              → crea task, ritorna task_id      │
│  GET  /api/tasks/{id}/status  → stato + progress %              │
│  DELETE /api/tasks/{id}       → annulla task                    │
│  GET /api/tasks/{id}/result   → download risultato              │
└─────────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│                       CELERY + REDIS                             │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │ Redis (Broker + Result Backend)                          │    │
│  └─────────────────────────────────────────────────────────┘    │
│                              │                                   │
│  ┌───────────┐  ┌───────────┐  ┌───────────┐                    │
│  │ Worker 1  │  │ Worker 2  │  │ Worker N  │  ← scalabile       │
│  │ (REST)    │  │ (Exec)    │  │ (Data)    │                    │
│  └───────────┘  └───────────┘  └───────────┘                    │
└─────────────────────────────────────────────────────────────────┘
                               │
            ┌──────────────────┼──────────────────┐
            ▼                  ▼                  ▼
     ┌────────────┐    ┌────────────┐    ┌────────────┐
     │ 🌐 REST    │    │ ⚙️ Eseguib.│    │ 📁 DataSrc │
     │ Nominatim  │    │ Python .py │    │ SharePoint │
     │ RE-VAL     │    │ .exe RETURN│    │ FTP/SFTP   │
     │ OpenAPI    │    │ R .R       │    │ S3/Azure   │
     └────────────┘    └────────────┘    └────────────┘
```

### 2.2 Struttura File

```
backend/
├── celery_app.py              # Config Celery
├── tasks/
│   ├── __init__.py
│   ├── rest_task.py           # Task chiamate REST
│   ├── executable_task.py     # Task eseguibili
│   └── datasource_task.py     # Task download
├── services/
│   ├── task_service.py        # CRUD task
│   └── template_service.py    # Gestione template
└── models/
    └── task.py                # Task model

data/
├── api_templates/
│   ├── _builtin/              # Nominatim, OSRM...
│   └── _imported/             # OpenAPI importati (RE-VAL)
└── executables/
    └── _registry/             # Script registrati

frontend/
├── components/
│   ├── ApiEnrichNode.jsx      # ✅ Esistente
│   ├── ExecutableNode.jsx     # Da creare
│   ├── DataSourceNode.jsx     # Da creare
│   └── TemplateLibrary/       # Da creare
│       ├── TemplateModal.jsx
│       ├── OpenAPIImport.jsx
│       └── TemplateCard.jsx
└── pages/
    └── ApiConnectionManager.jsx  # ✅ Esistente (da estendere)

docker-compose.yml             # + Redis service
```

---

## 3. Sistema Template

### 3.1 Tipi di Import

| Tipo | Uso | Come funziona |
|------|-----|---------------|
| **OpenAPI Import** | API enterprise (RE-VAL) | Importi schema, hai tutti gli endpoint |
| **Template Singolo** | Servizi pubblici (Nominatim) | Preconfigurato per 1 endpoint |

### 3.2 UI Template Library

```
📥 IMPORTA API
├── 📄 Da OpenAPI Schema
│   └── (URL o file JSON) → genera connettore multi-endpoint
│
└── 📦 Da Template Predefinito
    ├── 🌍 Geocoding
    │   ├── Nominatim (indirizzo → coordinate)
    │   └── Nominatim Reverse (coordinate → indirizzo)
    ├── 🚗 Routing
    │   └── OSRM (percorso A → B)
    └── 🏠 Importati da OpenAPI
        └── DATI IMMOBILIARI API (N endpoint)
```

### 3.3 Schema Template Completo

```json
{
  "_meta": {
    "schema_version": "1.0",
    "template_id": "nominatim_geocoding",
    "template_type": "single_endpoint | openapi_import",
    "category": "geocoding | routing | catasto | custom",
    "icon": "🌍",
    "tags": ["osm", "coordinate", "indirizzo"],
    "author": "URBY-ETL",
    "version": "1.0.0",
    "created_at": "2026-01-22",
    "official": true
  },
  
  "display": {
    "name": "Nominatim - Geocoding",
    "short_description": "Converte indirizzi in coordinate GPS",
    "long_description": "Servizio gratuito di OpenStreetMap...",
    "use_cases": [
      "Geocodifica indirizzi clienti",
      "Posizionamento immobili su mappa"
    ],
    "limitations": [
      "Max 1 richiesta/secondo (gratuito)"
    ],
    "docs_url": "https://nominatim.org/release-docs/latest/api/Search/"
  },

  "config": {
    "api_type": "enricher | generator",
    "endpoint": {
      "url": "https://nominatim.openstreetmap.org/search",
      "method": "GET"
    },
    "auth": {
      "type": "none | api_key | oauth2_password",
      "key_name": "X-API-Key",
      "location": "header | query"
    },
    "options": {
      "rate_limit": 1,
      "timeout": 30,
      "retry_on_error": true,
      "retry_max": 3,
      "retry_backoff": 2
    }
  },

  "parameters": [
    {
      "name": "q",
      "label": "Indirizzo completo",
      "description": "Indirizzo da geocodificare",
      "type": "string",
      "required": true,
      "example": "Piazza del Duomo, Milano",
      "mapping_hint": "indirizzo|address|via|street",
      "hidden": false,
      "default": null
    }
  ],

  "output_mapping": [
    {
      "path": "[0].lat",
      "column": "lat",
      "type": "real",
      "description": "Latitudine WGS84"
    },
    {
      "path": "[0].lon",
      "column": "lon",
      "type": "real",
      "description": "Longitudine WGS84"
    }
  ],

  "test_data": {
    "q": "Piazza del Duomo, Milano"
  },
  
  "openapi_schema": null
}
```

### 3.4 Schema OpenAPI Import

```json
{
  "_meta": {
    "schema_version": "1.0",
    "template_id": "reval_dati_immobiliari",
    "template_type": "openapi_import",
    "category": "catasto",
    "icon": "🏠",
    "source_url": "http://analisi.re-val.it:5001/openapi.json"
  },
  
  "display": {
    "name": "DATI IMMOBILIARI API",
    "short_description": "API catastali e OMI di RE-VAL/UeO"
  },

  "config": {
    "base_url": "http://analisi.re-val.it:5001",
    "auth": {
      "type": "api_key",
      "key_name": "X-API-Key",
      "location": "header"
    }
  },
  
  "openapi_schema": { "...schema completo..." }
}
```

---

## 4. Eseguibili

### 4.1 Protocollo Esecuzione

```
┌─────────────┐         ┌─────────────┐         ┌─────────────┐
│   URBY-ETL  │         │  ESEGUIBILE │         │   OUTPUT    │
│             │         │  (py/exe/R) │         │             │
│  DataFrame  │────────▶│             │────────▶│  DataFrame  │
│             │  JSON   │  Elabora    │  JSON   │  arricchito │
└─────────────┘         └─────────────┘         └─────────────┘

Input:  /temp/input_{task_id}.json
Output: /temp/output_{task_id}.json
```

### 4.2 Formato Input/Output

```json
// Input
{
  "task_id": "abc123",
  "data": [
    {"id": 1, "indirizzo": "Via Roma 1", "citta": "Milano"},
    {"id": 2, "indirizzo": "Via Nazionale 15", "citta": "Roma"}
  ],
  "parameters": {
    "modello": "predizione_valore_v2",
    "soglia": 0.8
  }
}

// Output
{
  "task_id": "abc123",
  "success": true,
  "data": [
    {"id": 1, "indirizzo": "Via Roma 1", "valore_stimato": 450000, "confidence": 0.92},
    {"id": 2, "indirizzo": "Via Nazionale 15", "valore_stimato": 380000, "confidence": 0.87}
  ],
  "metadata": {
    "rows_processed": 2,
    "execution_time_ms": 1250
  }
}
```

### 4.3 Registro Eseguibili

```json
{
  "id": "modello_return_unige",
  "name": "Modello Predizione RETURN - UniGe",
  "description": "Modello ML per stima valore immobiliare",
  "type": "python",
  "path": "executables/return_unige/predict.py",
  "parameters": [
    {"name": "modello", "type": "string", "required": true},
    {"name": "soglia", "type": "number", "default": 0.5}
  ],
  "output_columns": [
    {"name": "valore_stimato", "type": "real"},
    {"name": "confidence", "type": "real"}
  ],
  "timeout_seconds": 3600,
  "max_rows": 10000
}
```

---

## 5. Data Sources

### 5.1 Connettori Supportati

| Connettore | Auth | Stato |
|------------|------|-------|
| SharePoint 365 | OAuth2 | 🟡 Pianificato |
| SharePoint On-Premise | NTLM/Kerberos | 🟡 Pianificato |
| FTP | Username/Password | 🟡 Pianificato |
| SFTP | Username/Password + Key | 🟡 Pianificato |
| S3 | Access Key | 🟢 Futuro |
| Azure Blob | SAS Token | 🟢 Futuro |
| Google Drive | OAuth2 | 🟢 Futuro |

---

## 6. Gestione Avanzata Esecuzione

### 6.1 Gestione Errori per Riga

Quando una riga fallisce, il sistema deve continuare e tracciare:

```python
class RowResult(Enum):
    SUCCESS = "success"
    SKIPPED = "skipped"      # Input non valido
    ERROR = "error"          # API fallita
    RETRY_EXHAUSTED = "retry_exhausted"  # Max retry raggiunto

# Output finale include report errori
{
  "data": [...],  # Righe elaborate (anche parziali)
  "errors": [
    {"row_index": 42, "error": "API timeout", "input": {...}},
    {"row_index": 156, "error": "Invalid address", "input": {...}}
  ],
  "stats": {
    "total": 1000,
    "success": 980,
    "skipped": 12,
    "errors": 8
  }
}
```

**Opzioni utente:**
- ☑️ Continua su errori (default)
- ☐ Ferma al primo errore
- ☑️ Salva risultato parziale
- ☑️ Genera report errori (.csv)

### 6.2 Deduplicazione Chiamate

Cache interna per evitare chiamate duplicate:

```python
# Esempio: 1000 righe, 200 indirizzi unici
# Senza deduplica: 1000 chiamate API
# Con deduplica: 200 chiamate API (5x più veloce!)

dedup_config = {
    "enabled": True,
    "key_columns": ["indirizzo", "citta"],  # Colonne per chiave cache
    "cache_ttl": 3600  # 1 ora
}
```

### 6.3 Batch API Native

Alcune API supportano richieste batch:

```json
{
  "batch_mode": {
    "supported": true,
    "max_items_per_request": 100,
    "endpoint_batch": "/api/geocode/batch",
    "input_format": "array",  // [{"q": "addr1"}, {"q": "addr2"}]
    "output_format": "array"
  }
}
```

**Confronto performance:**

| Righe | Singola (1 req/s) | Batch (100/req) |
|-------|-------------------|-----------------|
| 100 | 100 sec | 1 sec |
| 1.000 | 16 min | 10 sec |
| 10.000 | 2.7 ore | 100 sec |

### 6.4 Preview su Campione

Prima di elaborare tutto il dataset:

```
┌─────────────────────────────────────────────────────────────┐
│ ⚠️ Stai per elaborare 10.000 righe                          │
│                                                             │
│ Tempo stimato: ~45 minuti                                   │
│ Chiamate API: ~10.000 (dopo deduplica: ~3.200)             │
│                                                             │
│ [🧪 Test su 10 righe]  [▶️ Esegui tutto]  [Annulla]         │
└─────────────────────────────────────────────────────────────┘
```

### 6.5 Validazione Input Pre-Chiamata

Prima di chiamare l'API, valida i dati:

```python
validation_rules = [
    {
        "column": "indirizzo",
        "rules": ["not_empty", "min_length:3"]
    },
    {
        "column": "lat",
        "rules": ["is_number", "range:-90:90"]
    }
]

# Risultato validazione
{
    "valid_rows": 950,
    "invalid_rows": 50,
    "issues": [
        {"row": 12, "column": "indirizzo", "issue": "empty"},
        {"row": 45, "column": "lat", "issue": "out_of_range: 999"}
    ]
}
```

**Comportamento:**
- Righe invalide → Skip automatico (non spreca chiamate API)
- Report validazione mostrato prima dell'esecuzione

---

## 7. Sicurezza

### 7.1 Credenziali API

**Problema attuale:** API key salvate in chiaro nel JSON 😬

**Soluzione:**

```
┌─────────────────────────────────────────────────────────────┐
│                    LIVELLI DI SICUREZZA                      │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  Livello 1 (Base):                                          │
│  - Encryption at rest (AES-256)                            │
│  - File config leggibili solo da backend                   │
│                                                             │
│  Livello 2 (Consigliato):                                   │
│  - Variabili ambiente per secrets                          │
│  - .env file con permessi ristretti                        │
│  - Config referenzia ${API_KEY_REVAL}                      │
│                                                             │
│  Livello 3 (Enterprise):                                    │
│  - HashiCorp Vault / AWS Secrets Manager                   │
│  - Rotazione automatica chiavi                             │
│  - Audit log accessi                                        │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

**Implementazione minima (Livello 2):**

```python
# config.json
{
  "auth": {
    "type": "api_key",
    "key_name": "X-API-Key",
    "key_value": "${REVAL_API_KEY}"  # Riferimento a env var
  }
}

# .env (non committato!)
REVAL_API_KEY=abc123secretkey
```

### 7.2 Sandbox Eseguibili

Isolare l'esecuzione di script esterni:

```yaml
# Docker container dedicato per eseguibili
executor_sandbox:
  image: python:3.11-slim
  read_only: true
  network_mode: none  # Nessun accesso rete (opzionale)
  mem_limit: 2g
  cpu_quota: 50000  # 50% CPU max
  volumes:
    - ./temp:/temp:rw  # Solo cartella temp
```

---

## 8. Costi e Quote

### 8.1 Tracking Utilizzo

```python
class ApiUsageTracker:
    """Traccia chiamate e costi per ogni API"""
    
    # Dati tracciati
    usage = {
        "api_id": "reval_omi",
        "period": "2026-01",
        "calls_total": 15420,
        "calls_success": 15102,
        "calls_error": 318,
        "cost_estimated": 154.20,  # Se API a pagamento
        "quota_limit": 50000,
        "quota_used": 15420,
        "quota_remaining": 34580
    }
```

### 8.2 Alert Quote

```
┌─────────────────────────────────────────────────────────────┐
│ ⚠️ ALERT: Quota API quasi esaurita                          │
│                                                             │
│ API: RE-VAL DATI IMMOBILIARI                                │
│ Utilizzo: 45.000 / 50.000 (90%)                            │
│                                                             │
│ Azioni:                                                     │
│ - Richiedi aumento quota                                    │
│ - Attendi reset (1 feb 2026)                               │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### 8.3 Dashboard Utilizzo (UI)

```
📊 Utilizzo API - Gennaio 2026

┌─────────────────────────────────────────────────────────────┐
│ API                    │ Chiamate │ Errori │ Quota    │ €   │
├────────────────────────┼──────────┼────────┼──────────┼─────│
│ 🌍 Nominatim           │ 12.340   │ 45     │ ∞ free   │ -   │
│ 🏠 RE-VAL OMI          │ 8.520    │ 12     │ 50k/mese │ 85€ │
│ 🏠 RE-VAL Fabbricati   │ 3.200    │ 8      │ 50k/mese │ 32€ │
│ ⚙️ Modello RETURN      │ 1.500    │ 0      │ -        │ -   │
├────────────────────────┼──────────┼────────┼──────────┼─────│
│ TOTALE                 │ 25.560   │ 65     │          │117€ │
└─────────────────────────────────────────────────────────────┘
```

---

## 9. Scheduler (Esecuzione Programmata)

### 9.1 Casi d'uso

| Trigger | Esempio |
|---------|---------|
| **Cron** | "Ogni lunedì alle 6:00" |
| **Intervallo** | "Ogni 4 ore" |
| **Evento** | "Quando arriva file in SharePoint" |
| **Manuale** | "On demand" (default attuale) |

### 9.2 Integrazione Celery Beat

```python
# celery_app.py
from celery.schedules import crontab

app.conf.beat_schedule = {
    'arricchimento-settimanale-immobili': {
        'task': 'tasks.execute_pipeline',
        'schedule': crontab(hour=6, minute=0, day_of_week=1),  # Lunedì 6:00
        'args': ('project_id_abc123',)
    },
    'aggiornamento-omi-mensile': {
        'task': 'tasks.execute_pipeline', 
        'schedule': crontab(hour=2, minute=0, day_of_month=1),  # 1° del mese
        'args': ('project_id_omi_update',)
    }
}
```

### 9.3 UI Scheduler

```
┌─────────────────────────────────────────────────────────────┐
│ ⏰ Pianifica Esecuzione                                      │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│ Frequenza: [Settimanale          ▼]                        │
│                                                             │
│ Giorno:    [✓] Lun [ ] Mar [ ] Mer [ ] Gio [ ] Ven        │
│                                                             │
│ Ora:       [06] : [00]                                      │
│                                                             │
│ ☑️ Notifica email al completamento                          │
│ ☑️ Notifica email su errore                                 │
│                                                             │
│ Prossima esecuzione: Lunedì 27 Gen 2026, 06:00             │
│                                                             │
│              [Salva Schedulazione]  [Annulla]               │
└─────────────────────────────────────────────────────────────┘
```

### 9.4 Trigger su Evento (Futuro)

```python
# Watch SharePoint folder
file_trigger = {
    "type": "file_watch",
    "source": "sharepoint",
    "path": "/Documenti/NuoviImmobili/",
    "pattern": "*.xlsx",
    "action": "execute_pipeline",
    "pipeline_id": "arricchisci_nuovi_immobili"
}
```

---

## 10. Dipendenze Eseguibili

### 10.1 Gestione Ambiente Python

```json
{
  "id": "modello_return_unige",
  "type": "python",
  "path": "executables/return_unige/predict.py",
  "environment": {
    "type": "venv",  // venv | conda | docker
    "requirements": "executables/return_unige/requirements.txt",
    "python_version": "3.11"
  }
}
```

### 10.2 Auto-Setup Ambiente

```bash
# Al primo utilizzo, il sistema:
1. Crea venv dedicato
2. Installa requirements.txt
3. Verifica import OK
4. Salva stato "ready"

# Esecuzioni successive usano venv esistente
```

### 10.3 Modalità Docker (Isolamento Completo)

```json
{
  "id": "modello_complesso",
  "type": "docker",
  "image": "ghcr.io/return-project/modello-unige:v2.1",
  "environment": {
    "GPU": false,
    "memory_limit": "4g"
  }
}
```

---

## 11. Concatenamento API (Pipeline Multi-Step)

### 11.1 Supporto Esistente

Il canvas già supporta concatenamento:

```
┌──────────┐    ┌──────────────┐    ┌──────────────┐    ┌────────┐
│ Sorgente │───▶│ API Geocoding│───▶│ API OMI      │───▶│ Output │
│ Immobili │    │ (Nominatim)  │    │ (RE-VAL)     │    │        │
└──────────┘    └──────────────┘    └──────────────┘    └────────┘
     │                 │                   │                 │
     │ indirizzo       │ +lat, +lon        │ +zona_omi       │
     │ citta           │                   │ +valore_min     │
     │                 │                   │ +valore_max     │
```

### 11.2 Ottimizzazione Pipeline

```python
# Il sistema rileva automaticamente le dipendenze:
# - API 2 (OMI) richiede lat/lon
# - lat/lon vengono da API 1 (Geocoding)
# → Esegue in sequenza, non in parallelo

pipeline_optimizer = {
    "detect_dependencies": True,
    "parallel_when_possible": True,
    "cache_intermediate_results": True
}
```

---

## 12. Task Asincroni (Celery + Redis)

### 6.1 Docker Compose

```yaml
# Aggiunta a docker-compose.yml
services:
  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    volumes:
      - redis_data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5

  celery_worker:
    build: ./backend
    command: celery -A celery_app worker --loglevel=info --concurrency=4
    volumes:
      - ./backend:/app
      - ./data:/app/data
    depends_on:
      - redis
      - backend
    environment:
      - CELERY_BROKER_URL=redis://redis:6379/0
      - CELERY_RESULT_BACKEND=redis://redis:6379/0

volumes:
  redis_data:
```

### 6.2 Task Model

```python
# models/task.py
from enum import Enum
from datetime import datetime
from typing import Optional, Dict, Any

class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILURE = "failure"
    CANCELLED = "cancelled"

class TaskType(str, Enum):
    REST_ENRICHMENT = "rest_enrichment"
    EXECUTABLE = "executable"
    DATASOURCE = "datasource"

class Task:
    id: str
    project_id: str
    task_type: TaskType
    status: TaskStatus
    progress: int  # 0-100
    total_rows: int
    processed_rows: int
    error_message: Optional[str]
    result_path: Optional[str]
    created_at: datetime
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    config: Dict[str, Any]
```

### 6.3 API Endpoints

```python
# routes/tasks.py

@router.post("/tasks")
async def create_task(request: TaskRequest) -> TaskResponse:
    """Crea e accoda un nuovo task"""
    pass

@router.get("/tasks/{task_id}/status")
async def get_task_status(task_id: str) -> TaskStatusResponse:
    """Ritorna stato e progress del task"""
    pass

@router.delete("/tasks/{task_id}")
async def cancel_task(task_id: str) -> dict:
    """Annulla un task in esecuzione"""
    pass

@router.get("/tasks/{task_id}/result")
async def get_task_result(task_id: str) -> FileResponse:
    """Scarica il risultato del task completato"""
    pass
```

---

## 13. Stato Attuale vs Target

### 13.1 Già Implementato ✅

| Componente | Note |
|------------|------|
| ArricchisciEditor | Canvas completo |
| ApiEnrichNode | Mapping parametri, validazione |
| ApiConnectionManager | Wizard 5 step |
| CRUD API Connections | Create, Update, Delete |
| Test API | Con selezione campi output |
| Auth None / API Key / OAuth2 | Funzionante |
| Enricher (riga per riga) | Con rate limiting |
| Generator | API che genera dataset |
| JSON Path Extraction | Navigazione nested |
| GeoJSON Support | FeatureCollection |

### 13.2 Da Implementare ❌

| # | Componente | Effort | Priorità |
|---|------------|--------|----------|
| **INFRASTRUTTURA** |||||
| 1 | Docker Compose (Redis) | 1h | 🔴 |
| 2 | Setup Celery + Beat | 3h | 🔴 |
| 3 | Task Model (DB) | 2h | 🔴 |
| 4 | API Tasks (CRUD) | 2h | 🔴 |
| **API REST** |||||
| 5 | Template Schema | 1h | 🔴 |
| 6 | Import OpenAPI | 3h | 🔴 |
| 7 | Template Nominatim | 1h | 🟡 |
| 8 | Batch parallelo | 2h | 🟡 |
| 9 | Batch API native | 2h | 🟡 |
| **GESTIONE ESECUZIONE** |||||
| 10 | Gestione errori per riga | 2h | 🔴 |
| 11 | Deduplicazione chiamate | 3h | 🔴 |
| 12 | Preview su campione | 2h | 🔴 |
| 13 | Validazione input pre-chiamata | 2h | 🟡 |
| **ESEGUIBILI** |||||
| 14 | Executor base | 3h | 🟡 |
| 15 | Timeout + Kill | 1h | 🟡 |
| 16 | Gestione dipendenze (venv) | 3h | 🟡 |
| 17 | Sandbox Docker | 2h | 🟢 |
| **DATA SOURCES** |||||
| 18 | SharePoint 365 | 3h | 🟢 |
| 19 | FTP/SFTP | 2h | 🟢 |
| **SICUREZZA** |||||
| 20 | Encryption credenziali | 2h | 🔴 |
| 21 | Variabili ambiente secrets | 2h | 🔴 |
| **MONITORING** |||||
| 22 | Tracking utilizzo API | 3h | 🟡 |
| 23 | Alert quote | 2h | 🟡 |
| 24 | Dashboard utilizzo | 3h | 🟢 |
| **SCHEDULER** |||||
| 25 | Celery Beat config | 2h | 🟡 |
| 26 | UI schedulazione | 3h | 🟢 |
| **FRONTEND** |||||
| 27 | UI Template Library | 4h | 🔴 |
| 28 | Progress tracking | 2h | 🔴 |
| 29 | Annulla task | 1h | 🟡 |
| | **TOTALE** | **~58h** | **~7-8 giorni** |

---

## 14. Piano Fasi

| Fase | Contenuto | Effort | Output |
|------|-----------|--------|--------|
| **1** | Infrastruttura (1-4) + Sicurezza (20-21) | 12h | Task asincroni + credenziali sicure |
| **2** | REST base (5-9) + Gestione exec (10-13) | 15h | Template + deduplica + preview |
| **3** | UI (27-29) + OpenAPI Import (6) | 9h | Importa RE-VAL con 1 click |
| **4** | Eseguibili (14-17) | 9h | Python/exe con venv + sandbox |
| **5** | Monitoring (22-24) + Scheduler (25-26) | 13h | Dashboard + esecuzioni programmate |
| **6** | Data Sources (18-19) | 5h | SharePoint + FTP |

---

## 15. Demo ESRI (13-14 Maggio 2026)

### 15.1 Scenario Demo

1. **Carica dataset immobili** (da ESTRAI)
2. **Arricchisci con Nominatim** → Geocoding indirizzi
3. **Arricchisci con RE-VAL/OMI** → Valori immobiliari
4. **Esegui modello RETURN** → Predizione ML
5. **Esporta risultato** → GeoJSON per ArcGIS

### 15.2 Requisiti Minimi per Demo

| Componente | Stato | Priorità |
|------------|-------|----------|
| Canvas ArricchisciEditor | ✅ | - |
| ApiEnrichNode funzionante | ✅ | - |
| Wizard configurazione API | ✅ | - |
| Template Nominatim pronto | ☐ | 🔴 |
| Import OpenAPI (RE-VAL) | ☐ | 🔴 |
| Progress bar durante esecuzione | ☐ | 🔴 |
| Deduplicazione chiamate | ☐ | 🔴 |
| Preview su campione | ☐ | 🔴 |
| Gestione errori con report | ☐ | 🟡 |
| Eseguibili (almeno Python) | ☐ | 🟡 |
| Dashboard utilizzo API | ☐ | 🟢 |
| Scheduler | ☐ | 🟢 |

---

## 16. Note Tecniche

### 16.1 Rate Limiting

- Nominatim: 1 req/sec (gratuito)
- RE-VAL: configurabile
- Default: 5 req/sec

### 16.2 Retry Policy

```python
retry_config = {
    "max_retries": 3,
    "backoff_factor": 2,  # 1s, 2s, 4s
    "retry_on_status": [429, 500, 502, 503, 504]
}
```

### 16.3 Caching

- Cache risposte API per evitare chiamate duplicate
- Chiave: hash(endpoint + parametri)
- TTL: configurabile (default 1h)

### 16.4 Limiti Consigliati

| Parametro | Valore Default | Note |
|-----------|----------------|------|
| Max righe per batch | 10.000 | Preview obbligatoria sopra |
| Timeout singola chiamata | 30s | |
| Timeout eseguibile | 3600s (1h) | Configurabile |
| Max retry | 3 | Con backoff esponenziale |
| Cache TTL | 3600s (1h) | Per deduplicazione |

---

## 17. Changelog

| Data | Modifica |
|------|----------|
| 2026-01-22 | Creazione documento |
| | Definita architettura Celery + Redis |
| | Schema template completo |
| | Aggiunti: gestione errori, deduplicazione, batch API |
| | Aggiunti: preview campione, validazione input |
| | Aggiunti: sicurezza credenziali, sandbox eseguibili |
| | Aggiunti: tracking costi/quote, dashboard |
| | Aggiunti: scheduler Celery Beat, trigger eventi |
| | Aggiunti: dipendenze eseguibili, venv management |
| | Piano rivisto: 6 fasi, ~58h totali |