# 📋 TODO DataFlow Manager - Gennaio 2026

## 📊 STATO PROGETTO ATTUALE

| Modulo | Completamento | Note |
|--------|---------------|------|
| **Estrai** | 98% ✅ | 357/368 test passati (97%) |
| **Manipola** | 95% ✅ | 24 trasformazioni, test da eseguire |
| **Arricchisci** | 40% 🔄 | File esterni integrati, core da completare |
| **File Esterni** | 100% ✅ | Upload, selezione colonne, SRID |
| **Upload** | 0% ❌ | Da fare |
| **Scheduler** | 0% ❌ | Da fare |

---

## 🎯 PIANO DI LAVORO

### FASE 1: REFACTORING ✅ COMPLETATO
Pulizia codice, rimuovi duplicati, centralizza logica comune.

**File Backend - Completati:**
- [x] `orchestrator_service.py` → Suddiviso in moduli (orchestrator/)
  - `estrai_executor.py` - Logica ESTRAI + preview
  - `manipola_executor.py` - Logica MANIPOLA + preview  
  - `enrich_executor.py` - Logica ARRICCHISCI
  - `transform_operations.py` - Operazioni base (12)
  - `spatial_operations.py` - Operazioni spaziali (12)
- [ ] `api_connection_service.py`
- [ ] `file_source_service.py`
- [ ] `query_executor.py`

**File Frontend - Da analizzare:**
- [ ] `ManipolaEditor.jsx`
- [ ] `ArricchisciEditor.jsx`
- [ ] `ProjectSourceNode.jsx`
- [ ] `FileSourceNode.jsx`
- [ ] `ResultNode.jsx`

**Obiettivi refactoring:**
- [x] Centralizzare operazioni trasformazione (transform_operations.py)
- [x] Centralizzare operazioni spaziali (spatial_operations.py)
- [ ] Centralizzare logica caricamento dati (data_loader)
- [ ] Centralizzare logica export (data_exporter)
- [ ] Rimuovere codice duplicato tra Editor
- [ ] Rimuovere codice duplicato tra Node components

### FASE 2: TEST COMPLETO (In corso)
Eseguire tutti i test del Piano Test, fix bug trovati.

**Aree da testare:**
- [x] Connessioni Database (5/5 DB) ✅ 100%
- [x] Modulo ESTRAI (357/368 test) ✅ 97%
- [ ] Modulo MANIPOLA (0/159 test) ☐ 0% - **PROSSIMO**
- [ ] Modulo FILE ESTERNI (15 test)
- [ ] Modulo ARRICCHISCI (20 test)
- [ ] Sicurezza (15 test)
- [ ] UI/UX Generale (15 test)

### FASE 3: INCREMENTO FUNZIONALITÀ (Dopo stabilizzazione)
Nuove feature solo su base stabile.

**Priorità Alta:**
1. [ ] FileGDB (.gdb) come formato di OUTPUT
2. [ ] Completare ARRICCHISCI (40% → 90%)
3. [ ] Upload su Database PostGIS
4. [ ] Upload su Feature Service ArcGIS
5. [ ] Scheduler automazione pipeline

**Priorità Media - Nuove Capabilities:**
6. [ ] **Stream dati in input** - Connessione a flussi real-time (Kafka, MQTT, WebSocket)
7. [ ] **Arricchisci con servizi esterni** - Chiamate a API/microservizi custom durante pipeline
8. [ ] **Linguaggio naturale** - Generazione pipeline via LLM (es. "unisci i POI con i comuni e calcola il buffer di 500m")

### FASE 4: DOCUMENTAZIONE & MARKETING (Pre-ESRI Conference)
Materiali per presentazione e promozione.

**Video Demo:**
- [ ] Script dettagliato (5-7 min)
- [ ] Registrazione con OBS/Loom/Camtasia
- [ ] Editing e sottotitoli

**Brochure/Presentazione:**
- [ ] Screenshot chiave da video
- [ ] Brochure PDF (2-4 pagine)
- [ ] Slide PowerPoint per conferenza
- [ ] GIF animate per social/LinkedIn

---

## ✅ COMPLETATI

### 20 Gennaio 2026
- ✅ Refactoring orchestrator_service.py in moduli separati
- ✅ Piano test MANIPOLA aggiornato (159 test case)
- ✅ Documentate tutte le 24 trasformazioni implementate

### 19-20 Gennaio 2026 - TEST ESTRAI COMPLETATI ✅
- ✅ **Matrice 1 - Connessioni**: 5/5 database (PostgreSQL, MySQL, MariaDB, SQL Server, Oracle)
- ✅ **Matrice 2 - UI Canvas**: Drag&drop, preview, selezione colonne su tutti i DB
- ✅ **Matrice 3 - Filtri WHERE**: Tutti i 10 operatori su tutti i 5 DB
- ✅ **Matrice 4 - Colonne Calcolate**: 
  - Geometriche: PostgreSQL 9/9, MySQL 2/9, MariaDB 3/9, SQL Server 8/9, Oracle 9/9
  - Numeriche, Testo, Data, Math: 100% su tutti i DB
