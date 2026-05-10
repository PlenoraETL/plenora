# Handoff — Bus factor 2 minimum

Doc minimo per onboarding di un secondo operatore. Scope: ownership +
processo, NON credenziali in chiaro.

Bus factor 1 è documentato esplicitamente in `incident_response.md`.
Questo file è il primo passo per ridurlo a 2.

## 1. Vincoli di sicurezza per questo doc

- ❌ NESSUNA credenziale (password, token, API key, PEM content) in
  chiaro
- ❌ NESSUN hostname / IP / endpoint specifico in chiaro
- ❌ NESSUN nome bucket / database / username operativo in chiaro
- ✅ Solo *ownership* (chi possiede cosa) e *processo* (come si
  ottiene accesso)

I valori reali (host, IP, secret, bucket name) restano:
- Per ambiente runtime: in `.env` del server (gestito secrets
  management, NON nel repo)
- Per operatore: nelle env var personali (PEM path, AWS profile, ecc.)
- Per il workflow CI: in GitHub Actions secrets (UI → Settings → Secrets)

## 2. Ownership matrix

Owner = persona/ruolo che ha autorità decisionale + accesso primario.
Backup = chi può subentrare in caso di assenza, dopo onboarding completato.

| Risorsa | Owner attuale | Backup | Come trasferire |
| --- | --- | --- | --- |
| PEM SSH server prod | bus_factor_1 | (da onboardare) | Vault sharing (1Password / Bitwarden / similar) |
| Vault password (master) | bus_factor_1 | (da onboardare) | Out-of-band sharing al momento dell'onboarding |
| AWS account root | bus_factor_1 | (da onboardare) | Root: solo emergenza. IAM user: creare per backup operator |
| AWS IAM user backup operator | n/a | (da creare) | IAM console → create user → policy read+limited-write |
| GitHub repo admin | bus_factor_1 | (da aggiungere) | Repo Settings → Collaborators → Add admin |
| GitHub Actions secrets | bus_factor_1 | (visibilità repo admin) | Same as repo admin |
| `.env` prod template | repo `runtime-prereqs.md` | repo `runtime-prereqs.md` | Doc già pubblico |
| `.env` prod values | bus_factor_1 server local | (da copiare su backup vault) | Out-of-band, mai via chat |
| JWT_SECRET_KEY rotation authority | bus_factor_1 | (da abilitare) | Vedi `jwt-incident-response.md` |
| DB prod read access | bus_factor_1 | (da abilitare) | PG role: create read-only user su `plenora` schema |
| DB prod read-write access | bus_factor_1 | (da abilitare) | PG role: limit a tabelle non-audit (escludere `log_runs`, `audit_actions`) |
| Deploy authority (chi triggera deploy.sh) | bus_factor_1 | (da abilitare dopo training) | `deploy/profiles/*.conf` versionato + PEM env |
| GitHub Actions workflow trigger | repo admin | repo admin | Auto via repo collab |

## 3. Onboarding 2nd operator — checklist

Da eseguire in ordine. Dopo ogni step, segnare data + responsabile in `dr_drill_log.md` come tracking.

### 3.1 Accessi base

- [ ] SSH key del 2nd operator generata localmente (su laptop personale, non shared)
- [ ] SSH public key aggiunta a `~/.ssh/authorized_keys` su server prod (sysadmin azione, fuori-banda)
- [ ] Test SSH connectivity dal laptop 2nd operator (solo `ssh -T` o `ssh exit 0` non-interattivo)

### 3.2 Vault + secrets

- [ ] 2nd operator ha account vault (1Password / Bitwarden / similar)
- [ ] PEM file condiviso via vault (NON via mail, chat, drive)
- [ ] Master password vault condiviso out-of-band (es. video call)
- [ ] 2nd operator scarica PEM in posizione locale, applica `chmod 600`
- [ ] Setta env var `PLENORA_DEPLOY_PEM=<local-path>` nel proprio profilo shell

### 3.3 AWS

