# 🔌 ARRICCHISCI - Piano di Sviluppo Completo

**Data:** 2026-01-23  
**Stato attuale:** ~80%  
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
├── routes/
│   └── templates.py           # ✅ GET /api/templates
├── tasks/
│   ├── __init__.py
│   ├── rest_task.py           # Task chiamate REST
│   ├── executable_task.py     # Task eseguibili (da fare)
│   └── datasource_task.py     # Task download (da fare)
├── services/
│   ├── task_service.py        # CRUD task
│   └── template_service.py    # Gestione template
└── models/
    └── task.py                # Task model

data/
├── api_templates.json         # ✅ Template preconfigurati (8 template)
├── api_connections/           # Connettori salvati
└── executables/
    └── _registry/             # Script registrati (da fare)

frontend/
├── components/
│   └── arricchisci/
│       ├── TemplateLibraryModal.jsx   # ✅ Libreria template
│       ├── OpenAPIImportModal.jsx     # ✅ Import OpenAPI
│       ├── ExecutableConfigModal.jsx  # ✅ Config eseguibili
│       └── DataSourceConfigModal.jsx  # ✅ Config data sources
├── pages/
│   └── ApiConnectionManager.jsx       # ✅ "Connettori Esterni"
└── styles/
    └── ApiConnectionManager.css       # ✅ Stili completi

docker-compose.yml             # + Redis service (da fare)
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

## 12. Modelli Remoti (RETURN / Università)

### 12.1 Scenario

I modelli ML dei progetti RETURN potrebbero risiedere su server delle università:

```
┌─────────────────────────────────────────────────────────────────┐
│                         URBY-ETL                                │
│                    (server ICSC)                                │
└─────────────────────────────────────────────────────────────────┘
          │                    │                    │
          ▼                    ▼                    ▼
   ┌────────────┐      ┌────────────┐      ┌────────────┐
   │ Server     │      │ Server     │      │ Server     │
   │ UniGe      │      │ PoliMi     │      │ UniBo      │
   │            │      │            │      │            │
   │ /predict   │      │ /analyze   │      │ /classify  │
   │ (FastAPI)  │      │ (Flask)    │      │ (FastAPI)  │
   └────────────┘      └────────────┘      └────────────┘
        │                    │                    │
   Modello Stima       Modello Rischio      Modello 
   Valore Immobili     Idrogeologico        Classificazione
```

### 12.2 Soluzione: Standard API Comune

Le università espongono i loro modelli come **API REST** seguendo uno standard comune definito da URBY-ETL/RETURN.

**Vantaggi:**
- Ogni università gestisce il proprio server/modello
- URBY-ETL li consuma come qualsiasi altra API
- Nessuna installazione richiesta sulle uni (solo esporre API)
- Scalabile: nuovi modelli = nuovi endpoint

### 12.3 Specifica API Standard per Modelli RETURN

