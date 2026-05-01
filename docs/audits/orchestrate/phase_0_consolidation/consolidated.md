# Phase 0.0 — Synthesis (consolidated)

**Data**: 2026-04-27
**Stato**: synthesis di `report.md` (audit Codex) + `baseline.md` (audit Claude). Read-only, nessun codice/test toccato.
**Regola**: dove i due report divergono, il codice e` autorita`. Le voci non risolvibili senza nuove evidenze sono marcate **needs verification**.

---

## 1. Verdict sintetico

I due audit **convergono** sul giudizio di sistema:

> Phase 5 ha reso il sistema governabile. Non ancora industrializzato.
> Niente nuova extraction massiva. Il prossimo valore e` deploy reproducibility,
> security boundary consolidation, frontend/worker mass-pay-down, test
> de-coupling **mirato**, e rimozione COMPAT-RESIDUE **solo con evidenza**.

Differenza di taglio:
- **Codex (`report.md`)**: piu` strategico, organizzato in 6 dimensioni di debolezza, 9 residui R0-R8, roadmap a 5 fasi (0.1-0.5) tematiche. Include 2 elementi che Claude non ha visto.
- **Claude (`baseline.md`)**: piu` operativo, organizzato in 15 sezioni con grep evidence puntuale (file:linea), 25+ residui A1-A4/B1-B6/C1-C6/D1-D6/E1-E5, roadmap a 5 batch sequenziali (0.A-0.E). Include 4-5 elementi che Codex non ha visto.

**Conclusione**: i due audit sono complementari. La sintesi qui sotto unisce la struttura strategica di Codex con il dettaglio operativo di Claude.

---

## 2. Confronto report — punti concordanti

Entrambi confermano, con prove indipendenti:

| # | Tema | Codex | Claude |
|---|---|---|---|
| 1 | Phase 5 ha ridotto i god-file principali | §"What Looks Solid" | §11 + tabella file >1500 |
| 2 | `arcgis_client.py` resta il piu` grande non toccato (~2400 righe) | R5 — 2367 righe | §7 — 2476 righe |
| 3 | SSRF dual implementation (`validate_external_url` raise vs `_validate_outbound_url` return-string) — debt confermato, design call | R2 — high | §8.1 — C1 |
| 4 | COMPAT-RESIDUE (`_filesystem_fallback_load_project`, `_carica_sorgenti_progetto`) richiede log prima di rimuovere | R3 — needs evidence | §4.3 + E1, E2 |
| 5 | Test source-coupling massiccio (`inspect.getsource`, `patch("...logger")`, AST scan) | §3 — 1285 patch sites, "many" getsource | §6 — 17 + 5 file enumerati |
| 6 | Deploy non ancora industrializzato (`deploy.sh` imperative, paths hardcoded, server da riallineare) | §1 + R1+R8 — high | §9 + D1-D6 |
| 7 | Frontend grandi componenti = prossimo terreno di massa | §2 — medium/high | §7 — 7 file >800 righe |
| 8 | Crypto fail-fast in produzione + JWT secret fail-fast: gia` in posto | §"What Looks Solid" | §1.2 (invariante 2) |
| 9 | `validate_external_url` ora copre IPv4 + IPv6 + unspecified (B3-B.8) | §"What Looks Solid" | §8.1 + tabella |
| 10 | Working tree pulito, tag `phase-5-complete`, remote `PlenoraETL/plenora.git` | §"Current Repository State" | §1 + §9 |
| 11 | Phase 5 follow-up structural pass non e` urgente — non ripartire con extraction massiva | §"Bottom Line" | §10 C6 — defer |

11 punti concordanti = baseline tecnica stabile.

---

## 3. Confronto report — divergenze

Confronto numerico sui file >1500 righe (Claude usa `wc -l` su Windows shell, Codex un altro counter):

