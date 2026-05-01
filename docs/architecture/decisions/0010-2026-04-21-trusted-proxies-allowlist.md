# ADR-0010: Risoluzione client IP via allowlist di trusted proxies (no trust-all)

**Status**: Accepted
**Date**: 2026-04-21
**Deciders**: @CloudIA

## Context

URBY-ETL loga il client IP per:

1. **Audit trail** (`plenora.audit_actions.client_ip`) — accountability, detection di pattern anomali.
2. **Rate limiting** per-IP su endpoint sensibili (login, API pubbliche).
3. **Log applicativi** `logger.info(..., client_ip=...)` — troubleshooting.

Il deploy tipico ha **un reverse proxy davanti** (nginx su lab.plenora.it, in futuro ALB/CloudFront). Il reverse proxy inoltra al backend aggiungendo header `X-Forwarded-For: <client_ip>, <proxy1>, <proxy2>, ...`.

Il problema: **se l'app legge `X-Forwarded-For` senza validazione, chiunque puo' spoofare il proprio IP** semplicemente aggiungendo l'header nella request originale.

Scenario attacco:
```
client malevolo → request con header "X-Forwarded-For: 127.0.0.1"
     ↓
nginx riceve, appende il vero IP:
   "X-Forwarded-For: 127.0.0.1, <vero_ip_malevolo>"
     ↓
backend legge X-Forwarded-For e prende il primo valore (127.0.0.1)
     ↓
audit log registra "127.0.0.1" invece del vero IP
```

La vulnerabilita' e' **spoofing dell'IP client** nei log e nel rate limiting (un attaccante puo' bypassare il rate-limit variando a piacere il valore spoofato).

## Decision

**Allowlist di IP/CIDR considerati "trusted proxies"** via env var `TRUSTED_PROXIES`. L'app risolve l'IP client **solo accettando `X-Forwarded-For` se il peer TCP e' in allowlist**; altrimenti usa `request.client.host` (il peer TCP diretto).

Codice: `app/shared/request_utils.py` — `_parse_trusted_proxies` (cached), `_is_trusted_peer`, API pubblica `resolve_client_ip(request)`.

Allowlist format (CSV in env):
- IP singoli: `"127.0.0.1,::1,10.0.0.5"`
- CIDR ranges: `"10.0.0.0/8,172.16.0.0/12"`
- Mix libero.

Default: `"127.0.0.1,::1"` — coerente con il setup single-host nginx → uvicorn sullo stesso box (come lab.plenora.it oggi).

Algoritmo per-request:

1. Se `request.client.host` NON e' in allowlist → **ignora** `X-Forwarded-For`. Ritorna `request.client.host`.
2. Se peer e' trusted → leggi `X-Forwarded-For`, prendi il **primo** valore (l'IP originale client, non il proxy intermedio), normalizza via `ipaddress.ip_address()`. Se invalido → fallback a `request.client.host`.

Gestione IPv4-mapped IPv6 (es. `::ffff:127.0.0.1`): viene estratto l'IPv4 equivalente e confrontato anche su quello. Necessario perche' setup dual-stack Linux tipici ricevono connessioni IPv4 come IPv6-mapped.

## Rationale

- **Trust del layer sottostante e' binario e verificabile**. `request.client.host` e' il peer TCP diretto — non puo' essere spoofato senza BGP hijack (fuori threat model). Se questo peer e' in una lista di IP che **noi** controlliamo (deploy operations), allora ci si puo' fidare di cio' che aggiunge/preserva in `X-Forwarded-For`.
- **Allowlist > trust-all** per default safe. Il comportamento senza config e' "solo localhost e' trusted" — se si deploya senza reverse proxy, l'app legge sempre il peer diretto, zero spoofing possibile. Il comportamento diventa laxer solo se l'operatore esplicitamente settando env var autorizza nuovi range.
- **Primo vs ultimo in `X-Forwarded-For`**: il convention e' client-first, proxy-chain-last. Prendere il primo e' corretto per ottenere l'IP originale (stesso comportamento di nginx `real_ip_module` e di ASGI `uvicorn --proxy-headers`).
- **`ipaddress` stdlib per normalizzazione**. Valida che il valore sia un IP reale (IPv4 o IPv6 valido), normalizza (`"::1"` vs `"0:0:0:0:0:0:0:1"` → stessa forma canonica), supporta CIDR matching in modo correct-by-construction. Niente regex fragile.
- **`lru_cache` sul parse**. La env var non cambia a runtime → parse una volta sola al primo call. Test monkeypatching l'env → chiave cache diversa → re-parse. Cache size 4 = margine per test multi-env.
- **Fallback robusto**: se `X-Forwarded-For` e' malformato (non parsable), si cade su `request.client.host`. Il log continua a funzionare con valore corretto ma di layer diverso.