- [ ] IAM user per 2nd operator creato (NON shared root!)
- [ ] Policy `AmazonS3ReadOnlyAccess` + `CloudWatchLogsReadOnlyAccess` allegate (read-only baseline)
- [ ] Policy custom limited-write per BACKUP_BUCKET / OBJECT_STORAGE_BUCKET (PUT/DELETE su prefix specifico)
- [ ] AWS CLI configurato sul laptop 2nd operator: `aws configure --profile plenora-backup`
- [ ] Test `aws s3 ls` riesce (read access)

### 3.4 GitHub

- [ ] 2nd operator aggiunto come collaborator al repo (admin role)
- [ ] 2nd operator può vedere GitHub Actions secrets (no leak: solo verifica esistenza, non lettura plaintext)
- [ ] 2nd operator può triggerare workflow_dispatch su `ci-functional.yml`

### 3.5 DB access

- [ ] PG read-only user creato per 2nd operator con grants su schema `plenora`
- [ ] Test `psql -h <host> -U <user_2nd_operator> -d <db>` connette
- [ ] Test query read: `SELECT count(*) FROM plenora.users` ritorna count
- [ ] Test denial scrittura: `INSERT INTO plenora.users ...` ritorna permission denied

### 3.6 Deploy training (no esecuzione reale prima di staging)

- [ ] 2nd operator legge `deploy-checklist.md` end-to-end
- [ ] 2nd operator legge `runbook.md` end-to-end
- [ ] 2nd operator legge `incident_response.md` end-to-end
- [ ] 2nd operator esegue `./deploy.sh backend --check-only` (no side-effect) → verifica output coerente
- [ ] Quando staging esiste, il secondo operatore esegue un deploy reale su staging come prima esercitazione

### 3.7 Tabletop incident exercise

- [ ] Scenario sintetico (es. "PG schema mismatch dopo deploy") proposto
- [ ] 2nd operator naviga `runbook.md` per identificare la procedura
- [ ] Discussione post-exercise: gap di doc / ambiguità / aggiornamenti necessari
- [ ] Aggiornamento `runbook.md` se emergono lacune

### 3.8 Sign-off

- [ ] 2nd operator conferma di poter operare in modalità backup
- [ ] bus_factor_1 conferma trasferimento ownership (per le voci dove si è scelto di farlo)
- [ ] Aggiornamento di questa matrix (sezione 2) con i nuovi backup

## 4. Procedura di assenza pianificata

Quando bus_factor_1 è in assenza pianificata (>1 settimana):

1. **Pre-assenza** (1 settimana prima):
   - Refresh tabletop exercise (sez. 3.7) per scenario probabile
   - Verificare deploy recente in stato verde (no PR pendenti rischiose)
   - Comunicare canale di emergenza (chi contattare se 2nd operator necessita escalation)

2. **Durante assenza**:
   - 2nd operator monitora: B-2 nightly, ticket urgenti, `incident_response.md` updates
   - 2nd operator esegue deploy solo per fix critici (no feature releases)
   - 2nd operator coordina con team per qualsiasi azione `runbook.md` §2 (recovery scenarios)

3. **Post-assenza**:
   - Briefing: 2nd operator sintetizza eventi rilevanti
   - Eventuale rotation: 2nd operator non torna automaticamente in backup-only mode

## 5. Out-of-scope

- ❌ Procedura di emergency RTO/RPO (vedi `dr_drill_playbook.md`)
- ❌ Service-level handoff (questo doc è solo per ops/deploy ownership)
- ❌ Code ownership del repo (vedi `CODEOWNERS` se esistente, altrimenti GitHub repo settings)
- ❌ Customer support / commerciale (fuori scope ops)

## 6. Gap noti (da chiudere progressivamente)

- Vault sharing process è manuale (no automation)
- AWS IAM user lifecycle: ad-hoc creation, no IaC dedicato
- DB read-only role provisioning: ad-hoc, non in migrations
- Staging environment: non esiste; il training di deploy reale resta bloccato finche' staging non e' disponibile

Questi gap sono accettabili al livello attuale (post-MVP, pre-scale).
Documentati per non perdere visibilità durante la maturazione operativa.