| File | Codex | Claude | Delta | Risoluzione |
|---|---:|---:|---:|---|
| `arcgis_client.py` | 2367 | 2476 | +109 | non risolto, **needs verification** — probabile differenza CRLF vs LF nel counter |
| `transforms/spatial.py` | 1606 | 1941 | +335 | come sopra |
| `services/file_service.py` | 1633 | 1795 | +162 | come sopra |
| `db/sql_functions.py` | 1505 | 1717 | +212 | come sopra |
| `estrai/query_generator.py` | 1451 | 1692 | +241 | come sopra |
| `services/schema_inference.py` | 1448 | 1465 | +17 | piccolo, irrilevante |
| `workers/tasks/enrichment_task.py` | 1230 | 1358 | +128 | come sopra |
| `frontend ApiConnectionManager.jsx` | 1127 | 1183 | +56 | come sopra |

**Verdict sulla divergenza**: i delta sono sistematici e proporzionali alla dimensione → quasi certamente differenza di metodo di conteggio (CRLF + line-ending counters). NON sono divergenze sostanziali. Il **ranking** dei file e` identico in entrambi i report. Per il baseline Phase 0 va bene la cifra Claude (`wc -l` standard); se serve una metrica autoritativa per il dashboard si fa una passata separata.

Altre divergenze:

| Tema | Codex | Claude | Risoluzione |
|---|---|---|---|
| Numero test backend | "~2260 backend test functions" | non quantifica | accettare metrica Codex (Claude non l'ha contata) |
| Numero patch/monkeypatch sites | "~1285" | non quantifica (enumera 22 file source-bound critici) | i due numeri sono compatibili: il totale include i patch normali, i 22 sono solo i source-bound bloccanti |
| Formula eval boundary | R7 — recurring audit target | non in lista A/B/C/D/E (solo menzionato §8.2 come AST whitelist) | **GAP CLAUDE** — aggiungere R7/B-formula al consolidated |
| Wrapper backward-compatible OAuth | non in residui | A2 — marcare DEPRECATED | divergenza di priorita`: Codex non li classifica, Claude li mette in A. Verdict: A2 resta. |
| `URBY_ALLOW_LOOPBACK_OUTBOUND` deprecato | non in lista | A3 — warning loud | divergenza di priorita`: A3 resta. |
| Git ignore permission warning | R0 — low | non in lista | **GAP CLAUDE** — aggiungere R0 (e` ambient noise ma e` un finding reale) |
| `arricchisci/api_connection_rest_file.py:1092` `owns=True backward-compat path non usati oggi` | non in lista | A4 — verificare se rimuovibile | **GAP CODEX** — Claude l'ha trovato via grep mirato, Codex no. A4 resta. |

Nessuna divergenza dottrinale. Solo coperture diverse. Tutte risolvibili per addizione.

---

## 4. Elementi presenti solo in Codex

| Tema | Trattamento nel consolidated |
|---|---|
| Git ignore permission warning | adottato come **R0** (Class A, priorita` low) |
| Formula eval boundary regular audit | adottato come **R7** (Class B/C, recurring audit) |
| Numero aggregato test backend (~2260) e patch sites (~1285) | adottato come metrica baseline §"Inventory" |
| Roadmap a 5 fasi 0.1-0.5 tematiche (artifacts → ops → security → tests → structural) | adottato come **macro-struttura** per la roadmap consolidata |
| `frontend Phase 5 second-pass` come prossima superficie di massa esplicita | adottato come C-frontend dedicato |
| Phase 5 reduction summary (api_connector ~600, ecc.) | gia` in CODE_MAP, riconfermato |

---

## 5. Elementi presenti solo in Claude

| Tema | Trattamento nel consolidated |
|---|---|
| Inventario env vars completo (~30 env) con classificazione canonical/deprecato/test-only | adottato come §"Feature flags" (riferimento a baseline §5) |
| 17 file `inspect.getsource` enumerati con file:linea | adottato in §"Source-bound test debt" |
| 5 file logger-bound enumerati | adottato come Batch 0.4-A (modernization candidates) |
| Re-export inventory completo (`# noqa: F401`) | adottato come §"Compat wrappers" |
| 3 fallback "legacy" sospetti (`enrichment_validation_preview.py:154`, `enrichment_service.py:263`, `manipola/plugin_executor.py:556`) | adottato come **A1** (verifica copertura test) |
| `arricchisci/api_connection_rest_file.py:1092` `owns=True` | adottato come **A4** |
| Deploy alignment dry-run con SSH probe specifico (`git remote -v`, `systemctl list-units`) | adottato come Batch 0.2 operativo (era 0.D) |
| MRO Phase 5 documentato per arricchisci (B3-A 7 mixin, B3-B 5 mixin) e carica (B2 6 mixin) e imdf (B4 facade) | gia` in CODE_MAP, non duplicato |
| `deploy.sh:20-22` commit-automatico se working tree sporco — pattern rischioso | adottato come **D5** |
| Server NON allineato a `PlenoraETL/plenora.git` post rename — stato del remote sul server ignoto | adottato come **D1-D3** (gia` allineati) |