## Alternatives considered

- **Trust-all `X-Forwarded-For` (leggere senza validare)** — **Rejected**. Permette spoofing come descritto nel context. Vulnerabilita' nota, documentata in OWASP (A04:2021 Insecure Design). Non accettabile.
- **`starlette.middleware.trustedhost.TrustedHostMiddleware`** — **Rejected per scope**. Quel middleware filtra `Host:` header (prevent host header attack), NON `X-Forwarded-For`. Problema ortogonale. Utile in futuro come layer aggiuntivo, ma non risolve questo.
- **Uvicorn `--proxy-headers` + `--forwarded-allow-ips`** — **Considerato, integrato in parte**. Uvicorn ha una feature simile che setta `request.client.host` al valore di `X-Forwarded-For` SE il peer e' in `--forwarded-allow-ips`. Problema: vincola la config al boot di uvicorn, non gestibile per test o per app che runnano dietro gunicorn+uvicorn workers. La nostra allowlist application-level e' piu' flessibile e coesiste con l'eventuale config uvicorn.
- **Hard-code localhost only** — **Rejected per deploy con ALB/CloudFront**. I deploy AWS hanno proxy in range non prevedibili (es. CloudFront IP ranges rotanti); serve configurabilita'.
- **Leggere `X-Real-IP` invece di `X-Forwarded-For`** — **Rejected come sostituto**. Stesso problema di spoofing. `X-Real-IP` e' nginx-specific e non standard. Preferiamo l'header standard + validazione del peer.

## Consequences

### Positive
- Default config sicura ("solo localhost trusted"). Deploy che non setta `TRUSTED_PROXIES` funziona e non spoofable.
- Deploy comuni (nginx locale, ALB interno) coperti con 1-2 righe di env.
- IP log affidabile → audit trail e rate-limiting efficaci.
- Zero nuove deps (solo `ipaddress` stdlib).
- Cache parse → overhead per-request ~= lookup set (O(1)).

### Negative
- Operatore deve ricordare di settare `TRUSTED_PROXIES` se deploya dietro ALB/CloudFront; altrimenti log vede sempre l'IP del proxy invece del client. Mitigazione: docstring esplicita nel modulo + check opzionale al boot (non implementato).
- IP client in `X-Forwarded-For` e' comunque spoofable da client se **prima** del proxy trusted (es. cliente MITM tra client e proxy). Fuori threat model — richiede gia' compromissione della rete.
- CIDR malformato nell'env var viene ignorato silenziosamente (non crash al boot). Trade-off: robustezza > rigor. Mitigazione: documentato in docstring; audit periodico della config.

### Neutral
- Cache size 4 e' minimale ma sufficiente per la realta' (la key cambia solo se l'env cambia — raramente). Aumentabile senza break.
- IPv4-mapped IPv6 (`::ffff:127.0.0.1`) gestito esplicitamente per compatibilita' dual-stack.

## References

- Source: `backend/app/shared/request_utils.py` — intero modulo (~130 righe)
- Source: `backend/app/shared/request_utils.py:20-28` — default allowlist + env `TRUSTED_PROXIES`
- Source: `backend/app/shared/request_utils.py:29-62` — `_parse_trusted_proxies_cached`
- Consumer: `app/shared/audit.py` (audit trail — usa `resolve_client_ip` se presente)
- Consumer: rate-limit middleware (`app/core/auth/auth.py` handler login)
- Related: ADR-0004 (JWT HS256 — audit trail che logga IP client usa questa risoluzione), ADR-0009 (blacklist jti — log di logout include IP)
- Invariants: `docs/architecture/invariants.md` §security — "IP client = peer TCP + X-Forwarded-For SOLO se peer in allowlist"
- OWASP: A04:2021 — Insecure Design / Improper trust of request headers