```yaml
# return-model-api-spec.yaml
# Specifica OpenAPI che le università devono implementare

openapi: 3.0.0
info:
  title: RETURN Model API Standard
  description: |
    Specifica standard per esporre modelli ML nel progetto RETURN.
    Implementare questa API per rendere il modello compatibile con URBY-ETL.
  version: 1.0.0
  contact:
    name: RETURN Project
    email: return@example.it

paths:
  /health:
    get:
      summary: Health check
      description: Verifica che il servizio sia attivo
      responses:
        200:
          description: Servizio attivo
          content:
            application/json:
              schema:
                type: object
                properties:
                  status:
                    type: string
                    example: "healthy"
                  model_version:
                    type: string
                    example: "2.1.0"

  /info:
    get:
      summary: Informazioni sul modello
      description: Ritorna metadati del modello (input/output attesi)
      responses:
        200:
          description: Metadati modello
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/ModelInfo'

  /predict:
    post:
      summary: Esegue predizione
      description: |
        Endpoint principale. Accetta un batch di dati e ritorna predizioni.
        Supporta sia singola riga che batch.
      security:
        - ApiKeyAuth: []
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/PredictRequest'
      responses:
        200:
          description: Predizioni completate
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/PredictResponse'
        400:
          description: Input non valido
        401:
          description: Non autorizzato
        500:
          description: Errore interno

components:
  securitySchemes:
    ApiKeyAuth:
      type: apiKey
      in: header
      name: X-API-Key

  schemas:
    ModelInfo:
      type: object
      properties:
        name:
          type: string
          example: "Modello Stima Valore Immobiliare"
        version:
          type: string
          example: "2.1.0"
        description:
          type: string
        provider:
          type: string
          example: "Università di Genova - DIME"
        input_schema:
          type: array
          items:
            $ref: '#/components/schemas/FieldSchema'
        output_schema:
          type: array
          items:
            $ref: '#/components/schemas/FieldSchema'
        limits:
          type: object
          properties:
            max_batch_size:
              type: integer
              example: 100
            timeout_seconds:
              type: integer
              example: 300

    FieldSchema:
      type: object
      properties:
        name:
          type: string
          example: "superficie_mq"
        type:
          type: string
          enum: [string, number, integer, boolean, date, geometry]
        required:
          type: boolean
        description:
          type: string

    PredictRequest:
      type: object
      required:
        - data
      properties:
        data:
          type: array
          description: Array di oggetti da processare
          items:
            type: object
          example:
            - id: 1
              superficie_mq: 85
              piano: 3
              zona_omi: "B1"
            - id: 2
              superficie_mq: 120
              piano: 1
              zona_omi: "C2"
        parameters:
          type: object
          description: Parametri opzionali del modello
          example:
            confidence_threshold: 0.8

    PredictResponse:
      type: object
      properties:
        success:
          type: boolean
        predictions:
          type: array
          items:
            type: object
          example:
            - id: 1
              valore_stimato: 285000
              confidence: 0.92
              range_min: 270000
              range_max: 300000
            - id: 2
              valore_stimato: 180000
              confidence: 0.87
              range_min: 165000
              range_max: 195000
        errors:
          type: array
          items:
            type: object
            properties:
              index:
                type: integer
              error:
                type: string
        metadata:
          type: object
          properties:
            rows_processed:
              type: integer
            execution_time_ms:
              type: integer
            model_version:
              type: string
```

### 12.4 Template URBY-ETL per Modello Remoto

Quando un'università implementa l'API, si crea un template per importarla:

```json
{
  "_meta": {
    "schema_version": "1.0",
    "template_id": "return_unige_stima_valore",
    "template_type": "remote_model",
    "category": "ml_model",
    "icon": "🧠",
    "tags": ["return", "ml", "valutazione", "immobiliare"],
    "provider": "Università di Genova",
    "project": "RETURN"
  },
  
  "display": {
    "name": "Modello Stima Valore Immobiliare (UniGe)",
    "short_description": "Stima il valore di mercato di immobili residenziali",
    "long_description": "Modello ML sviluppato da UniGe nell'ambito del progetto RETURN. Utilizza caratteristiche dell'immobile e dati OMI per stimare il valore di mercato.",
    "use_cases": [
      "Valutazione portafoglio immobiliare",
      "Stima preliminare per acquisizioni",
      "Analisi di mercato"
    ],
    "limitations": [
      "Solo immobili residenziali",
      "Copertura: Nord Italia",
      "Max 100 immobili per chiamata"
    ],
    "docs_url": "https://return.unige.it/docs/modello-stima"
  },

  "config": {
    "api_type": "enricher",
    "endpoint": {
      "url": "https://return.unige.it/api/v1/predict",
      "method": "POST"
    },
    "auth": {
      "type": "api_key",
      "key_name": "X-API-Key",
      "location": "header"
    },
    "options": {
      "timeout": 300,
      "batch_mode": {
        "enabled": true,
        "max_items": 100,
        "input_field": "data"
      },
      "retry_on_error": true,
      "retry_max": 3
    }
  },

  "parameters": [
    {
      "name": "superficie_mq",
      "label": "Superficie (mq)",
      "type": "number",
      "required": true,
      "mapping_hint": "superficie|area|mq|sqm"
    },
    {
      "name": "piano",
      "label": "Piano",
      "type": "integer",
      "required": true,
      "mapping_hint": "piano|floor"
    },
    {
      "name": "zona_omi",
      "label": "Zona OMI",
      "type": "string",
      "required": true,
      "mapping_hint": "zona_omi|cod_zona|omi"
    },
    {
      "name": "anno_costruzione",
      "label": "Anno costruzione",
      "type": "integer",
      "required": false,
      "mapping_hint": "anno|year|costruzione"
    },
    {
      "name": "stato_conservazione",
      "label": "Stato conservazione",
      "type": "string",
      "required": false,
      "mapping_hint": "stato|conservazione|condition"
    }
  ],

  "output_mapping": [
    {
      "path": "predictions[*].valore_stimato",
      "column": "valore_stimato",
      "type": "real",
      "description": "Valore di mercato stimato (€)"
    },
    {
      "path": "predictions[*].confidence",
      "column": "confidence",
      "type": "real",
      "description": "Confidenza della stima (0-1)"
    },
    {
      "path": "predictions[*].range_min",
      "column": "valore_min",
      "type": "real",
      "description": "Limite inferiore range stima"
    },
    {
      "path": "predictions[*].range_max",
      "column": "valore_max",
      "type": "real",
      "description": "Limite superiore range stima"
    }
  ],

  "test_data": {
    "data": [
      {
        "superficie_mq": 85,
        "piano": 3,
        "zona_omi": "B1",
        "anno_costruzione": 1980
      }
    ]
  }
}
```