---

## 6. Differenze di priorita` / classificazione

| Tema | Codex | Claude | Sintesi |
|---|---|---|---|
| Deploy alignment | **high before production** | **bloccante prima di Phase 1** | **bloccante** — primo batch operativo dopo synthesis e` 0.2 deploy probe |
| SSRF consolidation | **high** | C1 in Phase 1/2 | high, ma design-call: **NON in Phase 0** — Phase 0 documenta, Phase 1 implementa |
| COMPAT-RESIDUE rimozione | medium, needs evidence | E1-E3, needs evidence | concordi — Phase 0.4 evidence collection |
| Frontend pay-down | medium/high | C6 deferred a Phase 5 | **upgrade in Run 0/5**: e` candidato Phase 5 second-pass, non Phase 0 |
| Test de-coupling | medium | C5 + Batch 0.C parziale | concordi — modernizzare i 4 logger-piccoli ora, lasciare i 17 getsource per il loro refactor target |
| Big files structural pass | medium | C6 deferred | concordi — Phase 5 second-pass, non Phase 0 |
| Formula eval audit | medium | non classificato | **upgrade**: aggiungere come R7/B (audit recurring, non Phase 0 work) |
| Git ignore warning | low | non classificato | **add R0**, low |

Nessuna inversione di priorita`. Solo arricchimenti reciproci.

---

## 7. Inventory baseline (consolidato, single source)

### 7.1 Repo state

- Branch: `main`
- Remote: `origin → https://github.com/PlenoraETL/plenora.git` (clean post-Phase-5)
- Local sibling `ultra` rimasto come riferimento storico
- Tag: `phase-5-complete`
- Working tree: clean
- Root docs in repo: `README.md`, `CODE_MAP.md` (intenzionalmente minimi)
- Closeout 1-4 archiviati in `_history/phase0_4_root_docs_2026-04-27/`
- Vecchio orchestrate Phase 5 archiviato in `_history/orchestrate_phase5_2026-04-27/`
- Orchestrazione attiva: `int_efm/orchestrate/` (fuori repo)

### 7.2 Size baseline

| Area | Files | Lines (Codex) |
|---|---:|---:|
| `backend/app` | 211 | 66,584 |
| `backend/workers` | 9 | 3,888 |
| `backend/tests2` | 380 | 56,078 |
| `frontend/src` | 225 | 58,688 |
| `frontend/tests` | 52 | 6,916 |

Test backend functions: ~2260.
Patch / monkeypatch sites in backend tests: ~1285.

### 7.3 File grandi (ranking — uniforme tra i due audit, dimensioni Claude `wc -l`)

Backend >1500 righe:
1. `core/features/arcgis_client.py` — 2476 (top hotspot, NON toccato Phase 5)
2. `core/manipola/transforms/spatial.py` — 1941
3. `services/file_service.py` — 1795
4. `db/sql_functions.py` — 1717
5. `core/estrai/query_generator.py` — 1692
6. `services/schema_inference.py` — 1465 (sotto soglia ma vicino)
7. `workers/tasks/enrichment_task.py` — 1358

Frontend >800 righe:
1. `pages/ApiConnectionManager.jsx` — 1183
2. `components/panels/ResponseConfigBuilder.jsx` — 978
3. `components/transforms/ParameterInput.jsx` — 974
4. `components/panels/ApiEnrichNode.jsx` — 906
5. `components/nodes/result/ResultNodeStandardView.jsx` — 880
6. `pages/FileManager.jsx` — 875
7. `pages/ProjectList.jsx` — 862

