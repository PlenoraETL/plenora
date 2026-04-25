# Runbook — Incident response

Playbook generale per la gestione di incidenti operativi su PLENORA-ETL.
Pensato per l'operatore di turno: triage rapido, decisioni chiare, comms
pre-formate per cliente istituzionale.

Per scenari specifici vedi:
- [`jwt-incident-response.md`](jwt-incident-response.md) — token compromessi, blacklist Redis
- [`backup-restore.md`](backup-restore.md) — DB perso, restore da S3, escalation AWS
- [`runtime-prereqs.md`](runtime-prereqs.md) — prerequisiti runtime container/host
- [`trusted-proxies.md`](trusted-proxies.md) — rate limit + IP forwarding

> **Bus factor 1**: oggi tutti i ruoli sono coperti da Marco Bonamente
> (vedi `backup-restore.md:381`). Questo è un rischio noto da chiudere
> aggiungendo almeno una seconda persona con accesso SSH + IAM secondario
> AWS. Tracciato in TRIAGE Phase 3 come gap residuo (P0.5 audit ops).

---

## §1 Severity matrix

Quattro livelli. La severity la decide chi prende in carico l'incidente
all'apertura; può essere alzata o abbassata in corso d'opera con nota nel
post-mortem.

| Severity | Criterio | Esempi | Tempo di risposta atteso |
|---|---|---|---|
| **P0** | Sistema giù o dati errati visibili al cliente | Backend 500 globale, JWT_SECRET ruotato in pieno carico, dati corrotti scritti su Feature Service ArcGIS cliente | acknowledge < 15 min, fix start < 30 min |
| **P1** | Funzione critica degradata, workaround possibile | Pipeline schedulata fallisce per uno schedule, login lento (>5s p95), backup notturno non eseguito | acknowledge < 1h, fix start < 4h |
| **P2** | Funzione minore degradata, no impatto immediato cliente | UI lenta su lista progetti, log retention non applicata, status dashboard parziale | acknowledge < 4h, fix entro 2 giorni |
| **P3** | Bug cosmetico / debito tecnico osservabile | Errore tipografico in modale, warning loggato non azionabile | next sprint |

**Regola di escalation**: se entro il tempo di risposta atteso non c'è
acknowledge, alza di un livello. P0 senza acknowledge in 30 min →
notifica diretta al cliente con stato "indagine in corso".

---

## §2 Ruoli

Per ogni incidente, assegnare i 3 ruoli. Una persona può ricoprirne più
di uno; per ora coincidono tutti.

| Ruolo | Cosa fa | Owner attuale |
|---|---|---|
| **Incident commander (IC)** | Coordina il triage, decide priorità degli step, autorizza azioni distruttive (rollback, restore da backup, rotation chiave) | Marco Bonamente |
| **Comms lead** | Notifica il cliente, mantiene il canale di update durante l'incidente, redige il messaggio di chiusura | Marco Bonamente |
| **Post-mortem owner** | Scrive il post-mortem entro 5 giorni lavorativi dalla chiusura, archivia in repo | Marco Bonamente |

Se l'IC è non raggiungibile per > 30 min su un P0, chiunque abbia
accesso può procedere con triage di lettura (log, dashboard, status) ma
**non** azioni distruttive.

---

## §3 Triage flow

```
Incidente aperto (alert / segnalazione cliente / monitoraggio interno)
│
├── 1. Conferma riproducibilità: l'evento è ancora in corso?
│      - log applicativo (plenora.logs)
│      - dashboard admin (/api/admin/system-status)
│      - test endpoint /health, /ready
│
├── 2. Decidi severity (vedi §1)
│
├── 3. Apri canale di comunicazione (anche solo nota personale + email)
│      con timestamp ISO-8601 di acknowledge
│
├── 4. Triage scenario:
│      ├── DB non risponde            → backup-restore.md §1 (DB perso)
│      ├── Token JWT compromesso      → jwt-incident-response.md
│      ├── Pipeline output corrotto   → §3.1 sotto
│      ├── Backend 500 globale        → §3.2 sotto
│      └── Altro / non chiaro         → §3.3 fallback
│
├── 5. Mitigazione (vs root cause): se P0, mitiga prima, root-cause dopo
│
├── 6. Notifica cliente se P0/P1 con impatto visibile (vedi §4)
│
├── 7. Verifica chiusura: l'incidente è risolto E rimane risolto per >30 min
│
└── 8. Apri post-mortem entro 5 gg lavorativi (vedi §5)
```