### 12.5 Flusso: Come un'Università Pubblica un Modello

```
┌─────────────────────────────────────────────────────────────────┐
│ 1. SVILUPPO (Università)                                        │
│    - Sviluppa modello ML                                        │
│    - Wrappa in API REST seguendo lo standard RETURN             │
│    - Testa con /health e /predict                               │
└─────────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│ 2. REGISTRAZIONE (Coordinamento RETURN)                         │
│    - Università comunica URL endpoint                           │
│    - Fornisce documentazione (input/output)                     │
│    - Riceve API key per tracking                                │
└─────────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│ 3. CREAZIONE TEMPLATE (URBY-ETL)                                │
│    - Crea JSON template per il modello                          │
│    - Aggiunge a libreria template                               │
│    - Disponibile a tutti gli utenti URBY-ETL                    │
└─────────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│ 4. UTILIZZO (Utente finale)                                     │
│    - Importa template "Modello UniGe"                          │
│    - Configura mapping colonne                                  │
│    - Esegue arricchimento                                       │
└─────────────────────────────────────────────────────────────────┘
```

### 12.6 Auto-Discovery da /info

Se l'API implementa `/info`, URBY-ETL può auto-configurarsi:

```python
# Flusso auto-discovery
1. Utente inserisce solo URL base: https://return.unige.it/api/v1
2. URBY chiama GET /info
3. Riceve schema input/output
4. Genera automaticamente il template
5. Utente deve solo mappare le colonne
```

### 12.7 Marketplace Modelli RETURN

In futuro, una UI dedicata:

```
┌─────────────────────────────────────────────────────────────────┐
│ 🧠 Marketplace Modelli RETURN                                   │
├─────────────────────────────────────────────────────────────────┤
│ 🔍 [Cerca modello...                              ]             │
│                                                                 │
│ ┌─ VALUTAZIONE IMMOBILIARE ────────────────────────────────┐   │
│ │                                                          │   │
│ │  🧠 Stima Valore Immobili          UniGe    ⭐ v2.1     │   │
│ │     Input: superficie, piano, zona_omi                  │   │
│ │     Output: valore_stimato, confidence                  │   │
│ │                                         [+ Aggiungi]    │   │
│ │                                                          │   │
│ │  🧠 Analisi Comparativa             PoliMi   ⭐ v1.3     │   │
│ │     Input: coordinate, tipologia                        │   │
│ │     Output: comparabili[], prezzo_medio                 │   │
│ │                                         [+ Aggiungi]    │   │
│ └──────────────────────────────────────────────────────────┘   │
│                                                                 │
│ ┌─ RISCHIO ────────────────────────────────────────────────┐   │
│ │                                                          │   │
│ │  🧠 Rischio Idrogeologico           UniBo    ⭐ v1.0     │   │
│ │     Input: geometry, elevation                          │   │
│ │     Output: risk_score, flood_probability               │   │
│ │                                         [+ Aggiungi]    │   │
│ └──────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

---

## 13. Task Asincroni (Celery + Redis)

### 13.1 Docker Compose

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

### 13.2 Task Model

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

### 13.3 API Endpoints

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

## 14. Stato Attuale vs Target

### 14.1 Già Implementato ✅

| Componente | Note |
|------------|------|
| ArricchisciEditor | Canvas completo |
| ApiEnrichNode | Mapping parametri, validazione |
| **Connettori Esterni** (ex ApiConnectionManager) | Wizard 5 step, rinominato |
| CRUD API Connections | Create, Update, Delete |
| Test API | Con selezione campi output |
| Auth None / API Key / OAuth2 | Funzionante |
| Enricher (riga per riga) | Con rate limiting |
| Generator | API che genera dataset |
| JSON Path Extraction | Navigazione nested |
| GeoJSON Support | FeatureCollection |
| **Template Library UI** | Modal con ricerca/filtri, carica da JSON |
| **Template Schema JSON** | `data/api_templates.json` + endpoint FastAPI |
| **OpenAPI Import UI** | Modal per importare da URL/file |
| **Template Nominatim** | Geocoding + Reverse nel JSON |
| **ExecutableConfigModal** | Wizard 5 step per .py/.exe |
| **DataSourceConfigModal** | Wizard 4 step per SharePoint/FTP/S3 |
| **Categorie Complete** | API REST + Eseguibili + Data Sources |

### 14.2 Da Implementare ❌

| # | Componente | Effort | Priorità | Stato |
|---|------------|--------|----------|-------|
| **INFRASTRUTTURA** ||||||
| 1 | Docker Compose (Redis) | 1h | 🔴 | ❌ |
| 2 | Setup Celery + Beat | 3h | 🔴 | ❌ |
| 3 | Task Model (DB) | 2h | 🔴 | ❌ |
| 4 | API Tasks (CRUD) | 2h | 🔴 | ❌ |
| **API REST** ||||||
| 5 | Template Schema | 1h | 🔴 | ✅ **FATTO** |
| 6 | Import OpenAPI (UI) | 3h | 🔴 | ✅ **FATTO** |
| 7 | Template Nominatim | 1h | 🟡 | ✅ **FATTO** |
| 8 | Batch parallelo | 2h | 🟡 | ❌ |
| 9 | Batch API native | 2h | 🟡 | ❌ |
| **GESTIONE ESECUZIONE** ||||||
| 10 | Gestione errori per riga | 2h | 🔴 | ❌ |
| 11 | Deduplicazione chiamate | 3h | 🔴 | ❌ |
| 12 | Preview su campione | 2h | 🔴 | ❌ |
| 13 | Validazione input pre-chiamata | 2h | 🟡 | ❌ |
| **MODELLI REMOTI (RETURN)** ||||||
| 14 | Specifica API Standard | 2h | 🔴 | ❌ |
| 15 | Template tipo "remote_model" | 2h | 🔴 | ❌ |
| 16 | Auto-discovery da /info | 3h | 🟡 | ❌ |
| 17 | UI Marketplace modelli | 4h | 🟢 | ❌ |
| **ESEGUIBILI LOCALI** ||||||
| 18 | Executor base (backend) | 3h | 🟡 | ❌ |
| 19 | Timeout + Kill | 1h | 🟡 | ❌ |
| 20 | Gestione dipendenze (venv) | 3h | 🟡 | ❌ |
| 21 | Sandbox Docker | 2h | 🟢 | ❌ |
| 18b | **ExecutableConfigModal (UI)** | 2h | 🟡 | ✅ **FATTO** |
| **DATA SOURCES** ||||||
| 22 | SharePoint 365 (backend) | 3h | 🟢 | ❌ |
| 23 | FTP/SFTP (backend) | 2h | 🟢 | ❌ |
| 22b | **DataSourceConfigModal (UI)** | 2h | 🟢 | ✅ **FATTO** |
| **SICUREZZA** ||||||
| 24 | Encryption credenziali | 2h | 🔴 | ❌ |
| 25 | Variabili ambiente secrets | 2h | 🔴 | ❌ |
| **MONITORING** ||||||
| 26 | Tracking utilizzo API | 3h | 🟡 | ❌ |
| 27 | Alert quote | 2h | 🟡 | ❌ |
| 28 | Dashboard utilizzo | 3h | 🟢 | ❌ |
| **SCHEDULER** ||||||
| 29 | Celery Beat config | 2h | 🟡 | ❌ |
| 30 | UI schedulazione | 3h | 🟢 | ❌ |
| **FRONTEND** ||||||
| 31 | UI Template Library | 4h | 🔴 | ✅ **FATTO** |
| 32 | Progress tracking | 2h | 🔴 | ❌ |
| 33 | Annulla task | 1h | 🟡 | ❌ |
| | **TOTALE RIMANENTE** | **~55h** | **~7 giorni** | |

---

## 15. Piano Fasi

| Fase | Contenuto | Effort | Output |
|------|-----------|--------|--------|
| **1** | Infrastruttura (1-4) + Sicurezza (24-25) | 12h | Task asincroni + credenziali sicure |
| **2** | REST base (5-9) + Gestione exec (10-13) | 15h | Template + deduplica + preview |
| **3** | UI (31-33) + OpenAPI Import (6) | 9h | Importa RE-VAL con 1 click |
| **4** | Modelli Remoti RETURN (14-17) | 11h | Specifica + template + marketplace |
| **5** | Eseguibili locali (18-21) | 9h | Python/exe con venv + sandbox |
| **6** | Monitoring (26-28) + Scheduler (29-30) | 13h | Dashboard + esecuzioni programmate |
| **7** | Data Sources (22-23) | 5h | SharePoint + FTP |

---

## 16. Demo ESRI (13-14 Maggio 2026)

### 16.1 Scenario Demo

1. **Carica dataset immobili** (da ESTRAI)
2. **Arricchisci con Nominatim** → Geocoding indirizzi
3. **Arricchisci con RE-VAL/OMI** → Valori immobiliari
4. **Esegui modello RETURN** → Predizione ML
5. **Esporta risultato** → GeoJSON per ArcGIS

### 16.2 Requisiti Minimi per Demo

| Componente | Stato | Priorità |
|------------|-------|----------|
| Canvas ArricchisciEditor | ✅ | - |
| ApiEnrichNode funzionante | ✅ | - |
| Wizard configurazione API | ✅ | - |
| Template Nominatim pronto | ✅ | - |
| Import OpenAPI (RE-VAL) UI | ✅ | - |
| UI Template Library | ✅ | - |
| UI Eseguibili | ✅ | - |
| UI Data Sources | ✅ | - |
| Progress bar durante esecuzione | ☐ | 🔴 |
| Deduplicazione chiamate | ☐ | 🔴 |
| Preview su campione | ☐ | 🔴 |
| Gestione errori con report | ☐ | 🟡 |
| Specifica API modelli RETURN | ☐ | 🔴 |
| Template modello remoto | ☐ | 🟡 |
| Eseguibili backend (almeno Python) | ☐ | 🟡 |
| Data Sources backend | ☐ | 🟢 |
| Dashboard utilizzo API | ☐ | 🟢 |
| Scheduler | ☐ | 🟢 |

---

## 17. Note Tecniche

### 17.1 Rate Limiting

- Nominatim: 1 req/sec (gratuito)
- RE-VAL: configurabile
- Default: 5 req/sec

### 17.2 Retry Policy

```python
retry_config = {
    "max_retries": 3,
    "backoff_factor": 2,  # 1s, 2s, 4s
    "retry_on_status": [429, 500, 502, 503, 504]
}
```

### 17.3 Caching

- Cache risposte API per evitare chiamate duplicate
- Chiave: hash(endpoint + parametri)
- TTL: configurabile (default 1h)

### 17.4 Limiti Consigliati

| Parametro | Valore Default | Note |
|-----------|----------------|------|
| Max righe per batch | 10.000 | Preview obbligatoria sopra |
| Timeout singola chiamata | 30s | |
| Timeout eseguibile | 3600s (1h) | Configurabile |
| Max retry | 3 | Con backoff esponenziale |
| Cache TTL | 3600s (1h) | Per deduplicazione |

---

## 18. Changelog

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
| | Aggiunta sezione Modelli Remoti RETURN |
| | Specifica API standard per università |
| | Template remote_model + auto-discovery |
| | Marketplace modelli RETURN |
| | Piano rivisto: 7 fasi, ~69h totali |
| **2026-01-23** | **Rinominato "API Esterne" → "Connettori Esterni"** |
| | **Implementato Template Library UI** (carica da JSON) |
| | **Implementato `api_templates.json`** + endpoint FastAPI `/api/templates` |
| | **Implementato OpenAPIImportModal** |
| | **Implementato ExecutableConfigModal** (wizard 5 step) |
| | **Implementato DataSourceConfigModal** (wizard 4 step) |
| | **Aggiunta categoria "datasource"** ai filtri |
| | Template Nominatim, OSRM, RE-VAL, RETURN nel JSON |
| | Stato aggiornato: 70% → 80% |
| | Effort rimanente: ~69h → ~55h |