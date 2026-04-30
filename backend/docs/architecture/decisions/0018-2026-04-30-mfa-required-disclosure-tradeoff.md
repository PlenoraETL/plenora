# ADR-0018: MFA enrollment-state disclosure: 403 `mfa_required` vs 401 generico

**Status**: Accepted
**Date**: 2026-04-30
**Deciders**: @CloudIA

## Context

Il login `POST /auth/login` (gestito in `backend/app/core/auth/auth.py:711-728`) e' stratificato:

1. Se username/password sbagliati → `401 "Username o password errati"` (constant-time bcrypt verify gia' applicato a `_DUMMY_PASSWORD_HASH` per evitare username enumeration via timing).
2. Se utente disabilitato → `403 "Account disabilitato"`.
3. Se utente ha MFA abilitato e `mfa_code` manca dal body → `403 "mfa_required"`.
4. Se `mfa_code` presente ma errato → `401 "mfa_invalid"`.

L'asimmetria tra (1) e (3) implica che **un attaccante con password corretta scopre se l'account ha MFA abilitato**: il primo tentativo senza `mfa_code` ritorna `403 mfa_required` invece di `401 unauthorized`. Questa e' una **disclosure di account-state** distinta dall'username enumeration che il punto (1) gia' protegge.

Il commento in `backend/app/core/auth/mfa.py:23-31` documenta il trade-off ma rinviava a un ADR mai creato (`TODO: ADR-0002`, in conflitto numerico — ADR-0002 e' invece "safe-number-identifier-vs-bindparam"). Questo documento chiude quel TODO.

Il login expone tre risposte distinguibili (`401 errati`, `403 disabilitato`, `403 mfa_required`); un attaccante che gia' possiede una credenziale valida puo' inferire MFA-on/MFA-off prima ancora di provare un codice TOTP. Il rate-limit IP-based (`LOGIN_MAX_ATTEMPTS=10` su `LOGIN_LOCKOUT_SECONDS=300`, `auth.py:82-84`) limita la scala dell'inferenza ma non la elimina.

Vincolo trasversale: deadline produzione 2026-05-05, perimetro single-tenant lab.plenora.it. Nessun integratore esterno consuma `/auth/login`: solo SPA frontend.

## Decision

**Mantenere il leak**: `403 mfa_required` resta come risposta esplicita al post-password successo senza `mfa_code`.

Il client SPA (frontend) usa la distinzione `401 invalid_credentials` vs `403 mfa_required` per orchestrare la UX MFA-aware (campo TOTP che appare solo dopo password ok). Collassare a `401 generico` romperebbe questo flusso o richiederebbe un secondo round-trip stateful (`POST /auth/login-stage1` → `POST /auth/login-stage2`) che amplifica la superficie e introduce statefulness lato server senza beneficio reale.

Mitigazioni gia' attive (non modificate da questo ADR):

- Rate-limit per-IP (`LOGIN_MAX_ATTEMPTS=10/300s`) limita l'enumeration MFA-state come limita username enumeration.
- Constant-time password verify su `_DUMMY_PASSWORD_HASH` se utente non esiste (`auth.py:702-703`) — preserva timing-blindness sul punto (1).
- Lockout password-failure conta anche i fail su `mfa_invalid` (`auth.py:724`), quindi un attaccante che indovina password ma sbaglia TOTP brucia anche il budget rate-limit.

## Rationale

### Perche' non collassare a 401 generico
- **UX rotta**: il frontend mostra il campo TOTP condizionatamente al `403 mfa_required`. Senza distinzione, l'utente MFA dovrebbe sempre vedere il campo (degrado UX) o ricevere errore generico dopo aver inserito password+TOTP corretti la prima volta (degrado funzionale).
- **Threat model asimmetrico**: l'attaccante che inferisce "MFA-on" non guadagna credenziali; sa solo che dovra' aggirare un secondo fattore. Il leak vale quasi-zero in attacco mirato (MFA si presume default presente per admin/superadmin) e poco anche in spray (il 403 arriva solo dopo password corretta — l'attaccante ha gia' l'account compromesso al netto del 2FA).
- **Statelessness preservata**: una soluzione two-step richiederebbe sessione/token intermedio fra step-1 e step-2 → nuova superficie (replay del token intermedio, TTL, blacklist). Non vale.

### Perche' non spostare a 401 con `WWW-Authenticate`
- Esiste l'opzione `401 + WWW-Authenticate: TOTP realm="..."` (RFC 7235 style) per uniformare lo status code mantenendo distinzione semantica. Non adottato perche':
  - SPA non legge `WWW-Authenticate` di default — richiederebbe codice client custom uguale al `403 mfa_required` attuale, senza ridurre la disclosure.
  - Il proxy/CDN potrebbe filtrare `WWW-Authenticate` non-Bearer. Fragile.

### Confronto con prassi industriale
- **GitHub**: stessa asimmetria su `/login/two-factor` — l'utente arriva alla pagina 2FA solo se password ok (disclosure equivalente).
- **Google**: distingue MFA-required tramite challenge step in OAuth2; pattern stateful a step.
- **Microsoft Entra ID**: distingue tramite `error_codes` espliciti nel body — leak equivalente.

URBY-ETL si allinea al pattern GitHub-like (stateless, distinzione esplicita via status code) per coerenza con l'architettura JWT senza session intermedie (vedi ADR-0004).

## Alternatives considered

- **Alt 1: 401 generico per (1) e (3)**
  rejected because rompe UX MFA-aware client SPA o richiede stateful two-step (vedi Rationale).

- **Alt 2: 401 + WWW-Authenticate header**
  rejected because non riduce la disclosure osservabile (status differente) e aggiunge complessita' client.

- **Alt 3: two-step login con token intermedio**
  rejected because viola la statelessness JWT (ADR-0004), apre nuova superficie (replay token intermedio, TTL, blacklist), e non e' giustificato dal threat model (single-tenant, admin perimeter).

- **Alt 4: enforce MFA per tutti gli account**
  rejected because policy MFA opt-in e' decisione di prodotto attuale (`mfa.py:6` "opt-in stretto"). Cambiarla richiede separato decision-record con onboarding utenti esistenti. Non in scope di questo ADR.

## Consequences

- **Positive**:
  - Trade-off noto e tracciato in ADR formale (compliance ISO 27001 A.9.4 / SOA control coverage).
  - Coerenza JWT stateless mantenuta (ADR-0004).
  - UX MFA-aware preservata.

- **Negative**:
  - Disclosure MFA-state per IP rate-limited a ~10 probe / 5 min (mitigato da rate-limit IP).
  - Se in futuro si abilita SSO/OIDC esterno, valutare riallineamento al pattern OAuth2 challenge-step.

- **Neutral**:
  - Nessun cambio codice da questo ADR. Documentazione gia' presente in `mfa.py:23-31` resta valida; l'unica modifica e' la sostituzione del riferimento errato `TODO: ADR-0002` con `vedi ADR-0018` (commit di follow-up cosmetico, non vincolante).

## References

- Source file: `backend/app/core/auth/auth.py:711-728` (login flow MFA branching), `backend/app/core/auth/mfa.py:23-31` (trade-off comment), `backend/app/core/auth/auth.py:82-84` (rate-limit attivo).
- Migration: `backend/migrations/2026-04-21_add_user_mfa.sql`.
- Related ADR: ADR-0004 (JWT HS256 TTL 8h no refresh) — coerenza statelessness; ADR-0009 (JWT blacklist Redis fail-open read fail-loud write) — invalidazione complementare.
- Compliance: ISO 27001 Annex A.9.4 (Access control) — registro decisioni rate-limiting + MFA scope (vedi `backend/docs/compliance/soa-iso-27001.md`).
