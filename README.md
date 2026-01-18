# 📋 TODO DataFlow Manager - Gennaio 2026

## 📊 STATO PROGETTO ATTUALE

| Modulo | Completamento | Note |
|--------|---------------|------|
| **Estrai** | 98% ✅ | Completo, solo finiture |
| **Manipola** | 95% ✅ | Completo, file esterni integrati |
| **Arricchisci** | 40% 🔄 | File esterni integrati, core da completare |
| **File Esterni** | 100% ✅ | Upload, selezione colonne, SRID, integrato in tutti i moduli |
| **Upload** | 0% ❌ | Da fare |
| **Scheduler** | 0% ❌ | Da fare |

---

## 🎯 PIANO DI LAVORO

### FASE 1: REFACTORING (Prossima sessione)
Pulizia codice, rimuovi duplicati, centralizza logica comune.

**File da analizzare - Backend:**
- [ ] `orchestrator_service.py`
- [ ] `api_connection_service.py`
- [ ] `file_source_service.py`
- [ ] `query_executor.py`

**File da analizzare - Frontend:**
- [ ] `ManipolaEditor.jsx`
- [ ] `ArricchisciEditor.jsx`
- [ ] `ProjectSourceNode.jsx`
- [ ] `FileSourceNode.jsx`
- [ ] `ResultNode.jsx`

**Obiettivi refactoring:**
- [ ] Centralizzare logica caricamento dati (data_loader)
- [ ] Centralizzare logica export (data_exporter)
- [ ] Rimuovere codice duplicato tra Editor
- [ ] Rimuovere codice duplicato tra Node components

### FASE 2: TEST COMPLETO (Dopo refactoring)
Eseguire tutti i 155 test del Piano Test v3, fix bug trovati.

**Aree da testare:**
- [ ] Dashboard e Gestione Progetti (15 test)
- [ ] Modulo ESTRAI (35 test)
- [ ] Modulo MANIPOLA (30 test)
- [ ] Modulo FILE ESTERNI (15 test)
- [ ] Modulo ARRICCHISCI (20 test)
- [ ] Sicurezza (15 test)
- [ ] UI/UX Generale (15 test)
- [ ] DB Cross-Platform (10 test)

### FASE 3: INCREMENTO FUNZIONALITÀ (Dopo stabilizzazione)
Nuove feature solo su base stabile.

**Priorità:**
1. [ ] FileGDB (.gdb) come formato di OUTPUT
2. [ ] Completare ARRICCHISCI (40% → 90%)
3. [ ] Upload su Database PostGIS
4. [ ] Upload su Feature Service ArcGIS
5. [ ] Scheduler automazione pipeline

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

## ✅ COMPLETATI (18 Gennaio 2026)

### File Esterni - Integrazione Completa
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

## 🟠 BACKLOG - Transformers Top 20 FME (da fare in FASE 3)

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
| 10 | Reprojector | ⚠️ | Parziale |
| 11 | StringReplacer | ❌ | Replace con regex |
| 12 | Dissolver | ❌ | Dissolve per attributo |
| 13 | SpatialFilter | ❌ | Filtro spaziale |
| 14 | DuplicateFilter | ❌ | Rimuovi duplicati |
| 15 | Sorter | ❌ | Ordina per colonne |
| 16 | GeometryValidator | ❌ | Valida/ripara geometrie |
| 17 | CoordinateExtractor | ❌ | Estrai X, Y, Z |
| 18 | PointOnAreaOverlayer | ❌ | Point-in-polygon |
| 19 | NeighborFinder | ❌ | Trova più vicino |
| 20 | Generalizer | ❌ | Semplifica geometrie |

**Stato: 8/20 completati (40%)**

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
| Transformers | 40% | 80% |

**Obiettivo globale: ~90%**

---

*Ultimo aggiornamento: 18 Gennaio 2026*

### Backend
- `file_routes.py` - Parametro SRID
- `file_source_service.py` - Geometry detection, SRID, SHP fix
- `api_connection_service.py` - Supporto file_source

---

*Ultimo aggiornamento: 18 Gennaio 2026*