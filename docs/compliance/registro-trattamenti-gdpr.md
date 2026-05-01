# Registro dei Trattamenti — GDPR Art. 30

**Titolare del trattamento**: il cliente che deploya URBY-ETL nel proprio dominio.
**Ruolo di questo registro**: URBY-ETL e' applicazione installabile. Il registro sotto descrive i trattamenti di dati personali **incorporati nel prodotto**; il cliente-titolare li integra nel proprio registro GDPR Art. 30 aggiungendo finalita' specifiche e, dove applicabile, dati trattati sopra l'applicazione (es. contenuti dei file sorgente caricati per ETL).
**Ultima revisione**: 2026-04-21.
**Scope**: trattamenti intrinseci al funzionamento del backend URBY-ETL. NON coprono i dati applicativi processati (rimasti nella responsabilita' del titolare — vedi §5).

## Indice trattamenti

| # | Trattamento | Finalita' | Base giuridica suggerita (Art. 6) |
|---|---|---|---|
| T1 | Autenticazione utente (login) | Controllo accessi | Art. 6(1)(b) esecuzione contratto / (f) legittimo interesse sicurezza |
| T2 | MFA TOTP opt-in | Rafforzamento autenticazione ruoli sensibili (A.9.4.2 ISO 27001) | Art. 6(1)(f) legittimo interesse |
| T3 | Audit trail azioni utente | Tracciabilita' CRUD/auth/login per accountability (A.12.4.1, A.12.4.3) | Art. 6(1)(c) obbligo di legge / (f) legittimo interesse |
| T4 | Log applicativi (request_id + logger) | Troubleshooting, detection incidenti | Art. 6(1)(f) legittimo interesse |
| T5 | Secret di sessione (JWT + blacklist Redis) | Continuita' di sessione + revoca | Art. 6(1)(b) / (f) |
| T6 | Credenziali connessioni dati utente | Esecuzione pipeline ETL per conto del titolare | Art. 6(1)(b) esecuzione contratto col titolare |

Sotto, per ogni trattamento: categorie di dati, destinatari, trasferimenti, retention, misure di sicurezza, riferimenti di codice.

---

## T1 — Autenticazione utente (login)

**Finalita'**: verificare l'identita' dell'utente prima di consentire accessi ad API/route protette.

**Categorie di dati personali**:
- `plenora.users.username` — identificativo scelto al momento della registrazione.
- `plenora.users.password_hash` — hash bcrypt della password (NON la password in chiaro).
- `plenora.users.role` + `plenora.users.group_id` — ruolo applicativo e appartenenza a gruppo organizzativo.
- `plenora.users.created_at` / `last_login_at` / `is_active` — metadati ciclo di vita.

**Categorie di interessati**: utenti applicativi abilitati dal cliente-titolare (dipendenti, consulenti, utenti esterni autorizzati). Numerosita' tipica per deploy: 10–100 account.

**Destinatari**:
- Interno all'app: `app.core.auth.auth.authenticate`, `get_current_user`.
- Esterno: nessuno. Nessun SSO federato, nessun IdP esterno, nessuna email (vedi §6).

**Trasferimenti extra-UE**: nessuno nella versione on-premise/lab. Se il titolare sceglie il target deploy AWS eu-west-1 (Ireland), il dato resta in UE.

**Retention**:
- Record utente: vita dell'account. Disattivazione via `is_active=FALSE`, cancellazione hard su richiesta di esercizio dei diritti (Art. 17 GDPR).
- `last_login_at` viene aggiornato a ogni login; nessuna storicizzazione dei login (quella vive in T3).

**Misure di sicurezza tecniche**:
- Password hash: bcrypt con salt per-utente (standard), mai in log o response.
- Trasporto: HTTPS obbligatorio in prod (enforced lato reverse proxy / nginx).
- Rate-limit login via middleware dedicato (vedi `app/core/auth/auth.py` handler `/auth/login`).
- Failed login non espone info sensibili al client: risposta generica "credenziali non valide".

**Misure di sicurezza organizzative**:
- RBAC 5 ruoli (superadmin / admin / user / viewer / esecutore) — vedi `docs/architecture/invariants.md §2`.
- Disattivazione account gestita da admin via API `/users/{id}` (non cancellazione — lascia traccia storica nei log T3).

**Riferimenti codice**:
- `app/core/auth/auth.py:254` `create_access_token`
- `app/core/auth/auth.py:/auth/login` handler
- `app/db/migrations/*_users.sql` schema tabella

---

## T2 — MFA TOTP opt-in

**Finalita'**: rafforzare l'autenticazione contro password spraying / credenziali compromesse, in particolare per ruoli `admin` / `superadmin`. Coerente con ISO 27001 A.9.4.2.

**Categorie di dati personali**:
- `plenora.user_mfa.user_id` — FK utente (gia' coperto da T1).
- `plenora.user_mfa.secret_encrypted` — secret TOTP base32 cifrato Fernet via `app.shared.crypto`. Non e' un dato personale in senso stretto ma e' un **fattore di autenticazione** personale e va protetto di conseguenza.
- `plenora.user_mfa.enrolled_at`, `last_verified_at`, `enabled` — metadati.

**Categorie di interessati**: sottoinsieme degli utenti T1 che hanno fatto opt-in all'MFA (deploy-specific).

**Destinatari**:
- Interno: `app.core.auth.mfa` (enroll/verify/disable/status).
- Esterno: app authenticator dell'utente (Google Authenticator, Authy, 1Password, ecc.) — NON riceve dati da URBY-ETL dopo l'enrollment; il QR code e il secret vengono scambiati solo al momento dell'enroll.

**Trasferimenti extra-UE**: nessuno (cfr. T1).

**Retention**:
- Record MFA: vita dell'enrollment. Disable via `/auth/mfa/disable` rimuove la riga (`ON DELETE CASCADE` su users la rimuove anche su cancellazione utente).
- Il secret non viene mai loggato in chiaro: `decrypt()` e' invocato solo in memoria durante `verify_code`.

**Misure di sicurezza tecniche**:
- Secret cifrato at-rest Fernet AES-128-CBC + HMAC-SHA256 (vedi ADR-0003, `app/shared/crypto.py`).
- Disable richiede proof-of-possession (valido TOTP code) per limitare il blast radius in caso di JWT rubato.
- Re-enroll sovrascrive il secret e resetta `enabled=FALSE` (use-case: perdita dispositivo, richiede riverifica).

**Riferimenti codice**:
- `app/core/auth/mfa.py` (commit `c3d3aae`)
- `backend/migrations/2026-04-21_add_user_mfa.sql`

---

## T3 — Audit trail azioni utente

**Finalita'**: accountability e non-ripudio sulle operazioni sensibili (auth, CRUD connessioni/datasources/credenziali). Richiesto da ISO 27001 A.12.4.1 (event logging) e A.12.4.3 (administrator logs).

**Categorie di dati personali**:
- `plenora.audit_actions.user_id` — FK utente.
- `plenora.audit_actions.username` — denormalizzato al momento dell'azione (per leggibilita' post-hoc anche se l'utente viene cancellato).
- `plenora.audit_actions.action` — tipo di operazione (`connection.create`, `datasource.delete`, `login`, ecc.).
- `plenora.audit_actions.resource_type` / `resource_id` — target dell'azione.
- `plenora.audit_actions.client_ip` — IP client (risolto via trusted-proxy allowlist — vedi ADR-0010).
- `plenora.audit_actions.user_agent` — User-Agent HTTP.
- `plenora.audit_actions.metadata` — JSON con eventuali campi contestuali (escluso il secret stesso — es. su rotation si registra che e' avvenuta, NON il nuovo valore).
- `plenora.audit_actions.occurred_at` — timestamp.

**Categorie di interessati**: utenti T1 che eseguono operazioni tracciate. Ogni azione produce una riga.

**Destinatari**:
- Interno: `app.shared.audit.audit_action` decorator + endpoint admin di consultazione (solo ruoli elevati).
- Esterno: nessun export automatico. Export on-demand via API admin per adempimenti (es. risposta a richiesta Art. 15 GDPR).

**Trasferimenti extra-UE**: nessuno.

**Retention**: indefinita by-design per audit trail (immutabilita' enforced da trigger BEFORE UPDATE/DELETE in `backend/migrations/2026-04-21_log_immutable_triggers.sql`). Eccezioni controllate da sessione DB che setta `plenora.retention_cleanup = 'true'` (script di retention-policy, non hot-path applicativo). Retention policy consigliata: **24 mesi minimo** (coerente con art. 30 principio di minimizzazione nel tempo), definita dal cliente-titolare in base al proprio registro.

**Misure di sicurezza tecniche**:
- Immutabilita' enforced via trigger PL/pgSQL (UPDATE e DELETE rigettati fuori dalla sessione di cleanup autorizzato).
- Fail-open sulla scrittura del decoratore (`@audit_action`): un errore nell'audit NON blocca l'operazione utente — registra solo il fallimento via `logger.warning`. Scelta deliberata per evitare che un audit guasto trasformi ogni endpoint in un 500.
- IP client risolto via trusted-proxy allowlist per evitare spoofing `X-Forwarded-For` (ADR-0010).

**Riferimenti codice**:
- `app/shared/audit.py` — decorator
- `backend/migrations/2026-04-21_log_immutable_triggers.sql` — trigger immutabilita'
- `backend/tests2/security/unit/test_log_immutable_contract.py` — test contratto

---

## T4 — Log applicativi (request_id + logger)

**Finalita'**: troubleshooting, detection di incidenti, correlazione eventi cross-request.

**Categorie di dati personali**:
- `plenora.logs.request_id` — UUID per-richiesta, generato dal middleware `RequestIDMiddleware`. Non e' un dato personale in se', ma collega righe di log alla stessa sessione HTTP.
- `plenora.logs.user_id` — opzionale, presente se la richiesta e' autenticata.
- `plenora.logs.message` — contenuto testuale del log. **Non deve contenere secret o PII sensibili**: policy enforced via code-review e grep di pattern comuni.
- `plenora.logs.timestamp` / `level` / `module`.

**Categorie di interessati**: utenti autenticati + traffico anonimo (health-check, pre-auth).

**Destinatari**: interno. Lettura riservata a ruoli admin via query DB diretta (no endpoint pubblico che dumpa tutti i log).

**Trasferimenti extra-UE**: nessuno.

**Retention**: stessa policy di T3 (immutabili via trigger, retention gestita via script con bypass controllato).

**Misure di sicurezza tecniche**:
- Logger centrale (`app.utils.logger`) — formatting strutturato, nessun `print` libero.
- Policy "no secret in log": `app.shared.crypto.decrypt` non logga il valore; `migrate_env_to_ssm.py` logga solo fingerprint SHA-256[0:8].
- `request_id` permette correlation senza esporre dati sensibili.

**Riferimenti codice**:
- `app/utils/logger.py`
- `app/core/auth/request_id_middleware.py`

---

## T5 — Sessione JWT + blacklist Redis

**Finalita'**: mantenere la sessione autenticata tra richieste, consentire revoca (logout).

**Categorie di dati personali**:
- **Access token JWT**: contiene `sub` (user_id), `username`, `group_id`, `role`, `exp`, `iat`, `jti`. Non e' "at-rest" lato server (stateless JWT), vive nel client.
- **Blacklist Redis**: chiave `plenora:jwt_blacklist:<jti>`, TTL = `exp - now`. Nessun dato personale oltre al jti (UUID random, non riconducibile all'utente senza lookup DB).

**Categorie di interessati**: utenti autenticati attivi.

**Destinatari**: client (browser/API caller) + Redis locale al deploy.

**Trasferimenti extra-UE**: nessuno (Redis single-instance sul nodo / VPC privata).

**Retention**:
- JWT client-side: max TTL 8h (`JWT_EXPIRE_HOURS` default 8, ADR-0004).
- Blacklist Redis: auto-cleanup alla scadenza naturale del token.

**Misure di sicurezza tecniche**:
- JWT firmato HS256 con `JWT_SECRET_KEY`, rotazione come da `docs/ops/jwt-incident-response.md`.
- Blacklist policy fail-open READ + fail-loud WRITE (ADR-0009).
- Chiave di firma separata dalla chiave di cifratura at-rest (ADR-0003, crypto.py:25-30).

**Riferimenti codice**:
- `app/core/auth/auth.py:254` `create_access_token`
- `app/shared/token_blacklist.py`

---

## T6 — Credenziali connessioni dati gestite dall'utente

**Finalita'**: permettere all'utente di configurare sorgenti dati (DB, API REST, SharePoint, FTP, SFTP, OAuth) per pipeline ETL, conservando le credenziali cifrate at-rest.

**Categorie di dati personali / sensibili**:
- `plenora.connections.password` (Fernet-encrypted) — credenziali DB.
- `plenora.api_connections.config.auth.*` (Fernet-encrypted su campi sensibili) — token API, client_secret OAuth, SharePoint credentials.
- `plenora.datasources.config.auth.*` (Fernet-encrypted) — credenziali FTP/SFTP/OneDrive.

**Nota**: NON si tratta di dati personali dell'utente autore, ma di **credenziali a sistemi terzi** che l'utente ha autorizzazione di usare. Possono includere credenziali service-account o di altre persone fisiche. Il titolare cliente deve garantire che gli utenti inseriscano solo credenziali di cui hanno diritto.

**Categorie di interessati**: utenti che configurano connessioni (tipicamente un sottoinsieme degli utenti T1 con ruolo `user` o superiore). Interessati indiretti: titolari delle credenziali inserite (spesso = cliente-titolare, raramente terzi).

**Destinatari**:
- Interno: `app/services/connection_service.py`, executor dei moduli ETL.
- Esterno: il sistema target delle credenziali (es. server PostgreSQL, API REST, endpoint SharePoint) al momento dell'esecuzione della pipeline. Solo `https://` / TLS per target esterni.

**Trasferimenti extra-UE**: dipende dai sistemi target scelti dal cliente. Fuori dallo scope del prodotto.

**Retention**: vita della connessione. Cancellazione via API da utente/admin rimuove la riga (no soft-delete per credenziali).

**Misure di sicurezza tecniche**:
- Cifratura at-rest Fernet AES-128-CBC + HMAC-SHA256 (ADR-0003).
- Chiave di cifratura `PLENORA_ENCRYPTION_KEY` gestita come SecureString in AWS SSM (target state — vedi `secrets-management.md`).
- Decrypt avviene solo in memoria al momento di eseguire la connessione/pipeline, mai emesso in log o response.
- `encrypt_dict_fields` applicato solo su campi in whitelist; le restanti config (host, port, database, username) restano in chiaro per ispezione/debug.

**Riferimenti codice**:
- `app/shared/crypto.py`
- `app/services/connection_service.py`

---

## 5 — Dati applicativi processati (fuori scope di questo registro)

I dati che l'utente **carica** o **legge** tramite le pipeline ETL (file Excel, export GeoJSON, output di API REST, risultati `eseguibili`, ecc.) **non sono trattati da questo registro**: URBY-ETL e' lo strumento, il titolare del trattamento per quei dati e' il cliente che deploya l'applicazione. Il cliente deve:

1. Identificare le finalita' specifiche del trattamento (es. "gestione patrimonio immobiliare ferroviario", "conciliazione fatture", ...).
2. Definire le basi giuridiche.
3. Gestire retention, minimizzazione, export su richiesta, oblio sui **propri** dati applicativi.

URBY-ETL fornisce le misure di sicurezza tecniche (cifratura at-rest, RBAC, audit trail, log immutability) come **supporto al titolare**, non si sostituisce ad esso.

---

## 6 — Trattamenti NON presenti

Per chiarezza di scope:

- **Email / SMTP**: nessun invio di email da URBY-ETL. Nessun worker SMTP configurato.
- **Notifiche push / SMS**: non implementate.
- **Telemetria product analytics** (Mixpanel, Amplitude, Segment): non presente. Nessun SDK di tracking lato backend.
- **Cookie di terze parti / marketing**: frontend SPA usa solo il JWT in memoria/localStorage, nessun cookie marketing.
- **Trasferimenti a data processor esterni**: nessun sub-processor nel prodotto as-shipped. Se il cliente deploya su AWS, AWS diventa sub-processor per storage (S3), secrets (SSM), compute (EC2) — contratto DPA diretto cliente-AWS.

---

## 7 — Esercizio dei diritti (Art. 15-22 GDPR)

L'applicazione offre gli **strumenti tecnici** per supportare l'esercizio dei diritti. La gestione della richiesta e del riscontro e' responsabilita' del cliente-titolare.

| Diritto | Strumento tecnico disponibile |
|---|---|
| **Accesso** (Art. 15) | Query SQL su `plenora.users`, `plenora.audit_actions`, `plenora.logs` filtrata per `user_id`. Non esiste endpoint self-service; admin esegue export manuale. |
| **Rettifica** (Art. 16) | Endpoint admin `PATCH /users/{id}` per dati anagrafici; password self-reset dall'utente. |
| **Cancellazione** (Art. 17) | `DELETE /users/{id}` cancella il record + cascade su `user_mfa`. **`audit_actions` e `logs` NON vengono cancellati** (obbligo ISO 27001 A.12.4.1 + principio di accountability). Il diritto all'oblio va bilanciato con obblighi normativi: il cliente-titolare documenta la base giuridica della conservazione. |
| **Limitazione** (Art. 18) | Disattivazione via `is_active=FALSE` (vedi T1). |
| **Portabilita'** (Art. 20) | Export JSON via API admin. Scope: dati T1 + credenziali connessioni (cifrate — export utile solo per reimport nello stesso deploy). |
| **Opposizione** (Art. 21) | NA — nessun trattamento basato su legittimo interesse per marketing/profilazione. |

---

## 8 — Violazioni (Art. 33-34)

Playbook di risposta incidenti: `docs/ops/jwt-incident-response.md` + §"Incidente" in `secrets-management.md`.

Riepilogo flusso:
1. Contenimento: rotazione secret impattati (JWT, DB, PLENORA_ENCRYPTION_KEY).
2. Analisi: audit trail T3 + log T4 (conservati in forma immutabile) permettono la ricostruzione della timeline.
3. Notifica: compilare `docs/compliance/data-breach-template.md` (_da creare quando il primo incidente lo richiedera'; in Fase 1 non scritto per evitare documentazione decorativa inutilizzata_).

---

## 9 — Revisione

Questo registro va rivisto:
- Ogni volta che si aggiunge un trattamento (es. quando si aggiungera' SMTP, SSO, telemetria).
- Ogni 12 mesi per verifica di accuratezza.
- A ogni cambio di architettura rilevante (es. passaggio da deploy on-prem a SaaS gestito).

**Prossima revisione programmata**: 2027-04-21.

## Riferimenti

- `docs/architecture/invariants.md` — invarianti architetturali (crypto, auth, RBAC)
- `docs/compliance/soa-iso-27001.md` — Statement of Applicability ISO 27001
- `docs/compliance/secrets-management.md` — gestione secret
- `docs/ops/jwt-incident-response.md` — playbook rotazione JWT
- `docs/architecture/decisions/0003-*.md` — ADR crypto at-rest
- `docs/architecture/decisions/0004-*.md` — ADR JWT HS256 TTL 8h
