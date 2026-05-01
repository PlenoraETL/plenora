# DR drill playbook

Procedura ripetibile per esercitare end-to-end il disaster recovery di
PLENORA-ETL. Cadenza target: **trimestrale**. Ogni esecuzione produce un
record append-only in [`dr_drill_log.md`](dr_drill_log.md).

> Distinzione importante: il timer `plenora-backup-test.timer` weekly
> verifica solo che il dump sia **restorable** (no errore in pg_restore).
> Non testa la pipeline completa. Questo playbook copre il drill end-to-end
> "DB perso → ripristino + smoke applicativo" misurando RPO/RTO reali.

---

## §1 Scope

### Cosa testiamo

- Disponibilità + integrità di un dump recente su S3 (`plenora-backup-...`)
- Capacità di restaurare lo schema `plenora` su un PG vergine
- Estensioni richieste (`postgis`, `postgis_raster`, `postgis_topology`,
  `h3`, `h3_postgis`)
- Smoke applicativo post-restore: login + lista progetti + healthcheck
- Misurazione RPO + RTO reali (non solo restorability)

### Cosa NON testiamo

- Failover automatico (non implementato — restore è manuale)
- Cross-region (bucket è in `eu-south-1`, niente replica multi-region)
- Restore dei file utente / blob (sono in DB, coperti dallo schema dump)
- Cleanup post-incident dei task Celery in-flight (non DR-related)

---

## §2 Prerequisiti

Prima di iniziare il drill verifica:

- [ ] Accesso SSH al server prod (`ubuntu@15.160.219.93`) — non lo usiamo
      per modificare prod, serve solo per simmetria di ambiente nelle
      operazioni post-restore
- [ ] Accesso AWS con permessi `s3:ListBucket` + `s3:GetObject` sul
      bucket `plenora-backup-112011134642-eu-south-1-an`
- [ ] Postgres locale o staging con `superuser` (necessario per
      `CREATE EXTENSION`); idealmente **stessa major version del prod**
- [ ] `pg_dump` / `pg_restore` installati locale (versione ≥ versione
      remota — i client recenti leggono dump più vecchi)
- [ ] Un blocco di tempo dedicato (~1 ora). Non eseguire mai un drill
      durante un incidente reale o una finestra di deploy.

---

## §3 Step di drill

Ogni step ha **comando + checkpoint + atteso**. Cronometra dall'inizio
dello step 4 (download) alla fine dello step 8 (smoke verify): è il tuo
**RTO measured**.

### Step 1 — Apri il drill log

Nel log [`dr_drill_log.md`](dr_drill_log.md) crea l'entry header con
data ISO, executor, ambiente target. Salva il file. Servirà a riempirsi
durante l'esecuzione.

### Step 2 — Snapshot dei target dichiarati

Prima di iniziare, registra nell'entry corrente i target di RPO/RTO:

- **RPO target**: 24h (un backup giornaliero alle 02:00 UTC)
- **RTO target**: 2h (download + restore + smoke su dump `--schema=plenora` ~12 MB, schema + dati, non full-DB)

Vedi §5 per il razionale dei numeri.

### Step 3 — Identifica il dump da usare

```bash
aws s3 ls s3://plenora-backup-112011134642-eu-south-1-an/ \
  --region eu-south-1 | tail -10
```

**Checkpoint**: deve esistere un dump con timestamp recente (ultime 48h).
**Atteso**: lista non vuota, dump più recente è del giorno precedente.

Se il dump più recente è > 48h fa: stop drill, apri P1 incidente —
qualcosa nel ciclo backup è rotto, drill non rappresentativo.

Registra nel log: nome dump scelto + timestamp.

### Step 4 — Download del dump

**Avvia cronometro RTO qui.**

```bash
aws s3 cp s3://plenora-backup-112011134642-eu-south-1-an/<dump-file> \
  /tmp/dr_drill.dump --region eu-south-1
```

**Checkpoint**: file `/tmp/dr_drill.dump` esiste, dimensione > 1 MB.
**Atteso**: download in < 30s su connessione locale tipica.

Registra nel log: dimensione file, secondi di download.

### Step 5 — Crea DB target vergine

```bash
DRILL_DB="plenora_dr_drill_$(date +%Y%m%d_%H%M%S)"
createdb -h localhost -U postgres "$DRILL_DB"
psql -h localhost -U postgres -d "$DRILL_DB" -c "
  CREATE EXTENSION IF NOT EXISTS postgis;
  CREATE EXTENSION IF NOT EXISTS postgis_raster;
  CREATE EXTENSION IF NOT EXISTS postgis_topology;
  CREATE EXTENSION IF NOT EXISTS h3;
  CREATE EXTENSION IF NOT EXISTS h3_postgis;
"
```

**Checkpoint**: `psql -d "$DRILL_DB" -c "\dx"` lista tutte le 5 estensioni.
**Atteso**: ~5-15s totali.

Se manca un'estensione: stop, fix install locale, ripeti drill (i target
di prod hanno tutte le estensioni installate; un drill su un PG senza è
falsamente positivo).

### Step 6 — Restore

```bash
pg_restore \
  --dbname "postgresql://postgres@localhost/$DRILL_DB" \
  --no-owner --no-privileges \
  --exit-on-error \
  --verbose \
  /tmp/dr_drill.dump 2>&1 | tail -20
```

**Checkpoint**: `pg_restore` exit code 0; ultime righe non contengono
`ERROR:`.
**Atteso**: ~10-60s per schema da 12 MB.

Registra nel log: secondi di restore, eventuali warning non-fatali.

