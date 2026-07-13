# Ticketing system — overview operativo

Sistema unificato per `tech_debt` (debito tecnico visibile agli admin) e `user_support` (segnalazioni utenti). Codebase: `backend/app/core/tickets/`, schema: `plenora.tickets|ticket_comments|ticket_attachments`.

## Schema DB

| Tabella | Colonne principali | Indici |
|---|---|---|
| `tickets` | id, kind, title, description, severity, status, category, created_by, assignee, group_id, created_at, updated_at, resolved_at | `(group_id, status)` · `(assignee, status)` WHERE NOT NULL · `(created_by)` · `(kind, status)` · `(updated_at DESC)` |
| `ticket_comments` | id, ticket_id (FK cascade), author_id, body, created_at | `(ticket_id, created_at)` |
| `ticket_attachments` | id, ticket_id (FK cascade), file_source_id (FK cascade), uploaded_by, uploaded_at | `(ticket_id)` |

`severity` e `status` vincolati a livello DB (CHECK). `category` libera — enum applicativo in `TICKET_CATEGORIES` (vedi sotto).

Migration: `migrations/2026-04-18_add_tickets.sql`, applicata automaticamente al boot via `migrations_runner.apply_pending_migrations`.

## RBAC

| Ruolo | Crea | Vede | Modifica privileged fields (severity/assignee/category) | Status change |
|---|---|---|---|---|
| esecutore | ❌ | ❌ | ❌ | ❌ |
| viewer / user | solo `user_support` nel proprio group | solo i propri | ❌ | solo riapre i propri (resolved/closed → reopened) |
| admin | entrambi i kind, nel proprio group | tutti del group | ✅ nel group | ✅ nel group |
| superadmin | entrambi i kind, qualsiasi group (via `target_group_id`) | cross-group | ✅ | ✅ |

Il backend è fonte di verità: il frontend nasconde la UI ai non-autorizzati ma il 403 parte dal router (`app/core/tickets/router.py` + service).

## State machine

```
             ┌──────→ in_progress ─→ waiting_user ─→ resolved ─→ closed
             │             ↑                ↓           ↓          │
  open ──────┤             └────── riapri ←─┘           │          │
             │                                           reopened ←┘
             └─────────────────────────────────────────→ resolved
```

Transizioni enforcement:
- **Admin/superadmin**: qualsiasi transizione tra gli stati validi (CHECK DB).
- **Creator utente**: unica transizione permessa è `resolved|closed → reopened` sui propri ticket.

Nessun enforcement DB-level sulle transizioni: il CHECK vincola solo l'insieme di valori, non il grafo. La policy del grafo vive in `service.update_ticket`.

## Categorie (enum applicativo, estensibile senza migration)

Whitelist in `app/core/tickets/service.py::TICKET_CATEGORIES`:

```python
TICKET_CATEGORIES = (
    "auth", "pipeline", "ui", "performance",
    "data_quality", "deploy", "other",
)
```

### Aggiungere una nuova categoria

1. Aggiungila alla tupla `TICKET_CATEGORIES`.
2. Aggiungi la label IT in `_category_display()` (stessa funzione).
3. **Nessuna migration DB necessaria** (campo `category` è TEXT libero).
4. Al prossimo refresh del frontend (`GET /tickets/meta` è chiamato dalla pagina), la nuova categoria appare nei dropdown.

**Non rimuovere mai una categoria esistente**: ticket storici la userebbero come `category='xxx'` e il dropdown non mostrerebbe il valore. Se una categoria va deprecata, lasciala nella whitelist e aggiungi un commento "(deprecato, non selezionabile)" nella label.

## Allegati

Allowlist hardcoded in `app/core/tickets/router.py::_ALLOWED_EXTENSIONS`:

