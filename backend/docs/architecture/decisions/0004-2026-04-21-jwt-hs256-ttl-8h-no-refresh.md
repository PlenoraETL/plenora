# ADR-0004: JWT HS256, TTL 8h, senza refresh token

**Status**: Accepted
**Date**: 2026-04-21
**Deciders**: @CloudIA

## Context

URBY-ETL espone un'API REST a single-tenant (per deploy) con utenti interni del cliente. L'autenticazione deve:

1. Supportare un numero limitato di client (web SPA + occasional CLI/API integration, no massa di utenti tipo SaaS).
2. Essere stateless lato backend (ogni worker FastAPI puo' validare un token senza consultare DB/Redis per ogni request).
3. Supportare revoca esplicita (logout, incident response) — i JWT puri non hanno revoca, ma una blacklist opt-in la permette (vedi ADR-0009).
4. Resistere a compromissione di un singolo secret (rotazione documentata e fattibile).

Token design choices aperte:

- **Algoritmo**: HS256 (simmetrico) vs RS256/ES256 (asimmetrico).
- **TTL**: short-lived + refresh token vs long-lived senza refresh.
- **Claim**: quali info embeddare per evitare DB lookup a ogni request.

## Decision

- **Algoritmo HS256** con unica `JWT_SECRET_KEY` condivisa tra tutti i worker.
- **TTL fisso 8 ore** (`ACCESS_TOKEN_EXPIRE_HOURS = int(os.getenv("JWT_EXPIRE_HOURS", "8"))`). Nessun refresh token.
- **Claim embeddati**: `sub` (user_id), `username`, `group_id`, `role`, `exp`, `iat`, `jti` (uuid4 hex, per blacklist).
- **Verifica a ogni request**: `get_current_user` dependency FastAPI valida la signature, legge il payload, verifica blacklist (`is_jti_blacklisted(jti)` — fail-open su Redis down).
- **Secret loading**: `JWT_SECRET_KEY` da env. Dev con env non settata → warning rumoroso + secret random per la sessione (token invalidi al restart). Prod con env non settata → `RuntimeError` al boot.

Codice: `app/core/auth/auth.py:122-151` (loading secret), `:254-268` (`create_access_token`), `:270-276` (`decode_token`).

## Rationale

### HS256 vs RS256
- **Numero di audience**: il backend e' monolitico in questa Fase — nessun microservizio separato che debba verificare il token senza avere il potere di emetterlo. HS256 (shared secret) e' adeguato quando "chi verifica" == "chi emette".
- **Rotazione**: rotare una chiave simmetrica e' una operazione (update env, restart). Rotare una coppia RSA e' tre operazioni (generare, distribuire public key, restart) + gestione JWKS endpoint. Overhead non giustificato.
- **Performance**: HS256 e' ~10-100x piu' veloce di RS256 per verify. Su un path criticissimo (ogni request), la differenza e' tangibile sotto carico.
- **Dimensione token**: HS256 token e' piu' compatto. Marginale.
- **Security property**: HS256 e' teoricamente piu' fragile (chi ruba la chiave puo' anche forgiare, non solo verificare). Nel nostro modello di minaccia questo e' un non-problema — se un attaccante ha accesso al filesystem dove vive `JWT_SECRET_KEY` probabilmente ha anche accesso a `PLENORA_ENCRYPTION_KEY` e al DB; la compromissione e' totale.

### TTL 8h + no refresh
- **Perche' 8h**: giornata lavorativa tipica. L'utente fa login al mattino, lavora, il token scade a fine giornata. Ri-autenticarsi a ogni sessione e' una friction accettabile e un checkpoint naturale.
- **Perche' no refresh**: il vantaggio del refresh token e' ridurre l'esposizione di credenziali long-lived (password) a una singola chiamata di login + tenere access token short-lived (15 min) per limitare il blast radius di un token rubato. Trade-off opposto: raddoppia il numero di endpoint auth, richiede revocation anche del refresh (doppia blacklist o rotating jti), e aggiunge un nuovo vettore di attacco (furto del refresh).
- **Alternativa concreta che scarteremmo**: 15 min access + 30 giorni refresh. Il refresh diventa di fatto un long-lived credential; se l'utente lo perde, scattano le stesse procedure di incident della password. Non elimina il problema, lo sposta.
- **Blast radius di 8h**: un token rubato senza blacklist vale fino a 8h. Con `/auth/logout` (ADR-0009) la finestra e' max 8h se l'utente non si accorge del furto, ma tipicamente molto meno se rileva attivita' sospetta. Accettabile per audience interna.

### Claim embeddati
- **`sub`, `username`, `group_id`, `role`**: permettono a `get_current_user` di costruire `CurrentUser` senza lookup DB. Guadagno enorme per hot-path (ogni request autenticata).
- **Rischio di staleness**: se admin cambia `role` o `group_id` mentre il JWT esiste, l'utente mantiene i vecchi privilegi fino a scadenza token (max 8h). Mitigazione per operazioni critiche async: il re-check `recheck_user_authorized` (ADR-0005) al pickup Celery fa DB lookup fresh e puo' revocare. Per hot-path HTTP, accettato — la finestra di 8h e' bounded.
- **`jti`**: uuid4 hex (128 bit) usato come chiave blacklist Redis. Collision-free in pratica.
- **NO claim `permissions` / `scopes`**: l'app modella ruoli fissi, non scope fine-grained. Aggiungere scope richiederebbe ri-emettere tutti i token al cambio di grant → complica piu' che aiuta.

## Alternatives considered

- **RS256 con JWKS endpoint** — **Rejected** (Fase 0/1). Giustificato solo se in futuro si splittano i worker in microservizi separati con verify-only, o se si espone la verifica a terze parti (partner API). Riconsiderabile con ADR supersede.
- **Refresh token rotante (JWT + refresh DB-backed)** — **Rejected**. Raddoppia il codice di auth + aggiunge tabella `refresh_tokens` + rotation logic. Valore marginale nel nostro modello (audience interna, RBAC fisso, 8h ragionevoli). Se passiamo a SaaS multi-tenant o audience pubblica, riconsiderare.
- **Session cookie server-side (stateful session in Postgres/Redis)** — **Rejected**. Richiede session lookup a ogni request → DB/Redis hop mandatory. Tradeoff opposto al design attuale. Meno "SPA-friendly" (serve CSRF protection).
- **OAuth2 full flow + IdP esterno (Keycloak, Auth0, Azure AD)** — **Rejected**. L'applicazione e' deploy on-prem / lab; non esiste un IdP di default che possiamo assumere. Il cliente che vuole SSO puo' federare in futuro; ADR-0004 supersede quando arrivera' il caso reale.
- **TTL piu' corta (15-60 min)** — **Rejected senza refresh**. Ri-login ogni ora trasforma il backend in un friction generator. La combinazione "short TTL + no refresh" e' la peggiore delle opzioni.

## Consequences

### Positive
- Stateless: ogni worker FastAPI valida il token con `JWT_SECRET_KEY` in memoria, zero DB hops sul hot-path.
- Payload self-contained: `get_current_user` costa solo la signature verify.
- Rotazione = update env + restart. Procedura in `docs/ops/jwt-incident-response.md` (esiste) + `docs/compliance/secrets-management.md`.
- Compatibile con blacklist Redis (ADR-0009) per revoca.
- Nessuna dipendenza aggiuntiva (`PyJWT` era gia' presente pre-Fase 0).

### Negative
- Cambio di ruolo/group utente richiede max 8h per propagare lato HTTP (mitigato dal re-check al pickup Celery per task async — ADR-0005).
- Rotazione secret invalida TUTTI i token attivi → logout forzato di tutti gli utenti (accettato, documentato come step in incident response).
- HS256 significa che chi ruba la chiave puo' forgiare token arbitrari. Compensato dal fatto che stesso attaccante ruba anche `PLENORA_ENCRYPTION_KEY` (stesso filesystem) → compromissione totale comunque.
- No claim `permissions` → per operazioni critiche serve `recheck_user_authorized` + `assert_project_access` a livello endpoint (bug: se manca, la protezione RBAC non e' applicata — contromisure: audit trail + sweep security — vedi `project_security_audit` memory).

### Neutral
- `JWT_EXPIRE_HOURS` e' configurabile via env se un deploy richiede TTL diverso (es. 4h per ambienti ad alto rischio).
- `create_access_token` e `decode_token` sono funzioni pure — facile test unit.

## References

- Source: `backend/app/core/auth/auth.py:122-151` — loading `JWT_SECRET_KEY`, fail-fast in prod
- Source: `backend/app/core/auth/auth.py:254-268` — `create_access_token` + claim set
- Source: `backend/app/core/auth/auth.py:270-276` — `decode_token` (verify signature + exp)
- Source: `backend/app/core/auth/auth.py:/auth/logout` handler — blacklist + fail-loud su Redis down
- Docs: `backend/docs/ops/jwt-incident-response.md` — playbook rotazione
- Docs: `backend/docs/compliance/secrets-management.md` — procedura operativa
- Related: ADR-0003 (crypto at-rest — chiave separata di proposito), ADR-0005 (recheck al pickup Celery per mitigare staleness dei claim), ADR-0009 (blacklist Redis jti per revoca)
- Invariants: `backend/docs/architecture/invariants.md` §auth
