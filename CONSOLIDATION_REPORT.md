# CONSOLIDATION — Report Fase 0

**Status**: in_progress
**Last updated**: 2026-04-19

## Fase A — Inventario
- [x] inventory_report.md committato (in arrivo: vedi `backend/docs/architecture/inventory_report.md`)
- Numeri (pre-checkpoint):
  - **8** famiglie di path duplicati
  - **~152** siti grezzi di fallback (121 `except: pass` + 31 `except: return default`), **~10** cluster critici
  - **6** feature flag veri + **3** alias retro-compat + **3** config-not-flag
  - **24** moduli mappati per policy errore + **6** coppie cross-modulo anomale, **1** silent puro

## Fase B — Consolidation execute
- (non iniziata — bloccata da checkpoint utente su casi dubbi elencati in inventory_report)

## Fase C — Invariants
- [ ] invariants.md pubblicato

## Fase D — Brief Fase 1
- [ ] 4 brief aggiornati

## Test
- Backend: non eseguito (fase read-only)
- Frontend: non eseguito (fase read-only)
- Smoke E2E manuale: non eseguito

## Bug trovati fuori scope (NON fissati)
- Docstrings stale post-migrazione PG:
  - `backend/app/services/connection_service.py:1-11` — parla di "SQLite tramite app.db.db_service"
  - `backend/app/core/features/features_service.py:1-13` — descrive filesystem JSON storage
  - `backend/app/core/features/features_service_db.py:1-7` — descrive "patch" verso SQLite
- `backend/app/core/features/features_service.py:1076-1078` — claim "per evitare dipendenza circolare" non verificato
- `backend/app/core/auth/auth.py:989-991` — silent swallow su `delete_group_cascade` (annotato in Sezione 4)
- Eventuali bug del nodo `result` colonne (cross-modulo estrai/manipola/arricchisci) — gia' tracciato in memoria `project_arricchisci_result_columns_bug.md`, fuori scope consolidamento

## Deps nuove richieste
- Nessuna

## Proposte Fase 1
- (da definire a fine Fase 0)
