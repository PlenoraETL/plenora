# URBY-ETL — Inventory Report (Fase 0 Consolidamento)

**Prodotto**: 2026-04-19
**Scope**: read-only audit pre-Fase 1, riferimento vincolante = code charter (regole base 1/4/5/6/7/8/9, review 1/2/3, arch 1/2/3/4/5).
**Modalità**: single-session. Nessuna modifica applicata in questa fase.

## Nota metodologica sull'eta' git

La storia git compressa del repo mostra pochi commit (`78dcd6c Initial commit: Plenora Backend` del 2026-04-10, poi `df35930` stessa data per il security audit, poi la sequenza `01d74cf`/`9cf3e14`/`4196e2b` della migrazione PG). Dopo lo spostamento dei sorgenti sotto `backend/` il blame per `backend/app/...` e' compresso su pochi giorni (principalmente 2026-04-10..2026-04-19). Di conseguenza la colonna "eta" in questo report e' grossolana: tutti gli item sono nati nell'ultimo mese di lavoro intensivo. Dove rilevante ho usato `git log -S` per collocare l'introduzione di un flag o di un fallback, altrimenti annoto "≈fresh (≤10gg)".

## Nota metodologica sulla copertura

Gli agenti Explore hanno triato rapidamente la superficie. Per la Sezione 2 (fallback) ho **verificato in prima persona** che la conta reale dei pattern `except ...: pass` e `except ...: return default` e' molto piu' alta dei ~11 casi che l'agente aveva segnalato. Riporto i totali misurati e i casi piu' rappresentativi; l'enumerazione integrale esce dallo scope di Fase A (serve tipizzare, non listare).

---

## Sezione 1 — Path duplicati

### 1.1 `FeaturesService` CRUD vs `features_service_db` — duplicato di facciata post-migrazione PG

- **Canonico proposto**: `backend/app/core/features/features_service.py:132-152` (classe `FeaturesService`, metodi CRUD)
- **Duplicato**: `backend/app/core/features/features_service_db.py` (intero file, 66+ funzioni free `*_db()`)
- **Eta**: entrambi creati 2026-04-10, modificati 2026-04-19 (≈fresh)
- **Owner modulo**: `app/core/features/`
- **Motivo dichiarato**: docstring di `features_service_db.py:1-7` dice *"Patch per FeaturesService — sostituisce le operazioni CRUD su file JSON con operazioni su SQLite"*. La docstring di `features_service.py:1-13` descrive ancora *storage = filesystem JSON* (stale dopo la migrazione a DB).
- **Stato reale**: `FeaturesService.create/list/get/update/delete_connection` sono pass-through di una riga verso `features_service_db.create_connection_db(...)` ecc. Nessun chiamante esterno importa direttamente `features_service_db`; e' usato solo via `FeaturesService` (vedi import a `features_service.py:62-65`).
- **Verdetto proposto**: `unify(keep=features_service.py — metodi CRUD in-class, remove=features_service_db.py — motivo migrazione concluso)`. Conseguente: rinominare la docstring di `FeaturesService` per riflettere storage DB e aggiornare `ConnectionService` docstring (`app/services/connection_service.py:1-11` dice ancora *"Storage: SQLite tramite app.db.db_service"* — tecnicamente corretto solo perche' il modulo `db_service` e' ora PG, ma ambiguo).

### 1.2 `_read_csv_safe` vs `_read_csv_with_encoding_fallback` — duplicazione ammessa

- **Canonico proposto**: `backend/app/utils/dataframe.py:162` — `_read_csv_safe(file_path) -> pd.DataFrame`
- **Duplicato**: `backend/app/core/features/features_service.py:1073` — `_read_csv_with_encoding_fallback(path)`
- **Eta**: entrambi ≈fresh, ultimo commit 2026-04-19
- **Owner modulo**: utility di `app/utils/` vs copia locale a `app/core/features/`
- **Motivo dichiarato**: `features_service.py:1076-1078` — *"Stesso pattern di app/utils/dataframe.py:_read_csv_safe. Duplicato qui per evitare dipendenza circolare."*
- **Stato reale**: `app/core/features/features_service.py` importa gia' `app.utils.result_store` e `app.utils.logger` (linee 43-44). Non c'e' ciclo evidente: `utils/dataframe.py` non importa da `core/features/`. Il motivo dichiarato va verificato ma molto probabilmente e' obsoleto.
- **Verdetto proposto**: `investigate(che verifica: verificare assenza di ciclo import, poi unify con keep=utils.dataframe._read_csv_safe)`.

### 1.3 Dispatcher enrichment a 4 strategie — charter base 1/4 in tensione

- **Dispatcher**: `backend/app/core/arricchisci/enrichment_service.py:371` — `def execute(self, df)`, ramifica a quattro esecuzioni diverse ai link `:403` (async), `:411` (batch_native), `:413` (parallel), `:415` (internal).
- **Targets**:
  - `_execute_parallel_async(df)` — `:626` (dietro flag `ENABLE_ASYNC_ENRICHMENT`, fallback sync on exception a `:408-410`)
  - `_execute_batch_native(df)` — `:808`
  - `_execute_parallel(df)` — `:440` (ThreadPoolExecutor)
  - `_execute_internal(df)` — `:1161` (single row)