### §3.1 Pipeline output corrotto verso ArcGIS / DB cliente

Se viene segnalato che un Feature Service (cliente ICSC/RFI/ANIA) o una
tabella DB destinazione contiene dati errati:

1. **Stop immediato** degli schedule che usano la stessa pipeline:
   `PATCH /api/schedules/{id}/toggle` con `enabled=false` su tutti gli
   schedule che puntano al `project_id` incriminato.
2. **Identifica blast radius**: query su `intermediate.runs` per il
   `project_id`, raccogli `run_id` e timestamp delle ultime 24-48h.
3. **Verifica reversibilità**:
   - se `WriteMode.UPSERT` con `match_columns` → la prossima run con
     dati corretti sovrascrive (il blast radius è temporale, non
     permanente).
   - se `WriteMode.APPEND` senza `match_columns` → potenziali duplicati
     nel sink. Scarta lo scenario "rilancia subito", coordina con il
     cliente per cleanup manuale o restore lato cliente.
4. **Comms al cliente** con template §4 dichiarando blast radius e RCA in
   progress.

### §3.2 Backend 500 globale (uvicorn / Celery)

1. `systemctl status plenora-backend plenora-celery plenora-beat` su
   server prod.
2. Tail log `journalctl -u plenora-backend -n 200 --no-pager`. Cerca
   `[CRASH]` come marker di errore non gestito.
3. Se OOM kill → `dmesg | tail`, riduci `celery --concurrency` o roll
   back l'ultimo deploy.
4. Se `psycopg2 connection pool exhausted` → controlla `pg_stat_activity`
   su PG (potrebbe esserci una transazione zombie). `KILL` mirato della
   connessione zombie, non riavvio del DB.
5. Restart in ordine: `plenora-backend` → verifica `/health` → `plenora-celery`
   → `plenora-beat`.

### §3.3 Fallback — incidente non identificato

Se non riesci a categorizzare in §3.1/§3.2 entro 15 minuti:

1. Apri P1 cautelativo.
2. Snapshot stato: `journalctl -u plenora-backend -n 1000 > /tmp/incident-<timestamp>.log`,
   `pg_dump --schema=plenora > /tmp/incident-snapshot.sql` (solo se PG
   risponde, schema-only è veloce).
3. Comunica al cliente solo se l'incidente è visibile esternamente (email
   da loro, comportamento osservabile su lab.plenora.it).
4. Coinvolgi una seconda persona se disponibile (vedi nota bus factor in
   testa).

---

## §4 Comms template istituzionale

Tre template pre-approvati, in italiano, professionali, **senza
overcommit prima dell'RCA**. Copy-paste e personalizza solo i campi
`<...>`.

### §4.1 Acknowledge iniziale (entro 30 min da P0, 1h da P1)

> Oggetto: [PLENORA-ETL] Incidente in corso — <data ISO> — <severity>
>
> Gentile <referente cliente>,
>
> ti segnaliamo che alle <ora UTC> abbiamo identificato un'anomalia su
> <componente: pipeline X / Feature Service Y / login lab.plenora.it>.
> Il team è già al lavoro per il triage.
>
> Stiamo verificando l'impatto sui vostri flussi. Una nuova
> comunicazione seguirà entro <ETA realistica, es. "le prossime 2 ore">
> con aggiornamento sullo stato e, se possibile, ETA di risoluzione.
>
> Per qualunque urgenza, rispondi a questa email.
>
> Plenora — Marco Bonamente

### §4.2 Update intermedio (durante incidente, ogni 2h o su cambio stato)

> Oggetto: [PLENORA-ETL] Update incidente <data> — <stato>
>
> Aggiornamento sull'incidente delle <ora UTC>:
>
> - **Stato attuale**: <indagine / mitigazione applicata / risolto in verifica>
> - **Impatto identificato**: <es. "schedule notturno X non eseguito tra le HH e le HH">
> - **Mitigazione in corso**: <azione>
> - **Prossimo update**: <ETA>
>
> Plenora — Marco Bonamente

