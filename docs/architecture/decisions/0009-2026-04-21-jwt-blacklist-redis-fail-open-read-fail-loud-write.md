# ADR-0009: JWT JTI blacklist Redis — policy fail-open READ + fail-loud WRITE

**Status**: Accepted
**Date**: 2026-04-21
**Deciders**: @CloudIA

## Context

URBY-ETL emette JWT stateless (ADR-0004) con TTL 8h. Ogni token include un claim `jti` (uuid4 hex) unico. L'endpoint `POST /auth/logout` deve invalidare il token corrente prima della sua scadenza naturale.

Il JWT puro non ha revoca. L'aggiunta di una **blacklist Redis** permette revoca opt-in:

- Logout scrive `SETEX plenora:jwt_blacklist:<jti> <ttl> 1` con `ttl = exp - now`.
- Ogni richiesta autenticata, `get_current_user` fa `is_jti_blacklisted(jti)` → 401 se presente.
- TTL = TTL del token residuo → auto-cleanup al natural expiry (no GC manuale).

Domanda di design aperta: **cosa fare se Redis e' down**?

Due operazioni, due policy possibili ciascuna:

- **READ** (ad ogni request): fail-open (passa se Redis down) vs fail-closed (rifiuta tutti i token).
- **WRITE** (al logout): fail-open (ritorna 200 OK senza realmente revocare) vs fail-loud (ritorna 503 + log).

## Decision

- **READ fail-open**: `is_jti_blacklisted()` ritorna `False` se Redis e' down o timeout. Il token passa (se signature + exp sono validi).
- **WRITE fail-loud**: `/auth/logout` ritorna 503 Service Unavailable se non puo' scrivere in Redis.

Codice: `app/shared/token_blacklist.py` — modulo intero. Policy documentata nel module docstring (lines 17-38). Call-site:

- Read: `app/core/auth/auth.py:305-307` in `get_current_user`.
- Write: `app/core/auth/auth.py` handler `/auth/logout` (~ line 689+).

Chiave Redis namespaced: `plenora:jwt_blacklist:<jti>`. Redis condiviso con Celery broker (`CELERY_BROKER_URL` canonica unica — no env var dedicata).

## Rationale

### READ fail-open
- **Disponibilita' vs security strict**: se Redis e' down, fail-closed invaliderebbe TUTTI i token → l'intera applicazione offline finche' Redis non torna. Blast radius catastrofico per una dipendenza single-instance.
- **JWT signature + exp restano enforced**. Un attaccante con Redis-down finestra NON puo' forgiare token, NON puo' estendere quelli scaduti. Puo' solo usare un token **rubato e ancora non scaduto** nella finestra in cui il logout non viene applicato.
- **Bound sull'esposizione**: TTL max del token = 8h. Se Redis e' down per 10 minuti, un token rubato e loggato-out poco prima viene effettivamente revocato solo dopo che Redis torna, oppure alla sua scadenza naturale (al massimo 8h).
- **Alerting operativo compensa**: Redis e' monitorato (uptime + latenza). Un outage lungo triggera incident response, con rotazione emergenza di `JWT_SECRET_KEY` (`docs/ops/jwt-incident-response.md`) che invalida TUTTI i token e forza re-login — soluzione nucleare disponibile.

### WRITE fail-loud
- **Il client DEVE sapere se il logout e' effettivo**. Confermare falsamente una revoca (200 OK silenzioso senza scrittura) e' peggio di fallire: l'utente crede di aver chiuso sessione su un dispositivo condiviso, l'attaccante con il JWT rubato continua ad averlo valido per 8h. E' un bug di security posture percepita.
- **503 e' retry-friendly**. Il client puo' ritentare; se Redis torna, il logout passa. Se Redis e' giu' a lungo, il client sa di dover ricorrere a misure piu' drastiche (chiudere browser, cambiare password, ecc.).
- **Asimmetria giustificata**: disponibilita' del check (read) > garanzia della revoca (write) **per l'utente medio**; ma la garanzia della revoca > disponibilita' **per l'utente che sta facendo logout intenzionalmente, spesso sotto sospetto di compromissione**. La policy riflette chi paga il costo di ciascun failure mode.