- **Eta**: ≈fresh (tutte in initial commit 2026-04-10, async probabilmente aggiunto nel security audit round)
- **Owner modulo**: `app/core/arricchisci/`
- **Motivo dichiarato**: commento a `:621` — *"ESECUZIONE PARALLELA ASYNC (P0 — feature flag ENABLE_ASYNC_ENRICHMENT)"* + *"Speedup 10x circa rispetto a ThreadPoolExecutor"*.
- **Osservazione charter**: la presenza simultanea di `_execute_parallel` e `_execute_parallel_async` e' il caso-scuola della regola "flag non tengono in vita due architetture" (base 7). Batch_native e internal sono shape diverse (batch API vs single row) e la loro coesistenza e' giustificabile, ma async+sync-thread non lo e'.
- **Verdetto proposto**: `unify(keep=_execute_parallel_async come path canonico quando len(df)>1 e non batch, remove=_execute_parallel)` **OPPURE** `remove flag ENABLE_ASYNC_ENRICHMENT + _execute_parallel_async` (se il path async non e' mai stato attivato in prod). Decisione utente: prima verificare se il flag e' mai stato set a `true` sul server (log/env di prod). Finche' dubbio: `mark-legacy(_execute_parallel, piano rimozione dopo rollout completo flag)`.

### 1.4 Writer chain `carica/` — Strategy giustificato, da documentare come invariante

- **Entry**: `backend/app/core/carica/database_writer.py` (~2000 righe) implementa `DestinationWriter` da `writer_base.py:41`.
- **Optimization**: `backend/app/core/carica/bulk_writers.py` (~390 righe) — chiamato solo da `database_writer.py:1952` con fallback su `executemany` se `BulkInsertNotSupportedError`.
- **Eta**: ≈fresh. `bulk_writers.py` aggiunto di recente (non tracked in git prima dell'ultimo round, appare tra `??` in `git status`).
- **Owner**: `app/core/carica/`
- **Motivo dedotto**: pattern Strategy + ottimizzazione isolata — allineato a charter arch 1/5.
- **Verdetto proposto**: `keep(justified: pattern Strategy con fallback misurato, documenta in invariants come path canonico)`. Nessuna rimozione, solo formalizzare.

### 1.5 IMDF: `loader.py`/`persist.py`/`storage.py`/`validator.py`/`arcgis_uploader.py`

- **Directory**: `backend/app/core/imdf/` — 5 moduli sorella (1314 loc arcgis_uploader, 828 validator, 672 router, 308 loader, 270 storage, 171 persist).
- **Eta**: ≈fresh (tutto `??` in git status — mai committato prima)
- **Owner**: `app/core/imdf/`
- **Sospetto duplicato**: `loader` + `persist` + `storage` sono tre nomi che, nel dominio IMDF, possono sovrapporsi ("carica dal filesystem", "persiste nello storage", "gestisce il blob store"). Non esaminati in dettaglio in Fase A — un canvas di 3 moduli con nomi affini merita rilettura.
- **Verdetto proposto**: `investigate(che verifica: mappa quale modulo possiede ingress/egress verso disco, blob store, DB; verifica se esiste responsabilita' duplicata tra loader/persist/storage)`.

### 1.6 Crypto key sourcing — 5 env var per la stessa chiave

- **Path**: `backend/app/shared/crypto.py:113-119`
- **Chain**: `PLENORA_ENCRYPTION_KEY` → `URBY_ENCRYPTION_KEY` → `CONN_ENCRYPT_KEY` → `SECRET_KEY` → `JWT_SECRET_KEY`
- **Eta**: ≈fresh (2026-04-10)
- **Owner**: `app/shared/crypto.py`
- **Motivo dichiarato**: docstring `:19-24` — alias retro-compat tra vecchio e nuovo nome durante il rollout (*"nome canonico nuovo (consigliato per nuovi deploy) / URBY_ENCRYPTION_KEY alias retro-compat, ancora supportato"*).
- **Verdetto proposto**: `mark-legacy(URBY_ENCRYPTION_KEY + CONN_ENCRYPT_KEY con piano rimozione dopo verifica .env di prod; keep=PLENORA_ENCRYPTION_KEY + fallback a SECRET_KEY/JWT_SECRET_KEY solo come emergenza)`. Gia' documentato (buon punto di partenza).

### 1.7 Nomi "connessione" paralleli — `ConnectionService` vs `FeaturesService`

- **Servizi paralleli**:
  - `backend/app/services/connection_service.py:35` — `class ConnectionService` (connessioni DB)
  - `backend/app/core/features/features_service.py:88` — `class FeaturesService` (connessioni ArcGIS)
- **Comment di progettazione**: `features_service.py:4-8` ammette esplicitamente *"Servizio parallelo a ConnectionService per gestire le connessioni ad ArcGIS Server"*.
- **Verdetto proposto**: `keep(justified: separation of concerns — DB vs ArcGIS hanno auth/test differenti, unificare sarebbe over-engineering)`. Documentare in invariants come due famiglie diverse di "connection".

### 1.8 `db/db_service.py` facade + 3 servizi concreti

- **Path**: `backend/app/db/db_service.py:1-51` (facade 51 loc) + `config_db_service.py` (866 loc) + `results_db_service.py` (228 loc) + `tickets_db_service.py` (379 loc).
- **Eta**: ≈fresh.
- **Motivo dichiarato**: `db_service.py` header comment (*"Migrato da 3 SQLite separati a un singolo PostgreSQL"*).
- **Verdetto proposto**: `keep(justified: facade mantiene API stabile post-migrazione, split per responsabilita')`. Documentare in invariants.

---

### Riepilogo Sezione 1

| # | Item | Verdetto proposto |
|---|------|-------------------|
| 1.1 | FeaturesService + features_service_db | unify |
| 1.2 | _read_csv_safe + _read_csv_with_encoding_fallback | investigate → unify probabile |
| 1.3 | 4 strategie enrichment (async+parallel+batch+internal) | unify o remove flag |
| 1.4 | Writer chain carica/ | keep + documenta |
| 1.5 | IMDF loader/persist/storage | investigate |
| 1.6 | 5 env var per chiave crypto | mark-legacy parziale |
| 1.7 | ConnectionService vs FeaturesService | keep + documenta |
| 1.8 | db_service facade | keep + documenta |

**Totale: 8 famiglie. 2 unify, 1 unify probabile, 2 investigate, 3 keep-documenta.**

---

## Sezione 2 — Fallback attivi

### Riconteggi grezzi (da grep verificato)

| Pattern | Occorrenze | Note |
|---------|-----------|------|
| `except ...: pass` | **121** su 42 file | molti sono `ImportError: pass` per dipendenze opzionali (legittimo), il resto e' misto |
| `except ... return None/False/True/[]/{}/""` | **31** su 17 file | silent default return, quasi sempre senza log |
| `except Exception as e: logger.warning` + return | non contato | best-effort con log (da triare) |

**Conclusione**: l'inventario esaustivo dei ~150+ siti esce dallo scope di Fase A. Di seguito i cluster piu' critici, classificati per rischio.

### 2.1 CRITICO — `crypto.py` encrypt: fallback a plaintext su errore

- **Path**: `backend/app/shared/crypto.py:213-218`
- **Snippet**:
  ```python
  try:
      token = _get_fernet().encrypt(value.encode("utf-8")).decode("utf-8")
      return f"{ENC_PREFIX}{token}"
  except Exception as e:  # pragma: no cover — fallback difensivo
      logger.warning(f"crypto.encrypt fallita: {e}")
      return value
  ```
- **Eta**: ≈fresh (2026-04-10 security audit round).
- **Owner**: `app/shared/crypto.py`
- **Motivo dichiarato**: `:205-207` — *"preferiamo non bloccare il save di una connessione per un bug crypto; l'utente vede comunque il warning nei log"*.
- **Charter check**:
  - Motivo ✓ (commentato)
  - Perimetro ✓ (solo encrypt)
  - Log ✓ (warning)
  - Piano ✗ (nessun TODO/issue)
- **Rischio**: se l'istanza Fernet non inizializza (es. env var sparita a runtime), il fallback scrive la password IN CHIARO nel DB senza alcun crash. Il `logger.warning` puo' sparire in un log rotation e l'amministratore non se ne accorge.
- **Verdetto proposto**: `mark-or-remove` — sostituire con `raise RuntimeError("crypto.encrypt broken — refusing to save plaintext")`. Questo e' dato critico (base 8 = fail-fast). Il charter vieta il silent-compromise.

### 2.2 ALTO — `crypto.py` decrypt: restituisce `None` su token invalido

- **Path**: `backend/app/shared/crypto.py:237-247`
- **Snippet**:
  ```python
  try:
      return _get_fernet().decrypt(token.encode("utf-8")).decode("utf-8")
  except InvalidToken:
      logger.error("... Restituisco None.")
      return None
  except Exception as e:  # pragma: no cover
      logger.error(f"crypto.decrypt fallita: {e}")
      return None
  ```
- **Motivo dichiarato**: docstring `:227-230` — *"l'app continua a girare ma la connessione di terze parti fallira' al primo uso, dando all'utente un errore visibile invece di un crash silenzioso"*.
- **Charter check**: motivo ✓, perimetro ✓, log ✓ (error), piano ✗.
- **Verdetto proposto**: `keep(3/4) + documenta in fallback autorizzati` (la motivazione e' forte: preferire errore-per-connessione a crash-app). Aggiungere piano: rimuovere quando policy "se crypto fallisce app down" sara' accettabile.

### 2.3 ALTO — `enrichment_service.py` async→sync silent fallback

- **Path**: `backend/app/core/arricchisci/enrichment_service.py:403-410`
- **Snippet**:
  ```python
  if _can_use_async:
      logger.info(f"[ASYNC] Esecuzione enrichment async (pool via ENRICHMENT_POOL_SIZE)")
      import asyncio
      try:
          result_df, stats, errors = asyncio.run(self._execute_parallel_async(df))
      except Exception as e:
          logger.error(f"[ASYNC] Errore nel path async, fallback a sync: {e}", exc_info=True)
          result_df, stats, errors = self._execute_parallel(df)
  ```
- **Charter check**: motivo parziale (feature flag), perimetro ✓, log ✓, piano ✗.
- **Rischio**: il fallback maschera un bug reale nel path async (es. regressione httpx). In prod fallback silente + raddoppio rate-limit. Correlato a 1.3.
- **Verdetto proposto**: `mark-or-remove` — legato a 1.3 (unify execution paths).

### 2.4 MEDIO — `database_writer.py` schema-introspection fallback con return vuoto

- **Path**: `backend/app/core/carica/database_writer.py:195-240` (quattro funzioni: `table_exists`, `get_existing_columns`, `get_schemas`, `get_tables`)
- **Pattern comune**: `except Exception as e: logger.error(f"[CRASH] ..."); return False|[]`
- **Charter check**: motivo dedotto (lettura metadata non deve bloccare), log ✓ (`[CRASH]` tag), perimetro ✓ (introspection only), piano ✗.
- **Rischio**: `table_exists() == False` diventa indistinguibile tra "tabella non c'e'" e "non siamo riusciti a verificare". Il chiamante potrebbe tentare CREATE TABLE su un DB dove il check ha fallito per permessi/rete.
- **Verdetto proposto**: `mark-or-remove` — preferibilmente `raise` e lasciare che il chiamante gestisca (fail-fast charter base 8). Almeno differenziare i ritorni (es. `Optional[bool]`/`None` per distinguere).

### 2.5 MEDIO — `bulk_writers.py` cursor close silent in finally

- **Path**: `backend/app/core/carica/bulk_writers.py:208-212`
- **Snippet**:
  ```python
  finally:
      try:
          cur.close()
      except Exception:
          pass
  ```
- **Charter check**: motivo ✗, perimetro ✓ (cleanup), log ✗, piano ✗.
- **Verdetto proposto**: `mark-or-remove` — aggiungere `logger.debug` oppure rimuovere il try (`cursor.close()` in psycopg2 e' idempotente).

### 2.6 MEDIO — `query_executor.py` cinque `except: pass` nel pool management

- **Path**: `backend/app/db/query_executor.py:198-200`, `225-227`, `240-242`, `576-578`, `583-585`, `672-674`, `677-680`
- **Pattern**: evict/dispose engine, `ALTER SESSION` Oracle, SSL disconnect — cleanup puro senza log.
- **Charter check**: motivo ✓ (commenti "Non bloccante — il timeout di default di Celery fa da fallback" e "Prosegui con il default se non ha i permessi"), perimetro ✓ (cleanup/Oracle-specific), log ✗ (neanche debug), piano ✗.
- **Verdetto proposto**: `mark-or-remove` — aggiungere `logger.debug(...)` uniformando il pattern (best-effort con tracing minimo).

### 2.7 MEDIO — `logs/router.py` silent JSON parse

- **Path**: `backend/app/core/logs/router.py:21-24`
- **Snippet**:
  ```python
  if d.get("details"):
      try:
          d["details"] = json.loads(d["details"])
      except Exception:
          pass
  ```
- **Charter check**: motivo ✗ (commento assente), perimetro ✓ (visualizzazione log viewer), log ✗, piano ✗.
- **Rischio**: dettagli log corrotti vengono mostrati come stringa invece di dizionario — display only. Non critico.
- **Verdetto proposto**: `mark-or-remove` — aggiungere `logger.debug` minimo e commento ("corrupt json in log details → leave as string").

### 2.8 ALTO — `file_service.py` — 21 `except: pass` senza pattern uniforme

- **Path**: `backend/app/services/file_service.py` — 21 occorrenze di `except ...: pass` tra `:438-1699`.
- **Esempi**:
  - `:438-439` `except Exception: pass  # pd.to_datetime fallisce → non è data` — legittimo (type detection).
  - `:547-548` `except Exception as _e: pass  # Rilevamento geometria fallito (non critico)` — legittimo.
  - `:574-575`, `:579-580`, `:876-877`, `:1151-1156`, `:1399-1406`, `:1440-1446`, `:1520-1526`, `:1691-1699` — senza commento, forma sospetta di swallow.
- **Verdetto proposto**: `mark-or-remove` — audit riga-per-riga in Fase B: chi ha commento legittimo e log → keep; chi non ha ne' commento ne' log → add log o raise. Spezzare in commit piccoli.

### 2.9 ALTO — `features_service.py:1069` fallback GeoJSON→CSV silent

- **Path**: `backend/app/core/features/features_service.py:1066-1070`
- **Snippet**:
  ```python
  try:
      import geopandas as gpd
      return gpd.read_file(path)
  except Exception:
      return _read_csv_with_encoding_fallback(path)
  ```
- **Charter check**: motivo ✗ (nessun commento), perimetro unclear, log ✗, piano ✗.
- **Rischio**: un `.gpkg` corrotto viene letto come CSV → output ibrido senza warning.
- **Verdetto proposto**: `mark-or-remove` — almeno loggare il tipo di errore geopandas e convertire in `ValueError` se il file non e' ne' GeoJSON ne' CSV plausibile.

### 2.10 SOSPETTO — 10 `except` in `db/config_db_service.py`, 48 in `carica/database_writer.py`, 27 in `features/routes.py`, 25 in `arricchisci/api_connector.py`

- **Volume**: contato via `grep ... --count`. Non tutti sono fallback (alcuni sono classi di eccezione tipizzata + re-raise).
- **Verdetto proposto**: `investigate` — Fase B inizia qui con triage riga-per-riga, partendo dai file con piu' except in modo decrescente.

### 2.11 MEDIO-ALTO — `pg_connection.py:242-245` silent putconn

- **Path**: `backend/app/db/pg_connection.py:242-245`
- **Snippet**: `try: _pool.putconn(conn); except Exception: pass`
- **Verdetto proposto**: `mark-or-remove` — `logger.warning` se putconn fallisce (pool exhaustion marker).

### Cluster osservati

| Modulo | Silent patterns osservati | Giudizio |
|--------|---------------------------|----------|
| `app/shared/crypto.py` | 2 return default su encrypt/decrypt | 1 critico, 1 accettabile |
| `app/db/query_executor.py` | 7 `except: pass` in pool/session mgmt | cleanup senza log |
| `app/db/pg_connection.py` | 1 silent putconn | cleanup senza log |
| `app/db/config_db_service.py` | 10 except | da triare — include transazioni |
| `app/db/migrations_runner.py` | 2-5 except | da triare |
| `app/core/carica/database_writer.py` | 48 except, 10 `pass`, 1 return default | mix introspection/write |
| `app/core/carica/bulk_writers.py` | 3 `except: pass` | cleanup + recovery path |
| `app/core/features/features_service.py` | 1 `pass` silent geopandas fallback | critico (type detection) |
| `app/core/features/features_service_db.py` | — | nuovo file, solo CRUD |
| `app/core/features/routes.py` | 27 except | volume alto, da triare |
| `app/core/arricchisci/api_connector.py` | 25 except, 7 `pass` | retry logic da validare |
| `app/core/arricchisci/enrichment_service.py` | async→sync silent fallback + 3 `pass` | charter base 7 violato |
| `app/core/intermediate/writer.py` | 5 `except`, 4 `pass` | scrittura DB — da audit |
| `app/core/scheduler/execution_manager.py` | 15 except, 2 `pass` | dispatch tasks |
| `app/services/file_service.py` | 39 except, 21 `pass` | mix type-detection + cleanup |
| `app/core/datasources/sharepoint.py` | 15 except, 1 `pass`, 4 return | network + auth |
| `app/utils/logger.py` | 2 `pass` nel logger stesso | accettabile (no recursion) |

### Top-10 fallback piu' preoccupanti

1. **crypto.py:213-218** — encrypt fallback a plaintext (DATI CRITICI)
2. **enrichment_service.py:403-410** — async→sync silent fallback (charter base 7)
3. **database_writer.py:195-240** — 4 funzioni introspect con return vuoto su errore (dato critico, mask di problema reale)
4. **features_service.py:1066-1070** — GeoJSON → CSV silent fallback (type detection)
5. **file_service.py** — 21 `except: pass` sparsi, molti senza commento
6. **intermediate/writer.py** — 5 except, 4 `pass` nella scrittura DB intermedia
7. **api_connector.py** — 25 except in retry loop (verificare che non mascherino rate-limit errors veri)
8. **query_executor.py** — 7 pattern silent in pool/session management (cleanup senza log)
9. **logs/router.py:21-24** — JSON parse silent (display ma viola policy)
10. **bulk_writers.py:208-212** — cursor close silent in finally

### Riepilogo Sezione 2

- **Numero totale grezzo (pattern `pass` + `return default`)**: ~152 siti in 45 file.
- **Verdetti proposti**:
  - `remove` (dati critici, charter base 8): ~1 (crypto encrypt silent)
  - `mark-or-remove` (fallback plausibile ma incompleto): ~10 cluster identificati sopra (somma conservativa di 30-50 siti)
  - `keep (4/4 properties)` o `keep (3/4) documentato`: da triare individualmente in Fase B
  - `investigate`: volume alto su `file_service.py`, `api_connector.py`, `config_db_service.py`, `database_writer.py`, `routes.py` — triage riga-per-riga.

---

## Sezione 3 — Feature flag

Tutti gli item sono stati verificati in `backend/` (codice + `.env.example` + tests). I flag veri sono 6; altri 3 env var sono configurazione non-flag.

### 3.1 `ENVIRONMENT` / `ENV`

- **Read**: `backend/app/main.py:65`, `backend/app/shared/crypto.py:125`, vari `if env == "production"`.
- **Documentato**: `backend/.env.example`, docstring `crypto.py`.
- **Introdotto**: 2026-04-10 (initial).
- **Stato**: kill-switch prod/dev. Disabilita `/docs`, fa fail-fast su chiavi mancanti.
- **Seconda arch**: no (attiva fail-fast, non tiene due path vivi).
- **Verdetto proposto**: `keep + documenta in invariants`.

### 3.2 `ENABLE_ASYNC_ENRICHMENT`

- **Read**: `backend/app/core/arricchisci/enrichment_service.py:396`.
- **Documentato**: comment inline, nessun README.
- **Introdotto**: commit 2026-04-10 security audit (non precedente — cercabile con `git log -S`).
- **Stato**: default `false`. Routing tra sync legacy (`_execute_parallel`) e async (`_execute_parallel_async`). Fallback silente su errore (vedi 2.3).
- **Seconda arch**: **SI'** — due path di esecuzione vivi contemporaneamente (charter base 7 violato).
- **Verdetto proposto**: `investigate → remove flag + unify path` una volta deciso quale tenere. Se mai attivato in prod (da verificare sul server): `remove flag, keep new path`. Se mai attivato: `remove flag, keep old path`.

### 3.3 `CARICA_BULK_INSERT`

- **Read**: `backend/app/core/carica/bulk_writers.py:392`, `backend/app/core/carica/database_writer.py:1926`.
- **Documentato**: docstring `bulk_writers.py:382-391`, test contract `tests2/carica/unit/test_bulk_writers_contract.py`.
- **Stato**: default `auto`, valori `on`/`off`. Operational kill-switch: disabilita bulk su regressione DB senza redeploy.
- **Seconda arch**: fallback `executemany` e' safety-valve, non path alternativo.
- **Verdetto proposto**: `keep + documenta in invariants` (flag operazionale legittimo).

### 3.4 `MSSQL_FAST_EXECUTEMANY`

- **Read**: `backend/app/db/query_executor.py:276`.
- **Documentato**: docstring `query_executor.py:260-267`, test `test_engine_factory_mssql_fast_executemany.py`.
- **Stato**: default `on`, opt-out via `off`. Workaround per casi edge MSSQL (precisione DATETIME2).
- **Verdetto proposto**: `keep + documenta in invariants` (operational, dialect-specific).

### 3.5 `PLENORA_DISABLE_DEFAULT_KEY` + alias `URBY_DISABLE_DEFAULT_KEY`

- **Read**: `backend/app/shared/crypto.py:159-160` (entrambi).
- **Documentato**: docstring `crypto.py:156-164`, `.env.example:34-35`.
- **Stato**: dev opt-in per replicare fail-fast di prod. Alias `URBY_*` retro-compat.
- **Seconda arch**: no (spegne solo dev-default key).
- **Verdetto proposto**: `keep + unify nome (remove URBY_DISABLE_DEFAULT_KEY dopo verifica .env)`. Aggiunge coda a 1.6.

### 3.6 `URBY_ALLOW_LOOPBACK_OUTBOUND`

- **Read**: `backend/app/shared/api_utils.py:93`.
- **Documentato**: docstring `api_utils.py:66-79`, test `test_validate_external_url_contract.py`, `tests2/functional/conftest.py:45`.
- **Stato**: test-only. Prod non deve settare.
- **Verdetto proposto**: `keep + documenta` (opt-in legittimo, usato solo in test harness).

### 3.7 `ENRICHMENT_POOL_SIZE` (borderline)

- **Read**: `backend/app/core/arricchisci/enrichment_service.py:671`.
- **Stato**: tuning numerico (default 50, clamp [5, 200]). Non toggle.
- **Verdetto proposto**: `config-not-flag` (trattare come parametro numerico, non flag).

### 3.8 `FILE_SOURCE_S3_THRESHOLD_BYTES`, `TEMP_ORPHAN_CLEANUP_HOURS` (config-not-flag)

- Operational knobs numerici, non toggle. Documentati in `docs/ops/runtime-prereqs.md`.
- **Verdetto proposto**: `config-not-flag`.

### 3.9 `CLOUD_PROVIDER`

- **Read**: `backend/app/shared/cloud_backends.py:235`.
- **Stato**: selector enum (attualmente solo AWS implementato). Non boolean flag.
- **Verdetto proposto**: `keep + documenta` (provider enum architetturale).

### Flag crypto-key (coda di 1.6)

| Env var | Tipo | Verdetto |
|---------|------|----------|
| `PLENORA_ENCRYPTION_KEY` | canonical | keep |
| `URBY_ENCRYPTION_KEY` | alias retro-compat | mark-legacy (rimuovi post-verifica .env) |
| `CONN_ENCRYPT_KEY` | alias legacy | mark-legacy |
| `SECRET_KEY` | fallback jwt-shared | investigate (chiarire se intended) |
| `JWT_SECRET_KEY` | fallback jwt-shared | investigate (chiarire se intended) |

### Riepilogo Sezione 3

- **Flag veri**: 6 (`ENVIRONMENT`, `ENABLE_ASYNC_ENRICHMENT`, `CARICA_BULK_INSERT`, `MSSQL_FAST_EXECUTEMANY`, `PLENORA_DISABLE_DEFAULT_KEY`, `URBY_ALLOW_LOOPBACK_OUTBOUND`).
- **Aliases retro-compat**: 3 (`URBY_ENCRYPTION_KEY`, `URBY_DISABLE_DEFAULT_KEY`, `CONN_ENCRYPT_KEY`).
- **Selectors/config-not-flag**: 3 (`CLOUD_PROVIDER`, `FILE_SOURCE_S3_THRESHOLD_BYTES`, `TEMP_ORPHAN_CLEANUP_HOURS`, `ENRICHMENT_POOL_SIZE`).
- **Verdetti**:
  - `keep + documenta`: 5 (ENVIRONMENT, CARICA_BULK_INSERT, MSSQL_FAST_EXECUTEMANY, URBY_ALLOW_LOOPBACK_OUTBOUND, CLOUD_PROVIDER)
  - `investigate → remove/unify`: 1 (ENABLE_ASYNC_ENRICHMENT) **+ 3 aliases crypto** (URBY_ENCRYPTION_KEY, URBY_DISABLE_DEFAULT_KEY, CONN_ENCRYPT_KEY)
  - `config-not-flag`: 3

---

## Sezione 4 — Policy errore per modulo

**Policy tassonomia**:
- **Fail-fast** = exception propagata via `raise` o `HTTPException`. Richiesto su dati, transazioni, metadata, sicurezza.
- **Best-effort** = `logger.error/warning` + continuazione. Accettabile solo per cleanup, metriche, osservabilita'.
- **Silent** = eccezione ingoiata senza log o con default opaco. Sempre rosso (charter base 9).
- **Mista/incoerente** = stesso modulo con punti fail-fast e silent senza motivazione → `unify`.

### Tabella modulo → policy dominante

| Modulo | Policy dominante | Evidenza (path:linea) | Anomalie | Verdetto |
|--------|------------------|------------------------|----------|----------|
| `app/core/auth/` | Fail-fast | `auth.py:126-145` fail-fast JWT; routes: HTTPException 401/403 | `auth.py:989-991` swallow su `delete_group_cascade` | `align`: re-raise esplicito |
| `app/core/connections/` | Fail-fast | `router.py:116, 193-200` HTTPException su 404/500 | — | `keep` |
| `app/core/projects/` | Fail-fast | `router.py:91-97, 195-196` logger.error + HTTPException | `router.py:298-315` mix canvas error handling | `align` |
| `app/core/files/` | Fail-fast | `router.py:183-184, 506-508` HTTPException | `router.py:153-154` vs `:241-246` log inconsistente | `align` (uniform log level) |
| `app/core/datasources/` | Fail-fast | `router.py:245-249` DataSourceError+HTTPException | `router.py:200-216` silent try/except senza log; `sharepoint.py` 15 except con 4 return vuoto | `align` (logging mancante) |
| `app/core/estrai/` | Mista (preview/execute) | `executor.py:156` execute raise; `:292-293, 438-439` preview logged+swallowed | Preview silenzia sempre | `align`: policy esplicita preview vs execute |
| `app/core/manipola/` | Mista | `executor.py:201-204` raise lineage cycle; `:125, 260-261, 734-735` logger.warning su CRS/intermediate non-bloccanti; `:337` preview swallow; `:546-548` generic swallow post-log | Ingestione pattern inconsistente | `unify`: separare chiaramente critici da osservabilita' |
| `app/core/arricchisci/` | Mista | `executor.py` fail-fast principale; `enrichment_service.py:403-410` async→sync silent fallback; `api_connector.py` retry loop con 25 except | Preview swallow (`executor.py:280`); dispatcher charter-violante | `unify` (legato a 1.3/2.3) |
| `app/core/carica/` | Fail-fast (router) | `router.py:149-180` HTTPException 500; writers con log `[CRASH]` | `database_writer.py:195-240` introspection return vuoto; `bulk_writers.py:208-212` cleanup silent | `align`: introspection → raise, cleanup → debug log |
| `app/core/features/` | Mista | `features_service.py:422-423, 904-905, 1069` silent/naked except; `features_service.py:1056-1058` warning+pass | Diverso da pattern router; query silenziose | `unify` (logging obbligatorio) |
| `app/core/imdf/` | Mista | `router.py:82-83` warning non-fatal; `:327-329` HTTPException; `:358-410` multi silent; `:477-478` S3 fallback warning | Diverse semantiche di fallback | `align`: policy unica fallback vs error |
| `app/core/eseguibili/` | Fail-fast | `router.py` HTTPException-based | `router.py` 19 except, 6 `pass` da triare | `align` probabile |
| `app/core/scheduler/` | Fail-fast | `router.py:112-139` pattern uniforme; `execution_manager.py` 15 except | `execution_manager.py:576-585` silent oracle tuning (cleanup) | `keep + align piccoli` |
| `app/core/orchestrator/` | Fail-fast | `service.py:245-246` HTTPException; `router.py` 19 except | Maggior parte delegata a worker | `keep` |
| `app/core/tickets/` | Fail-fast | `router.py:66-293` HTTPException + validazione | `router.py:456-457` warning+swallow | `align` |
| `app/core/admin_backups/` | Fail-fast | `service.py` sampled HTTPException | `service.py:134-135` silent AttributeError | `align` (minor) |
| `app/core/admin_system_status/` | Fail-fast | `service.py` 17 except, sampled HTTPException | Volume alto, triare | `investigate` |
| `app/core/artifacts/` | Fail-fast | `resolver.py, source_loader.py, types.py` raise on type errors | — | `keep` |
| `app/core/intermediate/` | Mista | `router.py:121-123, 168-170, 218-220, 336-338` generic→HTTPException 500; `writer.py:` 5 except, 4 `pass` | Writer non re-raise, delega a router | `align` (writer deve raise) |
| `app/core/logs/` | Silent (display) | `router.py:21-24` JSON parse silent | Unico punto silent in modulo di display | `align` (add debug log) |
| `app/shared/` | Mista | `authz_helpers.py:33-68` HTTPException; `columns_metadata.py:132-153` typed + raise; `crypto.py:216-217, 239-247` con warning/error + fallback; `token_blacklist.py:146-180` Redis warning+graceful; `sharing.py:164-169` silent | Pattern misti legittimi per layer condiviso | `align`: separare "fail-closed" documentato da "silent swallow" |
| `app/db/` | Fail-fast | `db_service.py` facade; `config_db_service` transazionale | `pg_connection.py:242` silent putconn; `query_executor.py` 7 silent cleanup | `align` (cleanup logging) |
| `app/services/` | Fail-fast + molti silent | `connection_service.py` raise; `file_service.py` 39 except, 21 `pass`; `orphan_cleanup_service.py`, `task_service.py`, `project_service.py` sample fail-fast | `file_service.py` dominante silent | `unify` (file_service e' outlier) |
| `app/utils/` | Fail-fast | `path_policy.py` raise; `logger.py` silent OK (no-recursion); `result_store.py` 10 except | `result_store.py` 10 except da triare | `align` |
| `workers/` | Mista | `pipeline_task.py:239-243` Exception→raise; `:184-185, 225-226` warning non-fatale (cleanup intermediate); `:346-348` preview raise | Split corretto critici vs non-bloccanti | `keep + documenta` |

### Anomalie cross-modulo (charter base 9)

| Coppia | Operazione | Policy A | Policy B | Giudizio |
|--------|------------|----------|----------|----------|
| `carica` vs `intermediate` | DB write | HTTPException 500 esplicita | `writer.py` non re-raise, delega | Asimmetria data-integrity → `unify` |
| `manipola` vs `arricchisci` | Preview | Swallow+log | Swallow+log | Consistente ma entrambi violano policy (preview dovrebbe avere policy esplicita) → `align` shared |
| `features` vs `datasources` | Query remota | Naked except sparso | HTTPException uniforme | Visibilita' errori inconsistente → `unify` |
| `imdf` vs `intermediate` | Read fallback | Warning, cache stale OK | Error, HTTPException | Semantic fallback diversa → `align` policy fallback |
| `scheduler` vs `orchestrator` | Task dispatch | Fail-fast uniforme | Fail-fast uniforme | Allineato → `keep` |
| `logs` vs `tickets` | Attachment/display | Silent no-log | Warning + swallow | Inconsistente → `align` (sempre logga) |

### Riepilogo Sezione 4

- **Fail-fast pulito**: `connections`, `auth` (con minor fix), `scheduler`, `orchestrator`, `artifacts`, `projects` (con minor fix), `tickets` (con minor fix), `eseguibili`, `utils` (maggior parte), `db` (maggior parte), `admin_backups`, `admin_system_status` (con investigate).
- **Mista da uniformare**: `estrai`, `manipola`, `arricchisci`, `features`, `imdf`, `intermediate`, `shared`, `services` (outlier file_service).
- **Silent da rimuovere**: `logs/router.py:21-24` (JSON parse). Alcuni punti in `database_writer.py` introspection.
- **Anomalie cross-modulo**: 6 coppie; 4 richiedono `unify`, 2 `align`.

**Totale anomalie policy proposte**: circa **10 moduli** richiedono intervento di align (anomalie interne), **6 coppie** cross-modulo richiedono unify/align, **1 silent puro** da rimuovere.

---

## Sintesi esecutiva

### Numeri complessivi (valori proposti, pre-checkpoint utente)

| Sezione | Conteggio | Verdetti proposti |
|---------|-----------|-------------------|
| **1 — Path duplicati** | 8 famiglie | 2 unify + 1 unify probabile, 2 investigate, 3 keep+documenta |
| **2 — Fallback attivi** | ~152 siti grezzi (121 `pass` + 31 `return default`), ~10 cluster critici | 1 remove (crypto encrypt), ~10 mark-or-remove, volume alto investigate |
| **3 — Feature flag** | 6 flag veri + 3 aliases retro-compat + 3 config-not-flag | 5 keep+doc, 1+3 investigate→remove/unify |
| **4 — Policy errore** | 24 moduli mappati + 6 coppie cross-modulo | ~10 moduli align, 4-6 unify cross-modulo, 1 silent puro |

### Casi dubbi da sottoporre a checkpoint utente

1. **ENABLE_ASYNC_ENRICHMENT + `_execute_parallel` vs `_execute_parallel_async`** (1.3 + 2.3 + 3.2) — decidere: mai attivato in prod → remove flag+async. Gia' attivato e stabile → remove sync+mark flag kill-switch. Serve lettura `.env` del server prod.
2. **crypto.py encrypt fallback a plaintext** (2.1) — decidere: `raise RuntimeError` (fail-fast) o mantenere best-effort con alert severity alta. Utente: la tua tolleranza al rischio "password plaintext in DB" vs "app crash".
3. **database_writer.py introspection return vuoto** (2.4) — decidere: `raise` o `Optional[T]` per differenziare "non trovato" da "non verificato".
4. **Alias retro-compat `URBY_ENCRYPTION_KEY` / `CONN_ENCRYPT_KEY`** (1.6 + 3.5) — serve verifica del `.env` di prod (lab.plenora.it, project_deployment) per capire quale nome e' effettivamente settato prima di rimuovere.
5. **IMDF `loader`/`persist`/`storage`** (1.5) — non analizzato in dettaglio in Fase A; decidere se audit di Fase B deve tracciarne i confini prima di toccare nulla o se si accetta "keep come-e'".
6. **file_service.py 21 `except: pass`** (2.8) — triage riga-per-riga e' lavoro significativo. Utente: accetta che Fase B dedichi tempo esteso qui o preferisce mark-legacy bulk?
7. **`SECRET_KEY` / `JWT_SECRET_KEY` come crypto fallback** (sez. 3) — intenzionale o side-effect? `auth.py:126-145` e' pulito (no default) ma crypto.py li accetta.
8. **Policy preview vs execute (estrai/manipola/arricchisci)** (sez. 4) — chiarire: preview e' "best-effort perche' non-bloccante per UI" o "fail-fast perche' deve segnalare bug"?

### Pattern piu' preoccupanti

1. **Fallback di sicurezza**: crypto.py encrypt → plaintext e' il singolo item piu' rischioso (viola charter base 8 su dati critici).
2. **Flag ENABLE_ASYNC_ENRICHMENT**: esempio scuola di charter base 7 violato (due architetture vive senza piano di rimozione). Il fallback silent da async→sync aggrava.
3. **Silent cleanup pervasivo**: pattern `try cur.close(); except: pass` nei writer/pool DB e' ubiquo senza motivazione esplicita (viola uniformita' charter base 9).
4. **Introspection schema con return vuoto**: `database_writer.py` maschera errori di permessi/rete dietro "tabella non esiste". Effetti a valle imprevedibili.
5. **Docstring stale post-migrazione**: `features_service.py`, `features_service_db.py`, `connection_service.py` descrivono ancora filesystem/SQLite dopo migrazione PG. Non sono bug ma indice di debito documentale che confonde chi legge.
6. **Mismatch policy preview vs execute**: estrai/manipola/arricchisci silenziano la preview in modo inconsistente. Un utente vede "preview vuota" vs "preview crash" a seconda del modulo.

### Bug fuori scope annotati (NON fissati)

- **Stale docstrings** dopo migrazione PG in `connection_service.py:1-11`, `features_service.py:1-13`, `features_service_db.py:1-7`.
- **`features_service.py:1076-1078` claim "per evitare dipendenza circolare"** senza verificare — da confermare/smentire in Fase B.
- **`auth.py:989-991` silent swallow su delete_group_cascade** — annotato in Sezione 4, da correggere in Fase B.
- **ConnectionService docstring** parla ancora di "SQLite tramite app.db.db_service" (tecnicamente vero ma fuorviante).

### Perimetro di Fase B previsto (pre-approvazione)

Dopo checkpoint utente, gli interventi di Fase B ricadranno prevedibilmente in queste categorie:
- `unify`: ~4-5 famiglie (features_service/db merge, `_read_csv_*` merge, execute paths enrichment, ecc.)
- `mark-legacy`: ~3-5 item (alias env retro-compat, duplicate crypto chain, preview silent paths)
- `remove`: ~1-2 item (crypto plaintext fallback se utente concorda, flag async se mai attivato)
- `align policy`: ~10 moduli (silent → log, introspection → raise)

**Volume stimato**: 20-30 commit atomici piccoli + 1 commit di invariants.md. Tempo 2-4 giorni lavoro effettivo (stima grezza).

---

**Fine Fase A — attendo checkpoint utente per passare a Fase B.**
