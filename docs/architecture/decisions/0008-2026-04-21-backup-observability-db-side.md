# ADR-0008: Observability backup via DB-side `plenora.backup_runs`, non `systemctl`/`journalctl`

**Status**: Accepted
**Date**: 2026-04-21
**Deciders**: @CloudIA

## Context

URBY-ETL esegue backup automatici del database PostgreSQL tramite lo script `backend/scripts/backup_db.py`, eseguito via systemd timer (`plenora-backup.timer` / `plenora-backup-test.timer`). Esiste anche un job di verify periodico che controlla integrita' dei dump su S3.

Requisito: l'API admin `/api/admin/backups/status` deve rispondere con lo stato dell'**ultimo run** del backup (success/failed/running, start/end time, duration, exit code, bucket key, size bytes, ultime righe di log se error). Consumer: dashboard admin frontend + endpoint di health-check per external monitoring.

Due strade:

1. **Interrogare systemd/journald del nodo**: `systemctl show plenora-backup.service`, `journalctl -u plenora-backup.service -n 50`. La source of truth vive nel sistema operativo.
2. **Source DB-side**: lo script scrive una row in una tabella (`plenora.backup_runs`) all'avvio e all'end; l'API legge quella tabella.

## Decision

**Source of truth DB-side**: tabella `plenora.backup_runs` scritta dallo script di backup all'avvio (INSERT con `started_at`, `result='running'`) e aggiornata a fine job (UPDATE con `finished_at`, `exit_code`, `result`, `bucket_key`, `size_bytes`, `log_tail`, `error_message`).

L'API admin (`app/core/admin_backups/service.py:76` `get_last_run_status`) legge con una semplice `SELECT ... ORDER BY started_at DESC LIMIT 1`.

Stesso pattern per verify runs: tabella `plenora.verify_runs`.

Nessun `systemctl` / `journalctl` invocato dall'app. L'app **non** ha shell access al nodo di deploy nel path di produzione (container-friendly).

## Rationale

- **Container-safe**. Se domani il deploy passa da systemd su EC2 a container su ECS/Fargate, l'app non deve cambiare. `systemctl` e' uno dei primi tool mancanti nei container minimal; un design DB-side funziona ovunque ci sia rete verso Postgres.
- **Single query per stato**. API admin = una SELECT. Latenza prevedibile, caching banale se serve.
- **Separation of concerns**. Systemd sa *eseguire* il job; il DB sa *cosa e' successo*. L'API admin interroga il "cosa", non il "come". Disaccoppia il backend dal modo operativo di avvio dello script.
- **Tracciabilita' strutturata**. Le colonne sono tipate (timestamp, int, text). Permette query analitiche ("media duration ultimi 30 giorni", "count di failed dal X"). Con journalctl avremmo solo testo libero da parsare.
- **Retention esplicita**. Le righe vecchie si cancellano via policy SQL (`DELETE ... WHERE started_at < now() - interval '90 days'`). Con journald la retention e' gestita da `Storage=`, `SystemMaxUse=` nel config systemd — meno prevedibile.
- **Log tail cross-boundary**. Lo script cattura `stdout`/`stderr` in memoria e li salva come `log_tail` (array di righe) alla fine del job. L'API mostra questi senza dover shellare a `journalctl`.

## Alternatives considered

- **`systemctl show` + `journalctl -u ... -o json`** — **Rejected**. Richiederebbe privilegi sudo-less per leggere `journalctl` (utente con `Storage=persistent` + `SystemMaxFileSize` config permissivo), hardening del nodo piu' permissivo di quanto desiderato, e integrazione shell-out dalla FastAPI app (antipattern). Incompatibile con container senza journald.
- **Log shipping a CloudWatch / ELK e poi query da li** — **Rejected per Fase 0/1**. Overkill per il volume di backup runs (1-2 righe al giorno). Dependency aggiuntiva + costo infra. Ragionevole se/quando il volume di jobs tracciati crescera' oltre un ordine di grandezza.
- **File-based: lo script scrive JSON su disco, l'API legge il file** — **Rejected**. Richiede FS shared tra script e backend (nel caso di deploy multi-nodo non esiste) o coordinazione via S3 (latenza + costo). Il DB e' gia' shared state naturale.
- **Notifica push via webhook al backend** — **Rejected**. Accoppia il lifecycle dello script alla disponibilita' HTTP del backend (un backend giu' perde l'evento). Il DB invece e' persistente: l'API che interroga dopo vede comunque tutto.

## Consequences

### Positive
- API admin `/api/admin/backups/status` e' una query + formatting. Latenza bassa, debugging banale.
- L'endpoint funziona anche se systemd e' fuori servizio (es. durante un'ispezione) — la row precedente e' ancora in DB.
- Cross-deploy compatibility: bare-metal, VM, container, serverless (se mai) — tutti scrivono in DB.
- Query storiche (trend analysis) gratis, basta aggiungere un `ORDER BY ... LIMIT N`.
- Il `log_tail` preserva contesto utile (ultima eccezione) senza dover shellare journalctl.

### Negative
- **Failure mode "script muore tra INSERT e UPDATE"**: riga con `result='running'` resta stale. Il `get_last_run_status` la segnala come `running` finche' un nuovo run la sovrascrive o un cleanup policy cancella. Mitigazione: timeout alert su `started_at < now() - interval '2 hours' AND result='running'` → probabile crash silenzioso. Tracciato come operational concern, non design flaw.
- Script di backup dipende da DB up. Se il backup e' tentato proprio perche' il DB ha problemi, non puo' loggare il proprio fallimento in DB. Dead zone teorica; pragmatico: se il DB e' totalmente giu', il backup fallisce e se ne accorge altro monitoring (es. CloudWatch su EC2 health).
- Una tabella in piu' per-tipo-di-job. Sistema semplice oggi (2 tabelle: `backup_runs`, `verify_runs`); se crescesse a 10+ tipi di cron, valutare tabella unica `scheduled_job_runs` con `job_type` discriminator.

### Neutral
- Test del DB-side: banale (insert fake + query). Test dell'effettivo running del cron: out of scope, infra-level.
- `log_tail` e' un campo `TEXT`/`JSONB` — cap a ~20 KB per riga per evitare tabella grassa.

## References

- Source: `backend/app/core/admin_backups/service.py:70-130` — `get_last_run_status`
- Source: `backend/app/core/admin_backups/service.py:182+` — `get_schedule_status` (ultima run + next cron fire)
- Source: `backend/scripts/backup_db.py` — script che scrive in `plenora.backup_runs`
- Source: `backend/app/core/admin_backups/__init__.py:5-12` — overview dell'interazione
- Related migrations: `backend/migrations/*backup_runs*.sql`, `*verify_runs*.sql`
- Invariants: `docs/architecture/invariants.md` §observability — DB-side ground truth per job status
