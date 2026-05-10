# Runbook Operativo

Procedure operative per scenari ricorrenti di recovery. Per incidenti
strutturati, severity, comunicazioni e post-mortem usa
[`incident_response.md`](incident_response.md).

Riferimenti:

- [`deploy-checklist.md`](deploy-checklist.md) - pre/post deploy e rollback
- [`backup-restore.md`](backup-restore.md) - backup e restore DB
- [`jwt-incident-response.md`](jwt-incident-response.md) - token e blacklist JWT
- [`dr_drill_playbook.md`](dr_drill_playbook.md) - esercitazione DR
- [`runtime-prereqs.md`](runtime-prereqs.md) - dipendenze runtime

## 1. CI Funzionale

Workflow: `.github/workflows/ci-functional.yml`.

Quando eseguirlo manualmente:

- modifiche a `backend/app/core/arricchisci/`
- modifiche a `backend/app/core/scheduler/`
- modifiche a `backend/app/db/` o `backend/migrations/`
- modifiche a `backend/app/main.py` o `app.config`

```sh
gh workflow run ci-functional.yml --ref <branch>
```

Debug mirato:

```sh
gh workflow run ci-functional.yml --ref <branch> \
  -f pytest_extra_args="-k <test_name> -s -vv --log-cli-level=DEBUG"
```

Se fallisce, leggere prima i log falliti:

```sh
gh run view <run_id> --log-failed
```

Classificare il problema come regressione applicativa, dipendenza esterna,
timeout/transient, oppure gap di configurazione CI. Aprire un follow-up solo
con log e branch/commit di riferimento.

## 2. MFA Admin Lockout

Scenario: un admin ha perso il dispositivo MFA e non puo' accedere all'UI.
Questa procedura e' ammessa solo per utenti admin, con verifica identita'
fuori banda e audit manuale.

1. Identificare l'utente:

   ```sql
   SELECT id, username, role
   FROM plenora.users
   WHERE username = '<admin_username>';
   ```

2. Verificare che il ruolo sia `admin` o `superadmin`.

3. Disabilitare MFA:

   ```sql
   UPDATE plenora.user_mfa
   SET enabled = false
   WHERE user_id = '<user_id>';
   ```

4. Registrare l'azione:

   ```sql
   INSERT INTO plenora.audit_actions (user_id, action_type, details, created_at)
   VALUES ('<user_id>', 'mfa_disabled_manual', 'MFA disabled via ops runbook', NOW());
   ```

5. Comunicare all'admin che deve riconfigurare MFA al login successivo.

## 3. JWT Secret Rotation

Usare [`jwt-incident-response.md`](jwt-incident-response.md) per la procedura
completa. Sequenza minima:

1. Generare un nuovo `JWT_SECRET_KEY`.
2. Aggiornare il secret manager o `.env` del server.
3. Riavviare `plenora-backend`.
4. Comunicare logout forzato a tutti gli utenti.

## 4. DB Schema Mismatch Dopo Deploy

Sintomi tipici: `psycopg2.errors.UndefinedTable`,
`psycopg2.errors.UndefinedColumn`, oppure errori runtime subito dopo deploy.

Diagnosi:

```sql
SELECT filename, applied_at
FROM plenora.schema_migrations
ORDER BY filename;
```

Confrontare con i file in `backend/migrations/`.

Recovery:

1. Se una migration richiesta non e' applicata, verificare manualmente le
   tabelle o colonne attese.
2. Se mancano, applicare il file `.sql` corretto con `psql -f`.
3. Registrare la migration se necessario:

   ```sql
   INSERT INTO plenora.schema_migrations (filename)
   VALUES ('<filename.sql>')
   ON CONFLICT (filename) DO NOTHING;
   ```

4. Riavviare `plenora-backend` e verificare `/health` e `/ready`.

## 5. Deploy Failure Modes

### SSH Preflight Fallisce

Controllare:

- VPN o rete richiesta dall'ambiente
- `PLENORA_DEPLOY_PEM`
- permessi PEM `600`
- reachability server (`ping`, `curl <app-url>/health`)
- allowlist IP o security group cloud

### `init_all_dbs` Fallisce

Controllare:

- PostgreSQL raggiungibile dal server applicativo
- schema `plenora` presente
- permessi `CREATE TABLE` sullo schema
- tabella `schema_migrations` presente o creabile

Poi seguire la procedura "DB Schema Mismatch Dopo Deploy".

### Health Gate Timeout

Controllare:

```sh
journalctl -u plenora-backend -n 100 --no-pager
systemctl status plenora-backend
curl -fsS <app-url>/ready
```

Se il servizio e' `failed`, indagare il log prima del restart. Se il servizio
e' solo lento durante warm-up DB, attendere e riprovare `/ready`.

## 6. Intermediate Persistence Override

Solo `superadmin` puo' richiedere `intermediate_mode` come query string.

```sh
curl -X POST "https://lab.plenora.it/api/projects/{project_id}/execute?intermediate_mode=full_pg" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"force_recalculate": false}'
```

Mode ammessi:

- `metadata_only`
- `preview_only`
- `full_pg`
- `full_artifact`

Controlli post-run:

- `intermediate.runs.status = 'completed'`
- `intermediate.node_meta.persistence_mode` riflette l'override
- per `full_artifact`, `artifact_uri`, `artifact_status='complete'` e
  `artifact_format='parquet'` sono valorizzati

Rollback: l'override e' per singola chiamata; la run successiva senza query
string torna al default globale.

## 7. Altri Scenari

| Scenario | Documento |
|---|---|
| Backup e restore | [`backup-restore.md`](backup-restore.md) |
| DR drill | [`dr_drill_playbook.md`](dr_drill_playbook.md) |
| Trusted proxy / IP allowlist | [`trusted-proxies.md`](trusted-proxies.md) |
| Ticket triage | [`tickets.md`](tickets.md) |
| Incident response | [`incident_response.md`](incident_response.md) |
| JWT incident | [`jwt-incident-response.md`](jwt-incident-response.md) |
| Runtime prerequisites | [`runtime-prereqs.md`](runtime-prereqs.md) |
