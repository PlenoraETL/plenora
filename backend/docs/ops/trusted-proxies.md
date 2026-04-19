# `TRUSTED_PROXIES` — configurazione reverse proxy

Controlla da quali **peer TCP** il backend accetta l'header
`X-Forwarded-For` (XFF) come IP client reale. Cruciale per:

- **Rate-limit login** (reperto #27): il bucket deve essere per-IP client
  vero, non per-IP del proxy
- Audit log / tracciamento IP
- Qualsiasi logica basata su "da dove arriva la richiesta"

## Sintesi del flusso

```
client                nginx/ALB/ingress              backend (uvicorn)
  10.20.30.40  ────►  peer:10.20.30.40  ────►  peer: <IP del proxy>
                      XFF: -                    XFF: 10.20.30.40
```

Il backend legge `request.client.host` (TCP peer). Se quel peer è in
`TRUSTED_PROXIES` → prende il **primo IP** di `X-Forwarded-For`.
Altrimenti usa il peer (fail-safe: un attaccante diretto non può
spoofare).

## Formato env var

CSV di IP esatti e/o CIDR. Whitespace ignorato. Entry malformate sono
scartate silenziosamente al boot (no crash).

```bash
# IP esatti
TRUSTED_PROXIES=127.0.0.1,::1

# CIDR (K8s pod network tipico)
TRUSTED_PROXIES=10.0.0.0/8

# Mix
TRUSTED_PROXIES=127.0.0.1,::1,10.0.0.0/8,172.16.0.0/12
```

**Default**: `127.0.0.1,::1` (single-host nginx → uvicorn sullo stesso
box, come `lab.plenora.it` oggi).

## Ricette per deploy comuni

### Single-host nginx (default, come oggi)

```
[client] ──HTTPS──► [nginx :443 same box] ──http 5004──► [uvicorn]
```

- Il peer TCP visto da uvicorn è `127.0.0.1`
- **Env**: `TRUSTED_PROXIES` può essere omessa (il default copre già).
- **Verifica**:
  ```bash
  journalctl -u plenora-backend | grep "client_ip" # should show real IP dopo fix
  ```

### Kubernetes + Ingress (nginx-ingress, Traefik, ecc.)

```
[client] → [ingress pod (10.0.0.5)] → [backend pod (10.0.0.42 :5004)]
```

- Il peer visto dal backend è l'IP del pod ingress nel pod CIDR
- **Env**: allowlist del pod CIDR completo (non solo dell'ingress — i
  pod ingress possono essere multipli e con IP dinamici)
  ```yaml
  env:
    - name: TRUSTED_PROXIES
      value: "127.0.0.1,::1,10.0.0.0/16"  # adatta al tuo pod CIDR
  ```
- In alternativa, configura l'ingress con `use-forwarded-headers: "true"`
  + `use-proxy-protocol: "false"` così XFF è sempre propagato correttamente.

### AWS ALB / ELB

```
[client] → [ALB] → [target EC2 / ECS / EKS pod]
```

- L'ALB scrive XFF e termina TLS. Il peer TCP visto dal backend è
  l'IP privato dell'ALB node (dentro il tuo VPC).
- **Env**: allowlist del CIDR VPC o della subnet ALB
  ```
  TRUSTED_PROXIES=127.0.0.1,::1,10.1.0.0/16
  ```
- **NON** allowlist l'IP pubblico dell'ALB — il backend non lo vede mai
  come peer.

### CDN davanti (Cloudflare, CloudFront)

```
[client] → [CDN edge] → [origin (ALB/nginx)] → [backend]
```

- Due hop. XFF generico potrebbe avere il formato:
  `X-Forwarded-For: <client>, <cdn_edge>, <alb_private>`
- Il primo IP (`<client>`) è quello vero — il parser lo estrae
  automaticamente.
- Configura il CDN per scrivere `CF-Connecting-IP` (Cloudflare) o
  `X-Forwarded-For` e assicurati che l'origin propaghi XFF fedele al
  backend.
- `TRUSTED_PROXIES` deve contenere il CIDR dell'origin (ALB/nginx),
  non gli IP CDN pubblici.

## Sicurezza — anti-spoofing

Il check `_is_trusted_peer(peer)` esiste proprio per questo: se un
attaccante si connette **direttamente** al backend bypassando il proxy
(porta esposta per errore, SSRF da container interno, ecc.), il suo
XFF arbitrario viene ignorato e la rate-limit usa il suo IP reale.

**Anti-pattern**: non mettere mai IP pubblici in `TRUSTED_PROXIES`. Se
un IP pubblico arriva al backend come peer, significa che il traffico
sta bypassando il proxy → l'XFF dovrebbe essere ignorato.

## Validazione IP estratto da XFF

Il parser valida il primo IP di XFF via `ipaddress.ip_address()`.
Rifiutati e fallback al peer:
- Stringhe arbitrarie (`"foo"`, `"abc.def.ghi.jkl"`)
- Injection attempts (CR/LF, header chaining)
- IP con formato invalido (`"123.456.789.0"`)
- Stringhe vuote o solo whitespace

IPv6 viene normalizzato alla forma canonica (`2001:0db8::1` →
`2001:db8::1`).

## Troubleshooting

| Sintomo | Probabile causa | Fix |
|---|---|---|
| Tutti i login vengono rate-limitati da uno stesso IP (apparentemente `127.0.0.1` o IP ingress) | Peer del proxy non in `TRUSTED_PROXIES` | Aggiungi il suo IP/CIDR alla env var |
| Rate-limit non scatta mai | XFF non propagato dal proxy, oppure il parser lo rifiuta | Verifica `proxy_set_header X-Forwarded-For` su nginx; guarda i log backend per validazione |
| Rate-limit scatta per IP diversi ma legittimi (es. tutti uffici dietro NAT) | Normale — NAT espone un singolo IP esterno. Il rate-limit è per IP vero, non per utente. Se serve, aumenta `LOGIN_MAX_ATTEMPTS` | — |

## Verifica post-deploy

```bash
# 1. Il backend vede peer giusto?
# Fai request da client esterno e controlla il log
ssh ubuntu@host "sudo journalctl -u plenora-backend --since '1 minute ago' | grep 'client_ip'"

# 2. Simula rate-limit da client esterno (bash/curl):
for i in $(seq 1 12); do
  curl -s -o /dev/null -w "%{http_code}\n" \
    -X POST https://lab.plenora.it/api/auth/login \
    -H 'Content-Type: application/json' \
    -d '{"username":"test","password":"wrong"}'
done
# Prima 10 → 401, poi → 429
```

## Test coverage

- **Unit** (`tests2/security/unit/test_client_ip_trusted_proxy.py`):
  parser, allowlist, CIDR, validazione IP (18 test)
- **Endpoint** (`tests2/security/unit/test_login_rate_limit_per_ip.py`):
  rate-limit isolato per-IP via TestClient (3 test)
- **Integration** (`tests2/integration/test_login_rate_limit_integration.py`):
  stack completo con uvicorn live + http reale (2 test)

## Riferimenti

- Codice: `app/shared/request_utils.py::get_client_ip`
- Consumer: `app/core/auth/auth.py` login endpoint (l. 600-650 circa)
- Reperto originale: #27 "Rate limiting X-Forwarded-For (trusted proxy)"