### §4.3 Chiusura (incidente risolto, RCA preliminare)

> Oggetto: [PLENORA-ETL] Incidente <data> risolto — comunicazione di chiusura
>
> L'incidente segnalato alle <ora UTC> è stato risolto alle <ora UTC>.
> Sintesi:
>
> - **Sintomi**: <una frase>
> - **Causa preliminare**: <una frase, no congetture>
> - **Mitigazione applicata**: <una frase>
> - **Impatto sui vostri flussi**: <quantificato se misurabile, "nessuno"
>   se conferma di reconciliation post-incidente è OK>
>
> Il post-mortem completo sarà condiviso entro <data, max 5 giorni
> lavorativi>.
>
> Plenora — Marco Bonamente

**Cosa NON dire pre-RCA**: garanzie su "non si ripeterà mai più",
attribuzione a vendor terzi senza evidenza, scuse generiche senza fatto
concreto. Se non sai, dichiara "indagine in corso".

---

## §5 Post-mortem template

Da scrivere in `backend/docs/ops/postmortems/<YYYY-MM-DD>-<slug>.md`
entro 5 giorni lavorativi dalla chiusura. Slug breve, `kebab-case`
(es. `2026-05-12-pipeline-icsc-output-corrotto`).

```markdown
# Post-mortem — <titolo breve>

**Data incidente**: <YYYY-MM-DD>
**Severity**: <P0|P1|P2>
**Acknowledge**: <ora UTC>
**Risoluzione**: <ora UTC>
**Durata totale**: <hh:mm>
**Cliente impattato**: <ICSC|RFI|ANIA|interno>
**Incident commander**: <nome>

## Sintomi

<Cosa ha visto chi ha aperto l'incidente. Una sezione di 3-5 righe.>

## Timeline

- HH:MM UTC — <evento>
- HH:MM UTC — <evento>
- HH:MM UTC — mitigazione applicata
- HH:MM UTC — verifica chiusura

## Root cause

<Una sezione di 5-15 righe. Catena causale, non solo "il bug X".
Esempio: "il flag allow_unsafe_append è stato copiato come stringa
'true' dal frontend, il backend lo accettava come truthy via bool('true')
e abilitava per_batch senza match_columns. Retry parziale ha duplicato
righe.">

## Mitigazione

<Cosa abbiamo fatto per fermare il sanguinamento. Diverso da fix
definitivo se applicabile.>

## Fix definitivo

<Patch / commit / change applicato. Riferimento al commit hash o al PR.>

## Blast radius

<Quantitativo se possibile. "N righe duplicate su tabella X tra le HH:MM
e le HH:MM". Se zero, dichiararlo esplicitamente con criterio di verifica.>

## Follow-up

- [ ] <azione, owner, deadline>
- [ ] <azione, owner, deadline>

## Lessons learned

<Cosa cambia nel modo di operare. Update ai runbook se necessario,
tracking nel TRIAGE Phase 3+.>
```

---

## §6 Riferimenti operativi

- [`backend/docs/ops/backup-restore.md`](backup-restore.md) — restore PG da S3
- [`backend/docs/ops/jwt-incident-response.md`](jwt-incident-response.md) — JWT specifico
- [`backend/docs/ops/runtime-prereqs.md`](runtime-prereqs.md) — prerequisiti runtime
- [`backend/docs/ops/trusted-proxies.md`](trusted-proxies.md) — rate limit / X-Forwarded-For
- [`backend/deploy/backup-runbook.md`](../../deploy/backup-runbook.md) — backup operativo routine
- [`backend/scripts/test_restore.py`](../../scripts/test_restore.py) — verifica restore weekly

## §7 Checklist post-incidente

- [ ] Acknowledge timestamp registrato
- [ ] Cliente notificato (se P0/P1 visibile)
- [ ] Mitigazione applicata e verificata
- [ ] Status check post-fix per > 30 min senza ricorrenza
- [ ] Comms di chiusura inviata al cliente (se notificato)
- [ ] Post-mortem aperto in `backend/docs/ops/postmortems/`
- [ ] Follow-up tracciati (TRIAGE Phase 3+ o successivi)