### Step 7 — Verifica restorability semantica

```bash
psql -h localhost -U postgres -d "$DRILL_DB" <<'SQL'
SELECT 'users'       AS t, COUNT(*) FROM plenora.users
UNION ALL SELECT 'projects',     COUNT(*) FROM plenora.projects
UNION ALL SELECT 'connections',  COUNT(*) FROM plenora.connections
UNION ALL SELECT 'schedules',    COUNT(*) FROM plenora.schedules
UNION ALL SELECT 'audit_actions',COUNT(*) FROM plenora.audit_actions;
SQL
```

**Checkpoint**: tutti i count sono > 0 (eccetto ambienti staging vuoti).
**Atteso**: count plausibili rispetto a prod corrente. Se uno è 0 dove
atteso non-0 → restorability tecnica OK ma dump semanticamente vuoto.
Apri P1.

### Step 8 — Smoke applicativo (RTO end)

**Ferma cronometro RTO qui.**

Cambia temporaneamente `DATABASE_URL` su un'istanza locale del backend
verso `$DRILL_DB`, fai partire `uvicorn`, esegui:

```bash
# Healthcheck
curl http://localhost:8000/health
# Atteso: {"status":"ok"} o equivalente

# Login con un utente noto del dump (NON un utente prod attivo: drill =
# read-only sul dump)
curl -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"<test-user>","password":"<noto-da-staging>"}'
# Atteso: 200 + access_token nella response
```

**Checkpoint**: `/health` 200 + login 200.
**Atteso**: < 5s totali.

Se login fallisce con `JWT_SECRET_KEY` mancante: il drill ha rivelato
che la SSM lookup non è coperta — registra come finding nel log e nel
post-drill.

### Step 9 — Cleanup

```bash
dropdb -h localhost -U postgres "$DRILL_DB"
rm -f /tmp/dr_drill.dump
```

**Checkpoint**: nessun residuo. Il drill non deve lasciare DB orfani.

### Step 10 — Chiudi entry log

Compila l'entry in `dr_drill_log.md` con:

- RTO measured
- RPO measured (timestamp del dump scelto vs ora drill)
- Esito complessivo (PASS / PASS-with-findings / FAIL)
- Findings: ogni step che ha sforato il tempo atteso o richiesto
  intervento manuale → riga in "Findings" dell'entry
- Follow-up: TRIAGE entries da aprire se trovi gap (P1/P2)

---

## §4 Misurazioni RPO / RTO

### RPO — Recovery Point Objective

```
RPO_measured = ora_drill_inizio - ora_dump_creato
```

Esempio: drill alle 14:30 UTC del 2026-05-15, dump scelto delle 02:00 UTC
del 2026-05-15 → `RPO_measured = 12h 30m`.

**Soglia di alert**: `RPO_measured > 36h` → backup pipeline è in
regressione (dovrebbe essere ≤24h dato il timer giornaliero). Apri P1.

### RTO — Recovery Time Objective

```
RTO_measured = step_8_end - step_4_start
```

Cronometro tra inizio download e fine smoke applicativo. Include tutti
gli step automatizzabili (download, createdb, restore, smoke).

**Soglia di alert**: `RTO_measured > 2h` → o lo schema è cresciuto
sensibilmente, o il pg_restore ha rallentato, o c'è un'estensione
problematica. Apri P2 + aggiorna i target nel runbook.

### Target dichiarati

| Metrica | Target | Razionale |
|---|---|---|
| RPO | ≤ 24h | Backup giornaliero `plenora-backup.timer` alle 02:00 UTC |
| RTO | ≤ 2h | dump `--schema=plenora` ~12 MB (schema + dati, non full-DB), restore + extension setup + smoke ≤ 5 min sotto carico normale, buffer 24x per variabilità rete/AWS |

I target sono **conservativi** rispetto a quanto misurato in passato
(restore osservato 10-60s, vedi `backup-restore.md:134`). La distanza tra
target e baseline è il margine per coprire scenari peggiori (rete lenta,
PG locale carico, console AWS rallentata, ecc.).

---

## §5 Report template

Da copiare in `dr_drill_log.md` come nuova entry.

```markdown
### Drill <YYYY-MM-DD> — <executor>

**Ambiente target**: <localhost / staging>
**Dump usato**: <s3 key>
**Dump timestamp**: <YYYY-MM-DD HH:MM UTC>
**Drill timestamp inizio**: <YYYY-MM-DD HH:MM UTC>

**Misurazioni**:
- RPO measured: <Hh Mm>
- RTO measured: <Hh Mm Ss>
- Dump size: <MB>
- Restore time (step 6): <secondi>

**Esito**: PASS | PASS-with-findings | FAIL

**Findings**:
- <step N>: <descrizione, severity P1/P2>
- <...>

**Follow-up**:
- [ ] <azione, owner, deadline>
- [ ] <...>

**Note libere**:
<eventuali osservazioni non strutturate>
```

---

## §6 Riferimenti

- [`backup-restore.md`](backup-restore.md) — DR runbook completo, scenari A/B/C
- [`backup-runbook.md`](../../deploy/backup-runbook.md) — operazioni routine
- [`scripts/test_restore.py`](../../scripts/test_restore.py) — verifica
  restorability automatica weekly (subset di questo drill)
- [`incident_response.md`](incident_response.md) — se durante il drill
  scopri gap che richiedono intervento prod, apri incidente per il
  livello di severity appropriato
- [`dr_drill_log.md`](dr_drill_log.md) — registro append-only delle
  esecuzioni passate
