# Phase 0.3 — Security Boundary + Env Audit

**Data**: 2026-04-27
**Stato**: design + audit **read-only** completati. Nessun codice/test toccato. A2/A3 marker proposti come diff esatti (stop pre-implementation, in attesa di approvazione).
**Vincoli rispettati**: nessun deploy, nessun cambio behavior, nessun SSH (probe 0.2 sufficiente), batch piccolo, stop con report.

---

## 1. SSRF — design proposal

### 1.1 Stato attuale (verificato dal codice)

Due funzioni coesistono in moduli diversi con scopo identico:

| Funzione | File | Interfaccia | Loopback opt-in |
|---|---|---|---|
| `validate_external_url(url) -> None` | `app/shared/api_utils.py:67` | raise `ValueError` | si (`PLENORA_ALLOW_LOOPBACK_OUTBOUND`, guard hardened envs) |
| `_validate_outbound_url(url) -> Optional[str]` | `app/core/arricchisci/api_connection_source_loading.py:499` (`@staticmethod` mixin) | return `None`/error string | no opt-in |

**Call sites** (grep `app/`):

| Caller | Funzione usata | Linea |
|---|---|---|
| `arricchisci/router.py` | `validate_external_url` (via wrapper `_validate_external_url`) | 153, 172, 675 |
| `arricchisci/api_connection_management.py` | `validate_external_url` (test_api_connection) | 570 |
| `arricchisci/api_connection_generator_enricher.py` | `validate_external_url` | 467, 737 |
| `arricchisci/api_connection_rest_file.py` | `self._validate_outbound_url` | 512 |

**Asimmetria**: 5 call sites raise vs 1 call site return-string. Il return-string e` usato **solo da REST_FILE** perche` `_execute_rest_file` ha una propria error taxonomy `_rest_file_error` e vuole costruire la response senza catch ValueError.

### 1.2 Differenze sostanziali tra le due implementazioni

| Feature | `validate_external_url` (shared) | `_validate_outbound_url` (mixin) |
|---|---|---|
| Schema check | http/https only | http/https only |
| Hostname mancante | bloccato | bloccato |
| `is_loopback` | bloccato (con opt-in test/dev) | bloccato sempre |
| `is_private` | bloccato | bloccato |
| `is_link_local` | bloccato | bloccato |
| `is_reserved` | bloccato | bloccato |
| `is_multicast` | bloccato | bloccato |
| `is_unspecified` | bloccato (post-B3-B.8 2026-04-27) | bloccato |
| IPv4 (A) + IPv6 (AAAA) via `getaddrinfo` | si (post-B3-B.8) | si (sempre) |
| Short-circuit "host e` gia` un IP" | no (sempre `getaddrinfo`) | si (`ipaddress.ip_address(host)` first) |
| ENVIRONMENT guard hardened | si (prod/staging ignora opt-in) | n/a (no opt-in) |
| Errore restituito | "URL bloccato: l'host risolve a un IP privato/interno (X)" | "IP loopback bloccato: X" / "IP privato bloccato: X" / "IP link-local bloccato: X (es. AWS IMDS 169.254.*)" / etc. |

Le **policy di blocco sono allineate** post-B3-B.8. Restano 3 differenze concrete:
1. Interfaccia raise vs return-string
2. Short-circuit IP-letterale (solo nel mixin)
3. Granularita` messaggio errore (mixin piu` specifico)

### 1.3 Single-source design proposal

**Obiettivo**: una sola policy di SSRF, due interfacce wrapper, stesso comportamento.

**Architettura proposta** (NON implementata in Phase 0):