### Token legacy senza `jti`
- Tokens emessi prima dell'introduzione del claim `jti` (rollout di `stream-acn`) non hanno blacklist possibile. `get_current_user` chiama `is_jti_blacklisted(None)` → ritorna False → il token passa.
- `/auth/logout` su token legacy logga `logger.info("Logout su token legacy senza jti, no blacklist", op="logout_legacy")` e ritorna 200 con `{"blacklisted": False}` per trasparenza client-side.
- Blast radius limitato: tutti i token legacy scadono entro 8h dal rollout della feature (ADR-0004 TTL). Dopo 8h non esistono piu' token senza jti. Finestra transitoria accettata.

## Alternatives considered

- **READ fail-closed** — **Rejected** per blast radius. Vedi sopra.
- **READ con cache local + backoff**. Leggere da Redis, se down usare cache in-process per N secondi — **Rejected** per complessita'. Una cache locale stale mitiga il danno ma aggiunge un meccanismo di consistency da testare. Fail-open bool e' piu' semplice e ha lo stesso esito pratico per outage brevi.
- **WRITE fail-open con logging** — **Rejected** per security posture percepita. Un logout silenzioso che non revoca veramente e' un bug latente.
- **Blacklist in Postgres invece di Redis** — **Rejected**. DB hot-path per ogni request autenticata = carico lock + connessione per operazione TTL-pure inutile. Redis ha semantiche nativamente adatte (SETEX, TTL auto-cleanup). Postgres con TTL "finto" via colonna `expires_at` + cron cleanup = piu' complesso.
- **No revoca (JWT puro, stateless assoluto)** — **Rejected**. Il caso d'uso logout esplicito e' troppo comune per essere ignorato. Lasciare i token scadere naturalmente in 8h dopo "Logout" cliccato = UX e security posture degradata.

## Consequences

### Positive
- Policy chiara, documentata nel module docstring (`token_blacklist.py:17-38`) — non serve archeologia del commit history.
- Revoca opt-in compatibile con JWT stateless.
- Redis down non rompe l'app: degradation, non outage. L'utente che legge non accorge.
- Redis down durante logout: l'utente sa (503) e puo' decidere come procedere.
- TTL auto-cleanup in Redis → nessuna gestione storage manuale.
- Condivisione Redis con Celery broker riduce infrastructure count (un solo Redis, un solo monitoraggio).

### Negative
- Window di esposizione su Redis down: token rubato + appena-logoutato resta valido fino al natural expiry o al ritorno di Redis.
- Richiede monitoring di Redis uptime affidabile per sapere quando il fail-open e' "attivo" (= invalidazioni potenzialmente non applicate). Alerting esistente in `monitoring.yml` (fuori repo backend).
- `is_jti_blacklisted` deve essere resiliente a timeout Redis (non hangare la request). Implementato con timeout su `redis.from_url(...).get()` — documentato in `token_blacklist.py`.

### Neutral
- Prefix `plenora:jwt_blacklist:` evita collisione con keyspace Celery (`celery`, `_kombu.binding.*`). Disciplina di naming esplicita.
- I test contract in `tests2/security/unit/` coprono: revoca + re-check, fail-open su Redis down (mockato), fail-loud su logout con Redis down.

## References

- Source: `backend/app/shared/token_blacklist.py` — intero modulo (~100 righe), docstring con policy
- Source: `backend/app/core/auth/auth.py:305-307` — read check in `get_current_user`
- Source: `backend/app/core/auth/auth.py:689+` — handler `/auth/logout` con fail-loud
- Source: `backend/app/core/auth/auth.py:254-268` — emissione `jti` in `create_access_token`
- Docs: `docs/ops/jwt-incident-response.md` — rotazione secret come "reset nucleare"
- Related: ADR-0004 (JWT HS256 + TTL 8h — questo ADR aggiunge la revoca opt-in)
- Invariants: `docs/architecture/invariants.md` §auth — disponibilita' read > garanzia write per blacklist
