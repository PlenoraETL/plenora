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

## ✅ COMPLETATI (questa sessione - 18 Gennaio 2026)

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

## 🔴 PRIORITÀ ALTA

### 1. Completare ARRICCHISCI (40% → 90%)
- [ ] Completare ApiEnrichNode configurazione
- [ ] Test pipeline completa file → API → output
- [ ] Gestione errori API migliorata
- [ ] Cache risultati API

### 2. Upload su Database
- [ ] Modulo UPLOAD nuovo
- [ ] Scrivere output su tabella PostGIS
- [ ] Append/Replace/Truncate modes
- [ ] Mapping colonne

### 3. Upload su Feature Service  
- [ ] Pubblicare su ArcGIS Online/Portal
- [ ] Autenticazione OAuth
- [ ] Aggiornamento feature esistenti

### 4. Scheduler
- [ ] UI creazione scheduler
- [ ] Selezione progetti (Estrai → Manipola → Arricchisci → Upload)
- [ ] Configura frequenza (cron)
- [ ] Log esecuzioni
- [ ] Notifiche errori

---

## 🟠 PRIORITÀ MEDIA - Transformers Top 20 FME

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

## 🟢 PRIORITÀ BASSA - Transformers GIS Extra

| Transformer | Funzione |
|-------------|----------|
| AreaCalculator | Calcola area |
| LengthCalculator | Calcola lunghezza |
| CenterPointReplacer | Centroide |
| HullReplacer | Convex hull |
| BoundingBoxReplacer | Bounding box |
| Offsetter | Offset linee/poligoni |
| VoronoiDiagrammer | Voronoi |
| Triangulator | Triangolazione |

---

## 🎯 TARGET ESRI CONFERENCE - Maggio 2026

| Modulo | Target |
|--------|--------|
| Estrai | 98% |
| Manipola | 95% |
| Arricchisci | 90% |
| File Esterni | 100% ✅ |
| Upload | 100% |
| Scheduler | 100% |
| Transformers | 80% (16/20) |

**Obiettivo globale: ~90%**

---

## 📁 File Modificati Oggi

### Frontend
- `FileManager.jsx` - Upload con SRID
- `FileManager.css` - Stili SRID
- `FileSourceNode.jsx` - Classe nowheel
- `FileSourceNode.css` - Max-height scroll
- `ManipolaEditor.jsx` - file_source_id serialization
- `ArricchisciEditor.jsx` - Integrazione file esterni
- `ArricchisciEditor.css` - Stili file esterni
- `files.js` - Parametro SRID

### Backend
- `file_routes.py` - Parametro SRID
- `file_source_service.py` - Geometry detection, SRID, SHP fix
- `api_connection_service.py` - Supporto file_source

---

*Ultimo aggiornamento: 18 Gennaio 2026*