- ✅ **Matrice 5 - Esecuzione/Output**: CSV, GeoJSON, GPKG, SHP su tutti i DB
- ✅ **Matrice 6 - Tipi Dati**: INTEGER, VARCHAR, FLOAT, DATE, BOOLEAN, GEOMETRY su tutti i DB
- ✅ **Matrice 7 - Join**: INNER, LEFT, RIGHT, FULL OUTER, Spatial Join su tutti i DB
- ✅ **Copertura totale ESTRAI**: 357/368 test (97%)

### 18 Gennaio 2026
- ✅ FileManager con upload, selezione colonne, SRID manuale
- ✅ FileSourceNode con scroll colonne, tipi geometry
- ✅ Integrazione ManipolaEditor (sidebar + drag&drop)
- ✅ Integrazione ArricchisciEditor (sidebar + drag&drop)
- ✅ Backend orchestrator supporto file_source
- ✅ Backend api_connection_service supporto file_source
- ✅ Rilevamento automatico geometry type e SRID da GeoDataFrame
- ✅ Rilevamento WKT da stringhe CSV
- ✅ Fix Shapefile preview (.cpg vs .shp)

---

## 🟠 BACKLOG - Transformers Top 20 FME

| # | Transformer | Stato | Note |
|---|-------------|-------|------|
| 1 | AttributeCreator | ✅ | CalculatedColumn |
| 2 | AttributeFilter | ✅ | FilterNode |
| 3 | AttributeRenamer | ✅ | RenameNode |
| 4 | Bufferer | ✅ | BufferNode |
| 5 | Clipper | ✅ | ClipNode |
| 6 | Counter | ✅ | In Aggregate |
| 7 | FeatureMerger | ✅ | JoinNode |
| 8 | Aggregator | ✅ | AggregateNode |
| 9 | Tester | ❌ | IF/THEN multi-output |
| 10 | Reprojector | ✅ | Riproietta (completo) |
| 11 | StringReplacer | ✅ | Sostituisci Valori |
| 12 | Dissolver | ✅ | Dissolve per attributo |
| 13 | SpatialFilter | ❌ | Filtro spaziale |
| 14 | DuplicateFilter | ✅ | Distinct (Univoci) |
| 15 | Sorter | ✅ | Sort (Ordina) |
| 16 | GeometryValidator | ❌ | Valida/ripara geometrie |
| 17 | CoordinateExtractor | ❌ | Estrai X, Y, Z |
| 18 | PointOnAreaOverlayer | ✅ | Spatial Join (intersects) |
| 19 | NeighborFinder | ❌ | Trova più vicino |
| 20 | Generalizer | ✅ | Semplifica (Douglas-Peucker) |

**Stato: 15/20 completati (75%)** ⬆️ Era 40%

### Trasformazioni aggiuntive implementate (non in Top 20 FME)
- ✅ Elimina Colonne (Drop Columns)
- ✅ Riempi Nulli (Fill NA)
- ✅ Dividi Colonna (Split Column)
- ✅ Centroide
- ✅ Area (con conversione UTM automatica)
- ✅ Intersezione geometrica
- ✅ Unione geometrica
- ✅ Differenza geometrica
- ✅ Convex Hull

---

## 🎯 TARGET ESRI CONFERENCE - Maggio 2026

| Modulo | Attuale | Target |
|--------|---------|--------|
| Estrai | 98% | 98% |
| Manipola | 95% | 95% |
| Arricchisci | 40% | 90% |
| File Esterni | 100% ✅ | 100% |
| Upload | 0% | 100% |
| Scheduler | 0% | 100% |
| Transformers | 75% ⬆️ | 80% |
| Stream Input | 0% | 50% |
| Servizi Esterni | 0% | 70% |
| Linguaggio Naturale | 0% | MVP |

**Obiettivo globale: ~90%**

---

## 📁 Struttura Backend (dopo refactoring)

```
services/
├── orchestrator_service.py      # Entry point (129 righe)
├── orchestrator/
│   ├── __init__.py
│   ├── estrai_executor.py       # ESTRAI: execute + preview (357 righe)
│   ├── manipola_executor.py     # MANIPOLA: execute + preview (519 righe)
│   ├── enrich_executor.py       # ARRICCHISCI (58 righe)
│   ├── transform_operations.py  # 12 operazioni base (350 righe)
│   └── spatial_operations.py    # 12 operazioni spaziali (395 righe)
├── api_connection_service.py
├── file_source_service.py
├── query_executor.py
└── ...
```

---

*Ultimo aggiornamento: 20 Gennaio 2026*