```
app/shared/ssrf.py  (nuovo, oppure dentro api_utils.py se preferiamo no-new-files)

  def _check_outbound_url(url: str) -> Optional[str]:
      """Primitive: ritorna None=OK, str=motivo bloccato.
      Legge PLENORA_ALLOW_LOOPBACK_OUTBOUND + URBY_ALLOW_LOOPBACK_OUTBOUND
      con guard ENVIRONMENT hardened. Short-circuit IP-letterale. AAAA via
      getaddrinfo. Errore granulare per categoria."""

  def validate_external_url(url: str) -> None:
      """Wrapper raise. Chiama _check_outbound_url; se ritorna stringa,
      raise ValueError con messaggio.
      Compat: stessa firma di oggi."""
      err = _check_outbound_url(url)
      if err is not None:
          raise ValueError(f"URL bloccato: {err}")

  def check_outbound_url(url: str) -> Optional[str]:
      """Wrapper return-string. Identita` con la primitive."""
      return _check_outbound_url(url)
```

**Decisione loopback opt-in per REST_FILE**: REST_FILE eredita la decisione globale (`PLENORA_ALLOW_LOOPBACK_OUTBOUND`). NON ha opt-in dedicato. Motivazione:
- REST_FILE in produzione NON deve mai parlare con loopback (servizio interno deve essere esposto su rete esplicitamente).
- In dev/test, il flag globale gia` consente di puntare a fake server local.
- Aggiungere un flag dedicato (`PLENORA_REST_FILE_ALLOW_LOOPBACK`) sarebbe scope creep e moltiplicherebbe le superfici di errore di config.

**Migrazione step-by-step** (Phase 1+, NON Phase 0):
1. Creare `_check_outbound_url` in `shared/api_utils.py` (o nuovo `shared/ssrf.py`).
2. Riscrivere `validate_external_url` come wrapper raise sopra la primitive (behavior-equivalent).
3. Sostituire `_validate_outbound_url` del mixin con `from app.shared.api_utils import check_outbound_url`. Lasciare `_validate_outbound_url` come thin shim per non rompere i 4 lock test che fanno `monkeypatch.setattr(svc, "_validate_outbound_url", ...)`.
4. Modernizzare i 4 lock test (carry-over C5) a patchare `app.shared.api_utils.check_outbound_url` direttamente.
5. Rimuovere lo shim quando i test sono modernizzati.

**Test debt**: i 4 lock test in `tests2/arricchisci/unit/test_api_connector_*.py` patchano `_validate_outbound_url` come instance method (`patch.object(svc, "_validate_outbound_url")`). Se la primitive si sposta, i monkeypatch non vedono lo spostamento → modernizzazione richiesta PRIMA del refactor (regola METHOD).

### 1.4 Phase 0 deliberatamente NON modifica nulla

Il design e` documentato qui. Implementazione e` Phase 1+ (in pacchetto C1 SSRF + C5 test modernization).

---

## 2. A2/A3 — Marker safe (proposta diff)

### 2.1 A2 — `# DEPRECATED` su wrapper OAuth legacy

**Target**: `app/shared/api_utils.py:577-595`.

**Stato attuale**: 2 wrapper backward-compatible che delegano a `get_cached_oauth2_token`. La docstring dice "Wrapper backward-compatible" ma non marca esplicitamente "use canonical instead".

**Verifica consumer interni** (grep):

```
get_oauth2_token / get_oauth2_client_credentials_token
```

Da fare (Phase 0.3 batch dedicato) — se i 2 wrapper sono usati da N caller, marker safe; se non usati internamente da nessuno, candidato A piu` aggressivo (rimozione in Phase 1).

**Diff proposto** (zero behavior change):

```python
# app/shared/api_utils.py:577-595

def get_oauth2_token(auth: Dict) -> Optional[str]:
    """Ottiene token OAuth2 password grant (via cache con expires_at).

    .. deprecated::
        Wrapper legacy. Nuovi caller devono usare ``get_cached_oauth2_token``
        passando ``auth["type"] = "oauth2_password"``. Mantenuto per
        backward-compat con caller esterni che importano questo simbolo
        diretto. Nessuna data di rimozione fissata: rimossa quando zero
        caller interni rimasti (audit Phase 1+).
    """
    auth_with_type = dict(auth)
    auth_with_type.setdefault("type", "oauth2_password")
    return get_cached_oauth2_token(auth_with_type)


def get_oauth2_client_credentials_token(auth: Dict) -> Optional[str]:
    """Ottiene token OAuth2 client_credentials grant (via cache).

    .. deprecated::
        Wrapper legacy. Nuovi caller devono usare ``get_cached_oauth2_token``
        passando ``auth["type"] = "oauth2_client_credentials"``. Mantenuto
        per backward-compat. Vedi ``get_oauth2_token`` per nota generale.
    """
    auth_with_type = dict(auth)
    auth_with_type.setdefault("type", "oauth2_client_credentials")
    return get_cached_oauth2_token(auth_with_type)
```

**Behavior change**: zero. Solo docstring.
**Test impact**: zero. Nessun test legge la docstring.

### 2.2 A3 — Warning loud su `URBY_ALLOW_LOOPBACK_OUTBOUND`

**Target**: `app/shared/api_utils.py:118-122` (dentro `validate_external_url`).

**Stato attuale**: l'alias retro-compat e` letto silenziosamente come fallback. Nessun warning.

**Decisione di design**: il warning va emesso **una sola volta per processo**, non per ogni chiamata (sarebbe spam log). Pattern: `_warned_urby_loopback = False` module-level + check.

**Diff proposto** (zero behavior change su SSRF, +1 warning idempotente):

```python
# app/shared/api_utils.py

# Aggiungere a livello modulo, vicino a logger:

_WARNED_URBY_LOOPBACK_ALIAS = False


# Dentro validate_external_url, sostituire le linee 118-122:
        # Canonical PLENORA_*, alias retro-compat URBY_* (deprecato).
        plenora_set = os.environ.get("PLENORA_ALLOW_LOOPBACK_OUTBOUND", "").strip() == "1"
        urby_set = os.environ.get("URBY_ALLOW_LOOPBACK_OUTBOUND", "").strip() == "1"
        allow_loopback = plenora_set or urby_set

        # A3: warning loud one-shot quando si usa l'alias deprecato senza
        # il canonical. Aiuta gli operatori a migrare il .env prima
        # della rimozione futura dell'alias.
        if urby_set and not plenora_set:
            global _WARNED_URBY_LOOPBACK_ALIAS
            if not _WARNED_URBY_LOOPBACK_ALIAS:
                _WARNED_URBY_LOOPBACK_ALIAS = True
                logger.warning(
                    "[DEPRECATED] URBY_ALLOW_LOOPBACK_OUTBOUND e' un alias "
                    "retro-compat. Migrare a PLENORA_ALLOW_LOOPBACK_OUTBOUND. "
                    "L'alias verra` rimosso in una release futura."
                )
```

**Behavior change**: zero su SSRF (decisione `allow_loopback` invariata). +1 log warning emesso una sola volta per processo se l'operatore usa solo l'alias deprecato.
**Test impact**:
- `tests2/security/unit/test_validate_external_url_contract.py` blinda il fallback URBY_* (linee 132-145, "URBY_ALLOW_LOOPBACK_OUTBOUND continua a funzionare in dev/test"). Test resta verde (decision logic immutata).
- Modulo-level state `_WARNED_URBY_LOOPBACK_ALIAS` puo` causare flake tra test: serve `monkeypatch.setattr(api_utils, "_WARNED_URBY_LOOPBACK_ALIAS", False)` o un fixture autouse di reset. **Da considerare prima di committare** — proposta: aggiungere fixture in `tests2/conftest.py` per resetlo, oppure (piu` pulito) usare `logging.WarningLogger` con filter de-dup invece di flag globale.
- Decisione: **uso `functools.lru_cache(maxsize=1)` su una closure** invece di flag globale → niente state da resettare nei test, e` un cache hit. Diff alternativo:

```python
@functools.lru_cache(maxsize=1)
def _warn_urby_loopback_alias_once() -> None:
    logger.warning("[DEPRECATED] URBY_ALLOW_LOOPBACK_OUTBOUND e' un alias retro-compat. ...")

# E poi:
        if urby_set and not plenora_set:
            _warn_urby_loopback_alias_once()
```

Anche il pattern con `lru_cache` ha bisogno di reset nei test (`_warn_urby_loopback_alias_once.cache_clear()`), ma e` un'API standard pytest e meno fragile.

**Da decidere prima del commit A3**:
- Approccio flag vs lru_cache vs filter logging?
- Test fixture autouse di reset? In che file?

Mia raccomandazione: **lru_cache pattern + fixture autouse in `tests2/conftest.py`** che fa `_warn_urby_loopback_alias_once.cache_clear()`. Pulito e standard.

### 2.3 A2/A3 — proposta esecuzione

**Batch unico** (atomico, zero behavior change):
- Edit `app/shared/api_utils.py`: 2 docstring (A2) + 1 helper + 1 condizione warning (A3)
- Eventuale fixture in `tests2/conftest.py` per A3 cache reset
- Test gate: rieseguire `tests2/security/unit/test_validate_external_url_contract.py` (deve restare verde, attesa: 0 regressioni perche` la decision logic e` invariata)

**Stima**: ~30 min implementation + ~5 min test gate.

**Stop pre-implementation**: in attesa di tua decisione su:
1. Approccio A3 (flag vs lru_cache vs filter logging)
2. Fixture reset in `tests2/conftest.py` ammessa? (regola Phase 0: "nessun test logic" — fixture e` test infra, valutare)
3. Commit unico per A2+A3 o separati?

---

## 3. Secret / env audit dettagliato

### 3.1 Sorgenti analizzate

| Sorgente | Stato |
|---|---|
| `backend/.env.example` (repo) | 304 righe, 13KB, ~50 env documentate |
| `backend/.env` (repo locale) | 9 env settate, ENVIRONMENT=development, **DATABASE_URL produzione** (smell F4) |
| `.env` server (probe 0.2 §3.H) | 10 env settate (nomi only) |
| `.env.example` server (probe 0.2 §3.H) | 6160B, **outdated** vs repo 13170B (~7KB delta) |
| `crypto.py:_get_raw_key` | env canonica `PLENORA_ENCRYPTION_KEY`, alias legacy tombstoned 2026-04-23 |
| `auth.py:_jwt_env_value` | env canonica `JWT_SECRET_KEY`, fail-fast in prod/staging, no fallback |
| `config.py:_DOTENV_DISABLED_ENVS` | `.env` ignorato in `production`/`prod`/`staging`/`stage` |
| `runtime-prereqs.md` (repo) | tabelle env documentate |

### 3.2 Confronto `.env` server vs `.env.example` repo

**Env settate sul server (10)**:
```
CELERY_BROKER_URL              ← canonical
CELERY_RESULT_BACKEND          ← canonical
CORS_ORIGINS                   ← canonical
DATABASE_URL                   ← canonical
ENABLE_ASYNC_ENRICHMENT        ← canonical (default true)
ENRICHMENT_POOL_SIZE           ← canonical (default 50)
ENVIRONMENT                    ← canonical (valore non visto, presumibilmente "production")
JWT_SECRET_KEY                 ← canonical
PLENORA_ENCRYPTION_KEY         ← canonical
URBY_ENCRYPTION_KEY            ← TOMBSTONED 2026-04-23 (runtime ignora)
```

**Anomalie identificate**:

| # | Categoria | Env | Impatto |
|---|---|---|---|
| E1 | OBSOLETA | `URBY_ENCRYPTION_KEY` ancora nel `.env` server | Runtime non la legge piu` (lockato dal contract test). Smell ad alto valore: se un operatore in futuro cambia *solo* l'alias per errore, il backend NON puo` decifrare blob esistenti. **Rimuovere in Phase 1 cutover §5**. |
| E2 | LEGACY ALIAS RUNTIME-ATTIVO | `URBY_ALLOW_LOOPBACK_OUTBOUND` (codice linea 121) | Non sul server (correttamente OFF in prod). Marker A3 in §2.2. |
| E3 | LEGACY ALIAS RUNTIME-ATTIVO | `BACKUP_S3_BUCKET` (`runtime-prereqs.md:64` + `.env.example:198`) | Alias di `BACKUP_BUCKET` letto come fallback. Non sul server (arriva da SSM). Lasciare per ora, no urgenza. |
| E4 | `.env.example` SERVER OUTDATED | server 6KB vs repo 13KB | Mancano ~7KB di env documentate post-Phase-5. Risolto automaticamente dal cutover Phase 1 (re-clone porta `.env.example` aggiornato). |
| E5 | DUPLICATE | nessuna | server pulito |
| E6 | MANCANTE BUT DEFAULT-SAFE | ~40 env opzionali non settate sul server | Default safe nel codice. Esplicitarle migliora chiarezza ma non e` urgente. |

### 3.3 Env CRITICHE da fissare esplicitamente (raccomandazione Phase 1 cutover)

Nel cutover Phase 1, **dopo** il re-clone e **prima** del restart, raccomando di settare esplicitamente:

| Env | Valore proposto | Motivo |
|---|---|---|
| `ENVIRONMENT` | `production` | gia` settata? VERIFICARE valore reale (probe 0.2 ha visto solo il nome). Se non e` "production", crypto/JWT fail-fast NON sono attivi e cio` e` un security risk. |
| `STORAGE_POLICY` | `db` (default) o `hybrid` | scelta esplicita > implicita |
| `BACKUP_SCHEMA` | `plenora` | match del default ma esplicito |
| `JWT_BLACKLIST_REDIS_TIMEOUT_SEC` | `1` | match default, esplicito |
| `JWT_BLACKLIST_STALE_THRESHOLD_SEC` | `1800` | match default, esplicito |
| `LOG_LEVEL_CONSOLE` | `INFO` | match default |
| `LOG_LEVEL_DB` | `WARNING` | match default, riduce volume in DB |
| `LOG_RETENTION_DAYS` | `30` | match default |
| `UVICORN_WORKERS` | `2` | match config systemd unit, abilita stima pool log |
| `CELERY_CONCURRENCY` | `4` | idem |

**Da NON settare** sul server:
- `PLENORA_ALLOW_LOOPBACK_OUTBOUND` — DEVE rimanere assente in prod
- `URBY_ALLOW_LOOPBACK_OUTBOUND` — alias deprecato, mai settare
- `URBY_ENCRYPTION_KEY` — RIMUOVERE da .env server
- Tutte le `URBY_TEST_*` / `URBY_RUN_*` — solo test

### 3.4 Verifica ENVIRONMENT (probe extra read-only — eseguita 2026-04-27)

**Comando eseguito**:
```bash
ssh ubuntu@15.160.219.93 'grep -E "^(ENVIRONMENT|ENV)=" /home/ubuntu/plenora-etl/backend/.env'
```

**Output**: `ENVIRONMENT=production`

**Implicazioni — fail-fast tutti ATTIVI**:
- `crypto.py:_get_raw_key`: `is_prod = True` → raise `RuntimeError` su missing key o dev-default key copiata
- `auth.py`: raise `RuntimeError` su missing `JWT_SECRET_KEY`
- `validate_external_url`: hardened guard → `allow_loopback = False` indipendentemente dai flag opt-in (anche se l'alias `URBY_ALLOW_LOOPBACK_OUTBOUND` finisse per errore nel `.env` prod, sarebbe inerte)
- `config.py:_should_load_dotenv()`: ritorna `False` in `production` → python-dotenv NON carica `.env`. Le env arrivano da systemd `EnvironmentFile=` direttamente (design corretto, anti-leak da .env stranded)

**Conseguenza per E1**: rimossa l'urgenza. `URBY_ENCRYPTION_KEY` nel `.env` server resta uno smell ma con priorita` BASSA — il runtime non la legge mai in produzione. Lo cleanup resta candidato per la finestra Phase 1 cutover per igiene.

**Verifica `/proc/<pid>/environ`**: tentata ma vuota (sudo non ha potuto leggere `/proc/<pid>/environ` del processo uvicorn). Non e` un blocker: il `.env` e` autoritativo per quanto carica `EnvironmentFile=` di systemd.

### 3.5 Crypto chain — confermata pulita

- Env canonica `PLENORA_ENCRYPTION_KEY` ✓
- Alias `URBY_ENCRYPTION_KEY` / `CONN_ENCRYPT_KEY` **tombstoned 2026-04-23** ✓ (runtime non li legge)
- Test contract `test_crypto_key_sourcing_contract.py` blinda il tombstone ✓
- `_DEV_DEFAULT_KEY = "urby-etl-default-key-change-in-production"` rimasta come stringa nel codice — **B intenzionale** (cambiarla rompe blob cifrati locali storici).
- Detection del default-key in prod: `crypto.py:153-159` raise se `val == _DEV_DEFAULT_KEY` e env hardened.
- Detection chiave troppo corta: `crypto.py:162-167` raise se `len(val) < 16` in qualsiasi env.

### 3.6 JWT chain — confermata pulita

- Env canonica `JWT_SECRET_KEY` ✓
- Fail-fast `RuntimeError` se manca in `production`/`prod`/`staging` (linea 138-148) ✓
- Fallback dev: random per processo (token invalidi al riavvio) ✓
- NON c'e` fallback hardcoded ✓
- Login rate limit: `LOGIN_MAX_ATTEMPTS=10`, `LOGIN_WINDOW_SECONDS=60`, `LOGIN_LOCKOUT_SECONDS=300` (default) ✓
- Blacklist Redis con fail-loud su write `/auth/logout` + fail-open su read `get_current_user` (vedi `runtime-prereqs.md:124-138`)

### 3.7 Boundary auth/RBAC — confermati

- `JWTMiddleware` su tutte le route (`auth_middleware.py`) ✓
- 5 ruoli: `esecutore`, `viewer`, `user`, `admin`, `superadmin` ✓
- RBAC dependencies: `require_user_or_above`, `require_not_esecutore`, `require_admin` ✓
- Ownership check su risorse: `require_*_owner`, `_check_owner_access` ✓
- Niente lettura segreti in chiaro: tutti via `shared/crypto.py` ✓

---

## 4. Decisioni esplicite Phase 0.3

1. **SSRF design**: documento §1, **NON implementare** in Phase 0. Implementazione = Phase 1 (cluster C1 SSRF + C5 test modernization, multi-fase).
2. **A2 marker** (docstring DEPRECATED su 2 wrapper OAuth): zero behavior change → propongo commit dedicato.
3. **A3 marker** (warning loud su `URBY_ALLOW_LOOPBACK_OUTBOUND`): zero behavior change su decision logic, +1 log warning idempotente. Decisione di design preferita: `lru_cache` pattern + fixture reset in `conftest.py`. Da approvare prima del commit.
4. **Env audit**:
   - **E1 da risolvere in Phase 1 cutover**: rimuovere `URBY_ENCRYPTION_KEY` dal `.env` server.
   - **Probe extra `ENVIRONMENT` value**: 1 comando read-only, decidere se eseguirlo o assumere "production".
   - **§3.3 raccomandazione 10 env critiche**: settare in finestra Phase 1 cutover, NON ora.
5. **Phase 0.3 NON modifica codice** finche` A2/A3 non approvati.

---

## 5. Cosa Phase 0.3 NON ha fatto

- Nessun codice modificato ✓
- Nessun test modificato ✓
- Nessun deploy ✓
- Nessun SSH (sufficiente quanto raccolto in 0.2) ✓
- Nessun secret value letto / stampato ✓

**Phase 0.3 chiusa con report.**

In attesa di:
1. Approvazione A2 commit (docstring DEPRECATED, zero rischio)
2. Approvazione A3 design choice (flag vs lru_cache vs filter logging) + fixture conftest
3. Decisione su probe extra `ENVIRONMENT` value (1 comando read-only)
4. Conferma roadmap Phase 0.4 (test coupling audit + evidence collection per E1-E5)