| Ext | MIME atteso | Use case |
|---|---|---|
| png, jpg, jpeg, gif, webp | image/* | screenshot |
| log, txt | — | log applicativi / output errori |

Size cap: **10 MB** (`_MAX_ATTACHMENT_SIZE_BYTES`). Oltre: HTTP 413.

Storage: riusa `file_sources`; il catalogo resta in PostgreSQL e il payload dell'allegato viene sempre salvato su object storage S3-compatible.

Cancellazione: `DELETE /tickets/{id}/attachments/{aid}` rimuove il link + best-effort cleanup del file_source sottostante. Se il cleanup S3 fallisce, il file resta orfano (loggato con warning, lifecycle rule S3 lo raccoglierà).

## API

### Endpoints

```
GET    /api/tickets/meta                     # enum per dropdown FE
GET    /api/tickets/counts                   # badge sidebar + card dashboard
POST   /api/tickets                          # crea (RBAC sui kind)
GET    /api/tickets                          # lista filtrata (RBAC sullo scope)
GET    /api/tickets/{id}                     # dettaglio + commenti + allegati
PATCH  /api/tickets/{id}                     # update parziale
POST   /api/tickets/{id}/comments            # aggiungi commento
POST   /api/tickets/{id}/attachments         # upload multipart (mime allowlist)
DELETE /api/tickets/{id}/attachments/{aid}   # rimuovi allegato
```

### Filtri lista

`GET /api/tickets?kind=X&status=a,b&severity=x,y&assignee=uid&mine=true&limit=N`

- `status`, `severity` accettano lista CSV
- `mine=true`: per admin restringe a ticket assegnati a me; per user è implicito (created_by=me)

### Counts

`GET /api/tickets/counts` — campo `by_severity` con breakdown `{low, medium, high, critical}`. Usato da:
- Sidebar badge (totale `active_*`, refresh ogni 60s)
- Card dashboard admin (breakdown + soglie)

### Esempi curl

```bash
TOKEN="eyJhbGci..."
API="https://lab.plenora.it/api"

# Apri un ticket supporto
curl -X POST "$API/tickets" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"kind":"user_support","title":"Login SSO non funziona","severity":"high","category":"auth"}'

# Lista ticket attivi per severity alta
curl "$API/tickets?status=open,in_progress&severity=high,critical" \
  -H "Authorization: Bearer $TOKEN"

# Assegna + cambia stato
curl -X PATCH "$API/tickets/abc-123" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"status":"in_progress","assignee":"user-admin-01"}'

# Aggiungi commento
curl -X POST "$API/tickets/abc-123/comments" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"body":"Sto indagando sul provider SSO."}'

# Carica screenshot
curl -X POST "$API/tickets/abc-123/attachments" \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@screenshot.png"
```

## Notifiche (hook email-ready)

`app/core/tickets/service.py::TicketNotifier` è una classe base con metodi no-op:
- `on_ticket_created(ticket, actor)`
- `on_status_changed(ticket, old, new, actor)`
- `on_comment_added(ticket, comment, actor)`
- `on_assignee_changed(ticket, old, new, actor)`

Nota aggiornata: `TicketNotifier` e' un protocollo strutturale, non una
base class da subclassare. Il default registrato e' `NoopTicketNotifier`.

Ogni hook è **best-effort**: `_fire_notification` wrappa in try/except, le eccezioni sono loggate con warning ma non interrompono la persistenza del ticket. Policy: mai far fallire una write DB per una notifica persa.

### Collegare email quando serve

```python
# app/core/tickets/email_notifier.py
class EmailTicketNotifier:
    def on_ticket_created(self, ticket, actor):
        # usa il tuo mail client (smtplib, SES, ecc.)
        ...
    def on_status_changed(self, ticket, old_status, new_status, actor):
        if new_status in ("resolved", "closed"):
            # notifica al creator
            ...

# app/main.py (startup)
from app.core.tickets.service import set_notifier
from app.core.tickets.email_notifier import EmailTicketNotifier
if os.getenv("TICKET_NOTIFY_EMAIL", "false").lower() == "true":
    set_notifier(EmailTicketNotifier())
```

Alternative: Slack via webhook, Teams, PagerDuty per `severity=critical`. Stessa classe, logica diversa.

## Dashboard / Observability

Card "Ticket" in `/admin/system-status` (superadmin):
- Count attivi totali (cross-group)
- Breakdown per severity
- Soglie stato:
  - `ok`: nessun critical + high ≤ 5
  - `warn`: 1-2 critical OR high > 5
  - `error`: ≥ 3 critical

Sidebar: badge count sulla voce "Ticket", scope RBAC-aware (refresh 60s, fail-silent).

## Cancellazione: soft vs hard

`DELETE /api/tickets/{id}` esegue **soft delete** di default (admin del gruppo / superadmin):
- Setta `deleted_at = NOW()`, ticket sparisce dalle query CRUD ma resta in DB
- Commenti + allegati preservati per audit/recovery
- Recovery: aggiornamento manuale via DB (`UPDATE plenora.tickets SET deleted_at = NULL WHERE id = '...'`)

`DELETE /api/tickets/{id}?hard=true` (**solo superadmin**):
- Hard purge via FK cascade (commenti + allegati cancellati)
- Cleanup blob file_source best-effort (errori loggati, lifecycle S3 raccoglie orfani)
- Riservato a casi GDPR / pulizia massiva

## Retention

Nessun cleanup automatico dei soft-deleted. Se emerge il bisogno (es. normativa GDPR su ticket vecchi con dati personali), si aggiunge un cleanup task Dramatiq analogo a `cleanup_intermediate_runs`:

```python
# scheduled task (proposta futura)
DELETE FROM tickets
WHERE status = 'closed'
  AND updated_at < NOW() - INTERVAL '2 years';
-- comments/attachments cascade-deleted via FK
```

Per ora il volume atteso (~centinaia/anno) non giustifica un cleanup automatico.

## Troubleshooting

| Sintomo | Causa probabile | Fix |
|---|---|---|
| Utente non vede menu "Ticket" | Ruolo = `esecutore` (by design, kiosk-only) | Cambiare ruolo via Admin Panel |
| 403 su POST con kind=tech_debt | Utente non è admin/superadmin | Usare `user_support` o promuovere il ruolo |
| 413 su upload allegato | File > 10MB | Ridurre; non c'è override (design deliberato) |
| 400 "Estensione non consentita" | Mime non in allowlist (.pdf, .docx, ...) | Allegare in formato supportato o convertire in .log/.txt |
| Badge sidebar sparito | Backend `/tickets/counts` 500 o timeout | Check log backend; badge è fail-silent |
| Dropdown categorie non ha valore X | X non in `TICKET_CATEGORIES` | Aggiungerla alla whitelist (vedi sezione Categorie) |
| Counts dashboard sempre 0 | Migration non applicata | `curl /ready` — verifica `migrations.ok=true` |

## Link

- Codice: [`app/core/tickets/router.py`](../../backend/app/core/tickets/router.py), [`service.py`](../../backend/app/core/tickets/service.py)
- DB layer: [`app/core/tickets/db.py`](../../backend/app/core/tickets/db.py)
- Ticket errors: [`app/core/tickets/errors.py`](../../backend/app/core/tickets/errors.py)
- Migration: [`migrations/2026-04-18_add_tickets.sql`](../../backend/migrations/2026-04-18_add_tickets.sql)
- Test backend: [`tests2/tickets/test_tickets_rbac_contract.py`](../../backend/tests2/tickets/test_tickets_rbac_contract.py)
- Test frontend: `frontend/tests/pages/TicketsList.contract.test.jsx`
- Pagine UI: `/tickets` (lista), `/tickets/{id}` (dettaglio)