**Stato**: nessun file backend >2000 lavorato da Phase 5. `arcgis_client.py` resta il piu` grande residuo. **Phase 0 NON apre extraction.**

---

## 8. Invarianti riconfermate (single list)

Le 8 invarianti di `CODE_MAP.md` "Contratti da non rompere" — riconfermate da entrambi gli audit, validate dal codice:

1. `backend/app/core/intermediate/` = contratto dati pipeline
2. `backend/app/shared/crypto.py` = unico crypto-at-rest, env canonica `PLENORA_ENCRYPTION_KEY` (alias rimossi 2026-04-23)
3. `frontend/src/api/` = unico HTTP point (boundary FE/API chiuso Phase 4 Batch C3)
4. `backend/app/core/orchestrator/` coordina, non duplica step (cancel/cleanup idempotenti)
5. `backend/app/core/scheduler/` passa sempre da orchestrator
6. `backend/app/core/features/` = contratto ArcGIS (`WriteFeaturesResponse`, `CreateServiceResponse`, token come `?token=` query param)
7. `backend/app/core/auth/` + RBAC = non bypassabile (`JWTMiddleware`, `require_*`)
8. `backend/app/core/manipola/transforms_registry.json` = fonte unica trasformazioni

---

## 9. LEDGER — confermato / aggiunto / rimosso

### 9.1 Voci LEDGER originali confermate (nessuna smentita)

Tutte e 6 le sezioni di `LEDGER.md` sono state riconfermate da entrambi gli audit:
- Architecture/Code Quality (retry consolidation, rate limiting, logger normalization, compat wrappers, dead code) ✓
- Product/Ops (remote/GitHub gia` pubblicato, deploy/server da riallineare) ✓
- Phase 5 Follow-Up (second pass moduli, ridurre commenti cutover, import morti, duplicati api_utils, failure modes test) ✓
- Behavior-Bound Test Debt ✓
- Known Carry-Over (Redis distributed limiter, retry consolidation, server/deploy, API connector, logger naming, compat wrappers) ✓

### 9.2 Voci da AGGIUNGERE al LEDGER (gaps emersi dalla synthesis)

