# ADR-0003: Crypto at-rest via `cryptography.Fernet` per campi sensibili

**Status**: Accepted
**Date**: 2026-04-21
**Amended**: 2026-04-23 — Consequences > Negative §`/proc/<pid>/environ`: promessa systemd hardening allineata alla realta' corrente (non applicata in Phase 2). Vedi `orchestrate/phase_2_hardening/runtime_config_audit_addendum_systemd_hardening_snapshot.md`.
**Deciders**: @CloudIA

## Context

URBY-ETL persiste credenziali e secret su DB Postgres:

- `plenora.connections.password` — password di DB remoti (Postgres, Oracle, SQL Server, MySQL, ecc.) configurati dall'utente come sorgenti/destinazioni ETL.
- `plenora.api_connections.config.auth.*` — token API, `client_secret` OAuth2, credenziali SharePoint/Teams (payload JSON con sotto-campi).
- `plenora.datasources.config.auth.*` — credenziali FTP / SFTP / OneDrive.
- `plenora.user_mfa.secret_encrypted` — secret TOTP base32 per MFA opt-in (aggiunto in stream-acn batch 4A).

Il vincolo di sicurezza: il DB non deve contenere secret in chiaro. Un `pg_dump` del backup, un accesso read-only all'istanza, o uno screen-share con una query `SELECT *` non devono leakare credenziali.

Vincoli aggiuntivi:

1. **Backward-compat**: prima di Fase 1 esisteva gia' crypto at-rest per `connections.password` in `app/services/connection_service.py` con formato `"enc:<base64>"`. I valori esistenti in produzione devono continuare a essere decifrabili senza rewrite DB.
2. **Multi-campo**: diversi tipi di config JSON hanno diversi campi sensibili. Il modulo deve esporre sia API "single value" (`encrypt/decrypt`) che "dict with whitelist" (`encrypt_dict_fields`).
3. **Idempotenza**: la stessa config puo' essere salvata piu' volte (frontend auto-save); cifrare un valore gia' cifrato NON deve produrre doppia cifratura (illeggibile dopo una sola `decrypt`).
4. **Dev ergonomico**: in locale senza env key configurata, non deve crashare — serve fallback con warning (ma fail-fast in prod).

## Decision

Centralizzare crypto at-rest in `app/shared/crypto.py`, unico modulo che esporta `encrypt`, `decrypt`, `is_encrypted`, `encrypt_dict_fields`, `decrypt_dict_fields`. Implementazione:

- **Algoritmo**: `cryptography.fernet.Fernet` — AES-128-CBC + HMAC-SHA256, IV random per messaggio, token base64-urlsafe. Authenticated encryption (tamper detection built-in via HMAC).
- **Derivazione chiave**: SHA-256 della raw env key → primi 32 byte → urlsafe_base64 → chiave Fernet. Deterministica, matcha il formato legacy di `connection_service.py`.
- **Chain di env source** (in ordine di priorita'): `PLENORA_ENCRYPTION_KEY` (canonico) → `URBY_ENCRYPTION_KEY` (alias retro-compat) → `CONN_ENCRYPT_KEY` (legacy). La chain **non fallbacka su `SECRET_KEY` / `JWT_SECRET_KEY`**: quei secret appartengono al dominio auth JWT e la separazione e' intenzionale (rotazione JWT non deve invalidare i record cifrati).
- **Formato value**: `"enc:<base64>"`. Il prefisso permette di:
  1. distinguere a colpo d'occhio valori cifrati in `psql`,
  2. supportare legacy plaintext — `decrypt()` di valore senza prefisso ritorna il valore as-is (backward-compat per campi non ancora migrati),
  3. `encrypt()` idempotente — se il valore inizia gia' con `enc:`, ritorna il valore cosi' com'e'.
- **Comportamento se chiave mancante**:
  - `ENVIRONMENT=production` o `ENV=prod` → **`RuntimeError` esplicito al boot**. Nessun fallback hardcoded.
  - Dev/test → usa una chiave di default con `logger.warning` rumoroso a ogni call. Permette di lavorare in locale senza configurare nulla; cambiare poi la chiave richiede re-encryption manuale dei valori salvati.
- **`encrypt_dict_fields(d, whitelist)`**: cifra solo i campi in whitelist, preservando struttura e altri campi (es. host, port, database restano leggibili). Idempotente per-campo (usa `is_encrypted`).

## Rationale

- **Fernet scelto per maturita'**: parte di `cryptography` (package maintained, cross-platform, con attenzione a side-channel e constant-time comparison). Ampia adozione in ecosystem Python Flask/Django. Format stabile da 2014.
- **AES-128-CBC + HMAC-SHA256 vs AES-256-GCM**: differenza pratica trascurabile per threat model (attaccante post-exfiltration del dump). Vantaggio: Fernet e' pre-packaged, nessuna gestione manuale di nonce/tag/AEAD. Un refactor a GCM via `AESGCM` e' un ADR successivo se/quando servira'.
- **Derivazione SHA-256 invece di PBKDF2/scrypt**: la chiave di input e' gia' high-entropy (generata via `openssl rand -hex 32` in produzione), non una password umana. PBKDF2 con high iteration count e' overkill e rallenta ogni boot. SHA-256 collision-resistance basta per questo use case.
- **Prefisso `enc:` vs versionato `v1:enc:`**: si e' scelto di non versionare. Se in futuro si introduce AES-GCM, un nuovo prefisso (`enc2:` o `fgcm:`) permettera' coesistenza e migrazione lazy. Il prefisso attuale e' frozen.
- **Fail-fast in prod, warn in dev**: `RuntimeError` al boot e' desiderato — il backend non deve partire se non puo' decifrare i record esistenti. In dev, il warning rumoroso al primo `encrypt()`/`decrypt()` basta per far notare il problema senza bloccare lo sviluppo su checkout fresh.
- **Chiave separata da JWT**: documentata nel docstring (`crypto.py:25-30`). Rotazione JWT e' evento frequente (incident response, scadenza); rotazione chiave crypto at-rest e' invece operazione rara con downtime (richiede re-encryption full). Unire i due secret causerebbe un coupling fatale.

## Alternatives considered

- **PostgreSQL `pgcrypto` extension (column-level encrypt)** — **Rejected**. La chiave finirebbe nel DB (via `CREATE FUNCTION` con `DEFINER` che embed la key) oppure in `current_setting`, che e' leggibile da qualsiasi ruolo. Inoltre la dipendenza da extension Postgres lato applicazione rompe il principio di portabilita' (dev con SQLite, CI con Postgres base image, prod con Postgres + pgcrypto — tre path diversi per lo stesso operatore).
- **AWS KMS envelope encryption** — **Rejected per Fase 0/1**. Introduce dipendenza hard da AWS (offline dev impossibile senza mock) + latenza per chiamata KMS a ogni decrypt (hot-path per login, preview, execute pipeline). Costo per-call non trascurabile a scala ETL. Vantaggio di non tenere la chiave lato app e' reale ma non giustifica la riscrittura di ~100 call-site. Riconsiderabile se/quando il target passa a SSE-KMS puro senza envelope lato app.
- **Hashing (one-way) invece di cifratura** — **Rejected**. I secret devono essere **riusati** per connettersi al sistema target (Postgres remoto, API REST). Hash irreversibile rende la feature impossibile. Valido solo per password di login utente, dove infatti usiamo bcrypt (non in questo ADR).
- **Envelope file + `gpg`** — **Rejected**. Operazione manuale, non integrabile in un save-path dinamico (utente salva nuova connection → codice applicativo deve cifrare in-process).
- **Libreria `nacl`/`PyNaCl`** — **Rejected**. Non intrinsecamente peggio di `cryptography`, ma `cryptography` e' gia' dep transitiva (via `jwt`, `requests`, ecc.) e mantenuta da PyCA che include anche `hazmat` come base per KMS futuri. Ridurre il count di crypto lib a 1 e' preferibile.

## Consequences

### Positive
- Un solo punto di audit crypto (`app/shared/crypto.py`, ~300 righe).
- Formato value auto-descrittivo (`enc:` prefix) semplifica debugging e migrazioni.
- Idempotenza su `encrypt()` evita bug sottili su double-save (frontend auto-save, replay di webhook).
- Backward-compat con legacy plaintext: migration lazy possibile (il primo save cifra).
- Chain di env key permette migrazione gentile da `URBY_*` a `PLENORA_*` senza big-bang.
- `encrypt_dict_fields` + whitelist riduce il rischio di cifrare accidentalmente campi che devono restare leggibili (host, port) e viceversa.

### Negative
- Rotazione della chiave = **downtime** (re-encryption di tutti i record). Documentato in `secrets-management.md` §"Rotazione PLENORA_ENCRYPTION_KEY". Non automatizzato by-design: e' un'operazione rara che deve essere pianificata.
- Chiave vive in `os.environ` → leggibile via `/proc/<pid>/environ` su Linux da processi con lo stesso UID (o root). Baseline corrente in prod (verificato 2026-04-23, vedi addendum `runtime_config_audit_addendum_systemd_hardening_snapshot.md`): permessi kernel default `0400 ubuntu:ubuntu` su `/proc/<pid>/environ`, `/proc` montato senza `hidepid`. Un attaccante deve gia' avere code execution come `ubuntu` (o root) per leakare la chiave — perimetro coincidente con accesso al processo. Hardening systemd aggiuntivo (`ProtectProc=invisible` + companion `ProcSubset=pid`/`NoNewPrivileges=yes`/`PrivateTmp=yes`) **NON applicato in Phase 2** — scope di un ADR successivo se/quando si apre una finestra deploy dedicata. Vera soluzione long-term: migrazione a SSM (vedi `secrets-management.md` — stato target).
- Fernet token size ~ 1.5x del plaintext (base64 + overhead IV/HMAC). Overhead trascurabile per secret brevi (< 1 KB), ma da considerare se si cifrano grandi blob (non nel nostro caso oggi).

### Neutral
- Test unit in `tests2/security/unit/test_crypto_at_rest_contract.py` (esistente pre-stream-acn) coprono: roundtrip, idempotency, is_encrypted, fallback legacy plaintext, behavior su env mancante.
- Il modulo espone anche `encrypt_dict_fields` / `decrypt_dict_fields` usati da `connection_service` e `api_connections_service`.

## References

- Source: `backend/app/shared/crypto.py` — intero modulo, ~300 righe
- Source: `backend/app/services/connection_service.py` — call-site originale (pre-centralizzazione)
- Source: `backend/migrations/2026-04-21_add_user_mfa.sql` — MFA secret cifrato con lo stesso modulo
- Docs: `backend/docs/compliance/secrets-management.md` — procedura di rotazione
- Related: ADR-0004 (JWT HS256 — chiave separata da questa), ADR-0009 (blacklist Redis — dato non-persistent, non cifrato at-rest)
- Invariants: `backend/docs/architecture/invariants.md` §crypto — "un secret, un consumatore, un nome canonico"
