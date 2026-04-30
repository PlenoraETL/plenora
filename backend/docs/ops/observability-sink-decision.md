# A2 — Observability sink decision

> Status: **DECISION RECORDED** (analisi 2026-04-30). Setup operativo
> reale rinviato post-deadline; questo doc tiene traccia della scelta
> e del razionale.

## 1. Sintesi

A1 (PR #24) ha versionato 6 perf queries `Q-PERF-1..6` come `.sql`
paste-ready in `backend/scripts/perf_queries/`. Per A2 serve scegliere
un consumer dashboard che esegua queste query in modo programmato e
condiviso.

**Decisione**: **Metabase** come primary sink.

DBeaver resta come strumento parallelo per query ad-hoc / debug e per
runner Q-PERF-5 (script Python). Grafana valutato e scartato per il
caso d'uso corrente.

## 2. Comparison Metabase vs Grafana vs DBeaver

| Criterio | Metabase | Grafana | DBeaver |
|---|---|---|---|
| Tipologia | BI question + dashboard | Time-series + dashboard panel | SQL client + saved queries |
| Setup minimo | Container Docker + datasource PG (~20min) | Container Docker + datasource PG + provisioning YAML (~30min) | Già installato lato operatore |
| Visualizzazione p50/p95 | ⚠️ tabellare; no time-series native | ✅ time-series eccellenti | ❌ solo SQL plain output |
| Auth | Built-in users + group-level filter | Built-in users / role + LDAP | Per-operatore (non condiviso) |
| Multi-user shared dashboard | ✅ | ✅ | ❌ |
| Persistenza dashboard | DB interno (h2 / PG) | YAML provisioning + DB interno | File `.dbeaver` per-operatore |
| Read-only enforcement DB | ✅ via PG user dedicato | ✅ via PG user dedicato | ⚠️ dipende dal PG user dell'operatore |
| Self-host complexity | Low (1 container) | Medium (1 container + provisioning) | Zero (client only) |
| Cost (self-host) | Free AGPLv3 | Free Apache 2.0 | Free CE |
| Adatto a Q-PERF-1..4 (aggregati + percentile) | ⭐⭐⭐ | ⭐⭐ | ⭐ |
| Adatto a Q-PERF-3 (top N slow path) | ⭐⭐⭐ | ⭐⭐ | ⭐⭐ (one-shot) |
| Adatto a Q-PERF-6 (distribution) | ⭐⭐ | ⭐⭐⭐ | ⭐ |
| Adatto a "kiosk dashboard" | ✅ | ✅ | ❌ |
| Maintenance overhead | Low | Medium (alert rules, updates) | Zero |

## 3. Razionale Metabase

- **5/6 query sono aggregati semplici** (`COUNT`, `AVG`,
  `percentile_disc`, `GROUP BY`) → Metabase question native fit.
- **Q-PERF-3 top N** → Metabase tabella perfetta con filtri.
- **Setup operativo basso**: 1 container, no provisioning YAML.
- **Dashboard condivisa multi-utente** con permission integrate, OK
  per ruoli `ops` / `admin`.
- **Self-host minimal**: ~512MB RAM container, JVM-based ma stable.
- **Distribution histograms** (Q-PERF-6) ottenibili con bin manuali o
  visualizzazioni alternative; non blocker.

## 4. Quando preferire Grafana (decision tree)

Grafana sarebbe migliore se:
- Si volesse **time-series real-time** con refresh continuo (5s/30s)
- Si avessero già altri datasource (Prometheus / InfluxDB) da unificare
- Si volesse **alerting su threshold metric** (es. p95 > 500ms)
- Si volesse un layer di provisioning declarative (dashboard come code)

Per URBY-ETL stato attuale **nessuno di questi requisiti e' attivo**:
le perf queries sono baseline aggregati ad uso humano, non telemetry
real-time.

## 5. DBeaver come strumento parallelo

DBeaver resta lo strumento di:
- Query ad-hoc / debug DB (anche cross-schema)
- Runner Q-PERF-5 (script Python `intermediate_run_report.py`)
- Sessione DB autorizzata per Q1-Q4 compat residue (vedi
  `compat-residue-runbook.md`)

DBeaver **non** e' candidato come dashboard ufficiale (per-operatore,
no shared state).

## 6. Setup minimo futuro (operativo, NON eseguito qui)

Riferimento concettuale (i file effettivi NON sono committati e
verranno creati al setup time, post-deadline).

### 6.1 `docker-compose.metabase.yml` (snippet template)

```yaml
# Schema indicativo; aggiustare versioni e network al setup time.
services:
  metabase:
    image: metabase/metabase:v0.50
    ports:
      - "3000:3000"
    environment:
      MB_DB_TYPE: h2  # SQLite-like interno per Metabase metadata
      MB_PASSWORD_COMPLEXITY: strong
    volumes:
      - metabase-data:/metabase.db
volumes:
  metabase-data:
```

### 6.2 PG user read-only

Schema indicativo (NON eseguire qui):

```sql
-- Da eseguire manualmente in sessione DBA autorizzata.
-- Niente credenziali in clear, password generata + salvata in vault.
CREATE USER metabase_ro WITH PASSWORD '<from-vault>';
GRANT CONNECT ON DATABASE <db_name> TO metabase_ro;
GRANT USAGE ON SCHEMA plenora TO metabase_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA plenora TO metabase_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA plenora
    GRANT SELECT ON TABLES TO metabase_ro;
```

### 6.3 Dashboard skeleton

Plenora ETL Performance Baseline:
- Card Q-PERF-1: latenza s3_download p50/p95/p99 ultimi 7gg
- Card Q-PERF-2: backend distribution (object_storage vs db_legacy)
- Card Q-PERF-3: top 20 slow snapshots (table)
- Card Q-PERF-4: load_source_project p95 per project_id (HAVING ≥5)
- Card Q-PERF-6: full_artifact artifact_ms distribution

Q-PERF-5 e' uno script Python: documentato come "manual runner",
non integrato nel dashboard.

### 6.4 Network & access

- Metabase **NON exposed publicly**; accesso solo via VPN interna
- Login: utenti del gruppo `ops` (read) + `admin` (full)
- DB connection: utente `metabase_ro` definito in 6.2

## 7. Costo stimato

| Voce | Stima |
|---|---|
| Hardware | 1GB RAM su VPS esistente (no cost incrementale) |
| Time setup iniziale | ~2h (container + connection + 6 question + 1 dashboard) |
| Maintenance | ~30min/mese (Metabase upgrade quarterly + question audit) |

## 8. Out-of-scope

- ❌ Setup container reale (post-deadline, decisione separata)
- ❌ Esecuzione queries su prod DB
- ❌ Automatic alerting / scheduled report
- ❌ Embedding dashboard panel in app principale
- ❌ Metabase OSS vs EE comparison (rinviato a Phase 2)
- ❌ Q-PERF-5 integration (resta script Python manuale)

## 9. Riferimenti

- `backend/scripts/perf_queries/` — 6 query versionate
- `backend/scripts/perf_queries/README.md` — index + uso
- `orchestrate/phase_0_consolidation/performance_baseline_0_5f.md §5.1`
  (origine queries)
- `backend/scripts/intermediate_run_report.py` — Q-PERF-5 runner
- `backend/docs/ops/runbook.md` §1 (B-2 functional workflow context)