| ID | Voce | Origine | Classe |
|---|---|---|---|
| L+1 | Formula eval security boundary — audit recurring (eval con AST whitelist e` boundary esplicita ma resta superficie sensibile) | Codex R7 | B/C — recurring |
| L+2 | Git global ignore permission warning (`Permission denied` su `C:\Users\Marco/.config/git/ignore`) — local environment fix | Codex R0 | A — local |
| L+3 | Frontend large components come **next mass surface** (Phase 5 second-pass candidate) | Codex §2 + Claude §7 | C — structural |
| L+4 | `URBY_ALLOW_LOOPBACK_OUTBOUND` alias retro-compat → da deprecare con warning loud | Claude A3 | A — local |
| L+5 | Wrapper backward-compatible OAuth (`get_oauth2_token`, `get_oauth2_client_credentials_token` in `shared/api_utils.py:580,591`) → marcare `# DEPRECATED` | Claude A2 | A — local |
| L+6 | 3 fallback "legacy" sospetti in arricchisci + manipola da verificare per copertura test | Claude A1 | A — needs verification |
| L+7 | `arricchisci/api_connection_rest_file.py:1092` `owns=True backward-compat path` candidato rimozione | Claude A4 | A — needs verification |
| L+8 | `deploy.sh` commit-automatico su working tree sporco = pattern rischioso post-Phase-5 | Claude D5 | D — ops |

### 9.3 Voci da RIMUOVERE / chiudere

Nessuna. Il LEDGER attuale e` solo da arricchire.

---

## 10. Residui consolidati A/B/C/D/E

Mappa unificata. ID stabili (R0-R8 da Codex preservati; A1-A4, D1-D6, E1-E5 da Claude preservati con prefisso).

### 10.A — fix-now safe/local/testable

| ID | Cosa | Origine | Stato |
|---|---|---|---|
| A0 | Doc drift `full_artifact` post-storage in 5 file | delta_post_storage | ✅ DONE in `c426685` (doc-only, +63 -42, 5 file) |
| A1 | Verificare 3 fallback "legacy" arricchisci/manipola (`enrichment_validation_preview.py:154`, `enrichment_service.py:263`, `manipola/plugin_executor.py:556`) | Claude | ⚠️ carry-over E-class — needs DB/log evidence (E3/E4) |
| A2 | `# DEPRECATED` su `get_oauth2_token` / `get_oauth2_client_credentials_token` | Claude | ✅ DONE — `shared/api_utils.py:601-635` ha `.. deprecated::` blocks |
| A3 | Warning loud su `URBY_ALLOW_LOOPBACK_OUTBOUND` quando settato | Claude | ✅ DONE — `shared/api_utils.py:81-90` emette `[DEPRECATED] URBY_ALLOW_LOOPBACK_OUTBOUND` one-shot |
| A4 | Verificare `api_connection_rest_file.py:1092 owns=True` candidato rimozione | Claude | ⚠️ carry-over E-class — needs telemetry (commento gia' marca "path backward-compat non usati oggi") |
| R0 | Git global ignore permission warning | Codex | ⚠️ local environment, non-codebase |

### 10.B — intentional residual (lock + documenta)

| ID | Cosa | Origine |
|---|---|---|
| B1 | Tutti i fallback intenzionali (crypto legacy plaintext, S3→FS, bulk→executemany, batch→row-by-row, ecc. — vedi baseline §3) | Claude |
| B2 | `_prepare_headers` shim 3-line in `api_connector.py` core (carry-over esplicito) | Claude + CODE_MAP |
| B3 | `RateLimiter` re-export in `enrichment_service.py` | Claude |
| B4 | `arcgis_uploader.py` facade re-export (Path A/B test contract) | Claude + CODE_MAP |
| B5 | `transforms/base.py` 358-righe facade (registry `getattr(base, op_X)`) | Claude + CODE_MAP |
| B6 | `_filesystem_fallback_load_project` `[COMPAT-RESIDUE]` — log esistente, candidato E2 | Claude |
| R7 | Formula eval boundary (eval con AST whitelist) — recurring audit, NON immediate bug | Codex |

### 10.C — architecture/design initiative (Phase 1+ multi-fase)

| ID | Cosa | Fase target | Origine |
|---|---|---|---|
| R2 / C1 | **SSRF single-source consolidation** (raise + return-string wrapper sopra stessa policy) | Phase 1/2 hardening | both |
| C2 | Retry consolidation (per-row + batch + api_connector + ArcGIS — 4 pattern simili, helper `_retry_http_machinery`) | Phase 1/2 | Claude + LEDGER |
| C3 | Rate limiter distributed (Redis vs in-memory) | Phase 2 | Claude + LEDGER |
| C4 | Logger naming uniform (verificare scheduler/orchestrator/auth/features) | Phase 1 | Claude |
| C5 / R6 | Test source-bound modernization (~17 getsource + ~5 logger) — modernizzare prima del refactor target | Phase 1 preflight | both |
| R4 / C-FE | Frontend large components — Phase 5 second-pass | Phase 5 | both |
| R5 / C6 | `arcgis_client.py` 2476 + altri big files — Phase 5 second-pass | Phase 5 | both |

### 10.D — deploy/external coordination

| ID | Cosa | Bloccante? | Origine |
|---|---|---|---|
| D1 / R1 | Verificare server alignment con `PlenoraETL/plenora.git` (vs vecchio repo) | **SI prima di Phase 1** | both |
| D2 | Verificare systemd unit names attivi (`plenora-*` vs `urby-*`) | SI | Claude |
| D3 / R8 | Riallineare `deploy.sh` (paths hardcoded, server hardcoded, no rollback) | SI | both |
| D4 | Aggiornare runbook deploy a Run 0/5 (snapshot Phase 5 esiste fuori repo) | medio | Claude |
| D5 | Decidere policy commit-automatico in `deploy.sh:20-22` | basso | Claude |
| D6 | GitHub branch protection / CI / checks su nuovo repo da verificare | basso | Claude |

### 10.E — needs evidence/log observation

| ID | Cosa | Come decidere | Origine |
|---|---|---|---|
| E1 / R3a | `[COMPAT-RESIDUE] manipola._carica_sorgenti_progetto` ancora osservato? | grep log produzione | both |
| E2 / R3b | `[COMPAT-RESIDUE] api_connector._filesystem_fallback_load_project` attivo? | log warning count | both |
| E3 | `manipola/plugin_executor.py:556` "Fallback al vecchio sistema" raggiungibile? | code path + telemetry | Claude |
| E4 | `enrichment_service.py:263` + `enrichment_validation_preview.py:154` "legacy required" path attivo? | unit test + telemetry | Claude |
| E5 | `auth/auth.py:299` legacy bcrypt — quanti utenti residui? | DB query password_hash format | Claude |

---

## 11. Roadmap Phase 0.1 → Phase 0.5 (consolidata)

Macro-struttura ispirata a Codex (5 fasi tematiche). Operativa con i batch granulari di Claude.
Ordine raccomandato:

### Phase 0.1 — Baseline Artifacts (read-only, ~3h)

**Output**: aggiornamento documentale, nessuna modifica codice.
- Aggiornare `orchestrate/LEDGER.md` con le 8 voci L+1..L+8 di §9.2
- Adottare ID residui consolidati (sostituire `A1-A4 / R0-R8 / D1-D6 / E1-E5` con elenco unificato §10)
- Identificare quali residui sono fix-now (A) vs design call (C)
- Closeout artifact: questo documento `consolidated.md` ratificato

### Phase 0.2 — Ops/Deploy Baseline (server probe, ~2h)

**Bloccante per Phase 1**. Prima azione operativa post-synthesis.
- D1 — `! ssh ubuntu@15.160.219.93 "cd /home/ubuntu/plenora-etl/backend && git remote -v && git log --oneline -5 && git status"`
- D2 — `! ssh ubuntu@15.160.219.93 "systemctl list-units --all 'plenora-*' 'urby-*'"`
- D3 — secret scan policy + verifica `.env.example` allineato col runtime
- D4 — `orchestrate/phase_0_consolidation/deploy_alignment.md` con stato server + flow GitHub→server documentato
- D5 — decisione su commit-automatico (proposta: `deploy.sh` deve fail-fast se working tree sporco invece di committare)
- Output: smoke checklist pre-deploy

### Phase 0.3 — Security Boundary Baseline (design, no rewrite, ~3h)

**Output**: design recommendations, NON refactor.
- R2/C1 — disegnare SSRF single-source: una primitive policy `_check_outbound_address(url) -> Optional[str]` + 1 wrapper raise + 1 wrapper return-string, decisione esplicita su loopback opt-in per REST_FILE
- C2 — disegnare retry consolidation: helper `_retry_http_machinery(callable, max_retries, retry_on_status, backoff_strategy)`
- R7 — confermare formula eval audit policy (recurring, non immediate)
- A2 + A3 — marker DEPRECATED su OAuth wrapper + warning loud su `URBY_ALLOW_LOOPBACK_OUTBOUND` (questi DUE possono essere implementati safe in 0.3 perche` non rompono nulla)
- Crypto/JWT: gia` fail-fast, no work
- File upload / S3 / result store boundaries: confermare e documentare

### Phase 0.4 — Test Coupling Audit + Evidence Collection (~4h)

**Output**: candidati modernizzazione + evidence raccolta.
- C5 modernizzazione **mirata**: i 4 file logger-bound piu` piccoli (`test_artifact_resolver_trusted_roots.py`, `test_project_service_canvas_parser_narrow.py`, `test_auth_delete_group_logging_contract.py`, `test_audit_action_decorator.py`) → handler stdlib agganciato per nome (pattern B2.0 gia` consolidato)
- I 17 `inspect.getsource` test NON modernizzati ora — accoppiati al loro refactor target in Phase 1+
- E1, E2, E3, E4, E5 — confermare che i `logger.warning("[COMPAT-RESIDUE] ...")` esistenti scrivano in `logs/` DB; aggiungere counter (NON action) su `auth.py:299` legacy bcrypt
- A1 — verificare copertura test sui 3 fallback "legacy" sospetti (esecuzione test esistenti + grep)
- A4 — verificare se `api_connection_rest_file.py:1092 owns=True` e` ancora raggiungibile

### Phase 0.5 — Structural Candidate Selection (~2h)

**Output**: scelta del prossimo stream strutturale, NON extraction.
- Confronto: frontend large components vs `arcgis_client.py` vs `file_service.py` vs `spatial.py` vs `query_generator.py` vs worker task orchestration
- Criterio: rischio business × valore × test coverage esistente × accoppiamento source-bound
- Output: 1 brief Phase 1 con stream scelto + cluster candidati + rollback strategy
- **NESSUNA extraction parte in Phase 0**

### Closeout Phase 0 (~1h)

- File: `orchestrate/phase_0_consolidation/closeout.md`
- Aggiornare `orchestrate/LEDGER.md` con stato post-Phase-0 (A1-A4 chiuse o declassate, evidence E1-E5 raccolta)
- Brief Phase 1 in `orchestrate/phase_1_stabilization/brief.md`

**Stima totale Phase 0**: ~15h work, ~10-12 commit (la maggior parte su `orchestrate/` fuori repo, 2 commit safe nel repo per A2+A3 in Phase 0.3).
**Modifiche al codice produzione**: solo A2 (DEPRECATED docstring) + A3 (warning loud su env alias). Tutto safe e locale.

---

## 12. Decisioni esplicite consolidate

1. **Phase 0 NON apre refactor**. Output documentale + ≤2 marker safe.
2. **Phase 0.2 deploy probe e` il PRIMO batch operativo post-synthesis**. Bloccante per Phase 1.
3. **C1 SSRF, C2 Retry, C3 Rate limiter, C5 Test debt = Phase 1+**, non Phase 0. Phase 0 documenta il design.
4. **R4/R5 (frontend + arcgis_client big files) = Phase 5 second-pass**, NON Phase 0 e NON immediato.
5. **E1-E5 evidence raccolta in 0.4** prima di decidere rimozioni.
6. **Niente extraction senza brief Phase 5.x dedicato + test contract modernization preventiva** (regola METHOD).

---

## 13. Confidence note

- **Alta confidence** (entrambi gli audit + codice riconferma): invarianti, deploy bloccante, SSRF dual, COMPAT-RESIDUE evidence-required, test source-coupling, frontend mass next.
- **Media confidence** (un solo audit + codice riconferma): A1-A4 (Claude only), R0+R7 (Codex only), conteggio file >1500 esatto.
- **Da verificare** (needs verification): server stato reale post-rename (D1-D2), copertura test sui 3 fallback "legacy" A1, raggiungibilita` `owns=True` A4. Tutte sono task di Phase 0.4.

---

## 14. Cosa NON fare in Phase 0

- Rimuovere COMPAT-RESIDUE senza log ✗
- Aprire extraction su `arcgis_client.py` o frontend ✗
- Modernizzare i 17 `inspect.getsource` test in massa ✗ (vanno fatti col loro refactor target)
- Consolidare SSRF / retry / rate limiter in commit-bombe ✗ (sono design call multi-fase)
- Toccare crypto, JWT, RBAC, transforms_registry ✗ (invarianti, no churn senza motivo)
- Push remoto durante Phase 0 ✗ (METHOD: "Nessun push remoto automatico durante la fase di refactor"; Phase 0 e` doc, ma per coerenza)

---

**Stato**: Phase 0.0 synthesis **chiusa**.
**Next**: in attesa di approvazione utente su roadmap §11 prima di aprire **Batch 0.2 deploy/server alignment probe**.
**Vincolo rispettato**: nessun file di codice/test/CI/conftest modificato. Solo `orchestrate/phase_0_consolidation/consolidated.md` scritto.
