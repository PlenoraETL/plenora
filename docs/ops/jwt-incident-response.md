# Runbook — incident response JWT

Procedure operative per eventi di sicurezza sui token JWT emessi da PLENORA-ETL.

## Scenario A — token utente compromesso (singolo utente)

Esempio: un utente segnala credenziali rubate; un logout server-side è sufficiente.

1. L'utente (o un admin per conto suo) esegue `POST /api/auth/logout` con il suo token corrente.
   - Il `jti` viene aggiunto alla blacklist Redis con TTL pari alla scadenza naturale.
   - Il token non funziona più da quel momento.
2. L'utente cambia la password via `POST /api/auth/change-password` (o un admin via `POST /api/auth/users/{id}/reset-password`).
3. L'utente si riautentica: il nuovo token ha un `jti` diverso — indipendente dalla blacklist.

Verifica:

```bash
# Il token vecchio NON deve più funzionare
curl -H "Authorization: Bearer <vecchio_token>" https://lab.plenora.it/api/auth/me
# → 401 "Token revocato"
```

Se `/auth/logout` risponde 503 → Redis down (vedi "Blacklist inoperante" più sotto).

---

## Scenario B — token compromesso in finestra legacy pre-jti

Dopo il rollout del claim `jti` (commit `f183234`, 2026-04-18), tutti i token emessi sono revocabili singolarmente. Ma i token emessi **prima** del rollout non hanno `jti` → non possono essere messi in blacklist.

Se scopriamo che uno di questi token è stato leakato e il suo `exp` è ancora nel futuro, la sola via è **invalidare la `JWT_SECRET_KEY`**: tutti i token firmati con la vecchia chiave falliscono la verifica, indipendentemente da contenuto/scadenza.

### Blast radius

La rotazione invalida **tutti** i token emessi con la vecchia chiave, inclusi quelli legittimi. Conseguenze:

- Tutti gli utenti attivi vengono sloggati alla prossima richiesta (401).
- Gli automatismi che usano JWT a lungo termine devono riautenticarsi.
- Nessun impatto su dati: solo sessioni.

Accettabile come misura di emergenza; non usare per routine.

### Procedura

1. Generare una chiave nuova ad alta entropia:
   ```bash
   NEW_KEY=$(python -c "import secrets; print(secrets.token_urlsafe(48))")
   ```
2. Aggiornare il secret manager / `.env` sul server:
   ```bash
   ssh ubuntu@lab.plenora.it 'sudo sed -i "s|^JWT_SECRET_KEY=.*|JWT_SECRET_KEY='"$NEW_KEY"'|" /home/ubuntu/plenora-etl/backend/.env'
   ```
3. Riavviare il backend (uvicorn rileggerà la chiave all'import):
   ```bash
   ssh ubuntu@lab.plenora.it 'sudo systemctl restart plenora-backend'
   ```
4. Verifica: fai una qualsiasi GET autenticata con un token vecchio → 401 "Token invalido o scaduto".
5. Comunica agli utenti di effettuare di nuovo il login.

### Post-mortem

- Registra l'incidente: data, motivazione, chi ha ruotato, ora di ripristino.
- Se possibile identifica la root cause del leak (log access, phishing, credential reuse).
- Se il leak proveniva da un componente con credenziali hardcoded → fix del codice in parallelo, non solo della chiave.

---

## Scenario C — blacklist inoperante (Redis down/degradato)

Il dashboard `/admin/system-status` espone la card "JWT Blacklist". Stati:

- `ok`: Redis risponde, blacklist operativa.
- `warn`: Redis risponde a `EXISTS` ma nessuna operazione reale (write/read) è riuscita negli ultimi 30 min — configurabile via `JWT_BLACKLIST_STALE_THRESHOLD_SEC`. Sintomatico di ACL blocking, disk-full Redis, instance stuck.
- `error`: Redis raggiungibile ma `EXISTS` ha sollevato — autorizzazione o stato interno Redis.
- `unknown`: client non inizializzato, o `redis-py` non installato.

### Impatto pratico

- **Read (controllo blacklist al momento del request)**: fail-open → token validi per `exp` e firma passano, anche se `jti` dovrebbe essere revocato. La finestra di esposizione massima di un token compromesso è il suo `exp` residuo (default 8h).
- **Write (`/auth/logout`)**: fail-loud → 503 al client. L'utente non può revocare il suo token finché Redis non torna su. Workaround: rotazione `JWT_SECRET_KEY` (scenario B), molto invasiva.

### Diagnosi

```bash
# 1. Il server Redis risponde?
ssh ubuntu@lab.plenora.it 'redis-cli -u $(grep CELERY_BROKER_URL /home/ubuntu/plenora-etl/backend/.env | cut -d= -f2) ping'
# → PONG

# 2. Lettura di una chiave sentinel
ssh ubuntu@lab.plenora.it 'redis-cli exists plenora:jwt_blacklist:healthcheck'
# → 0 (normale, la chiave non esiste; non è un errore)

# 3. Scrittura di test con TTL breve
ssh ubuntu@lab.plenora.it 'redis-cli setex plenora:jwt_blacklist:_probe 10 "1"'
# → OK

# 4. Uptime e memoria (se Redis è stuck ma up)
ssh ubuntu@lab.plenora.it 'redis-cli info memory | grep -E "used_memory_human|maxmemory_human"'
```

### Remediation

| Sintomo | Azione |
|---|---|
| Redis server down | `sudo systemctl restart redis-server` (o container equivalente) |
| Memory full (OOM) | Svuotare chiavi celery non più usate (`redis-cli flushdb` con cautela) |
| ACL/auth rigettato | Verificare `CELERY_BROKER_URL` contenga username/password corretti |
| Timeout di rete | Alzare `JWT_BLACKLIST_REDIS_TIMEOUT_SEC` (default 1s) nell'`.env` del backend |

Dopo il restart, verifica che i contatori `write_ok` / `read_ok` della card blacklist ricomincino a crescere entro 1-2 min di normale traffico.

---

## Scenario D — intera fleet di token da revocare (es. compromesso server)

Se il server è stato compromesso e dobbiamo presumere che **tutti** i token attivi siano a rischio:

1. Rotazione `JWT_SECRET_KEY` (scenario B) — questa è l'azione primaria.
2. In parallelo, ruotare anche altre credenziali che potrebbero essere state esposte: DB, AWS IAM, Redis, connection secrets applicativi (tabella `secrets_blob`).
3. Svuotare completamente la blacklist (opzionale, cleanup): `redis-cli --scan --pattern 'plenora:jwt_blacklist:*' | xargs redis-cli del`. Dopo la rotazione della chiave, le entry diventano inutili comunque (i nuovi jti hanno chiave diversa).

---

## Link

- Codice blacklist: [../../backend/app/shared/token_blacklist.py](../../backend/app/shared/token_blacklist.py)
- Logout endpoint: [../../backend/app/core/auth/auth.py](../../backend/app/core/auth/auth.py) (`/auth/logout`)
- Test unit: [../../backend/tests2/security/unit/test_jwt_logout_blacklist.py](../../backend/tests2/security/unit/test_jwt_logout_blacklist.py)
- Test integration: [../../backend/tests2/integration/test_jwt_logout_blacklist_integration.py](../../backend/tests2/integration/test_jwt_logout_blacklist_integration.py)
- Runtime prereqs: [runtime-prereqs.md](runtime-prereqs.md)
