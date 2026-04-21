# Statement of Applicability — ISO 27001:2013 Annex A

**Scope**: applicabilita' dei controlli Annex A alla piattaforma URBY-ETL (backend + worker + deployment).
**Owner**: ACN workstream / security.
**Ultima revisione**: 2026-04-21.
**Perimetro**: controlli con implementazione applicativa (codice, config, policy operativa backend). I controlli puramente organizzativi (HR, fornitori, sicurezza fisica, BCM) ricadono sulle policy di Plenora Holding e sono indicati come **Org**.

**Legenda status**:
- **Implemented** — controllo in vigore con evidenza nel codice / infrastruttura
- **Partial** — controllo parzialmente implementato, gap noto tracciato
- **Not implemented** — controllo applicabile ma non implementato (pianificato o accettato come risk)
- **N/A — Org** — non applicabile al perimetro app, gestito a livello organizzativo
- **N/A — tech** — non applicabile tecnicamente (es. controllo su media fisici in un SaaS cloud)

---

## A.5 Information security policies

| Control | Title | Applicable | Status | Evidence |
|---|---|---|---|---|
| A.5.1.1 | Policies for information security | Y | N/A — Org | Policy sicurezza gestita da Plenora Holding |
| A.5.1.2 | Review of policies | Y | N/A — Org | Review ciclica organizzativa |

## A.6 Organization of information security

| Control | Title | Applicable | Status | Evidence |
|---|---|---|---|---|
| A.6.1.1 | ISMS roles and responsibilities | Y | N/A — Org | Definito a livello corporate |
| A.6.1.2 | Segregation of duties | Y | Partial | RBAC applicativo: `superadmin/admin/user/viewer/esecutore` con separazione CRUD vs execute vs read-only. Vedi `app/core/auth/auth.py:47-60` `VALID_ROLES`. Approvazioni a 4-occhi su azioni high-impact: non implementato. |
| A.6.1.3 | Contact with authorities | Y | N/A — Org | Fuori scope app |
| A.6.1.4 | Contact with special interest groups | Y | N/A — Org | Fuori scope app |
| A.6.1.5 | Infosec in project management | Y | Partial | ADR registry introdotto per tracciare decisioni security (vedi `docs/architecture/decisions/README.md`). Mancanza formale di DPIA per progetti pipeline con dati personali → gap. |
| A.6.2.1 | Mobile device policy | Y | N/A — Org | App server-side, no mobile |
| A.6.2.2 | Teleworking | Y | N/A — Org | Policy VPN gestita a livello infra |

## A.7 Human resource security

| Control | Title | Applicable | Status | Evidence |
|---|---|---|---|---|
| A.7.1.1 / A.7.1.2 / A.7.2.* / A.7.3.1 | HR lifecycle | Y | N/A — Org | Gestito da Plenora Holding (hiring, background check, offboarding) |

## A.8 Asset management

| Control | Title | Applicable | Status | Evidence |
|---|---|---|---|---|
| A.8.1.1 | Inventory of assets | Y | Partial | Inventario sistema in `docs/ops/`; catalogo crypto-at-rest in `docs/architecture/invariants.md` (chain chiavi). Manca inventario formale dei dati trattati → coperto dal Registro GDPR (vedi `registro-trattamenti-gdpr.md`). |
| A.8.1.2 | Ownership of assets | Y | Partial | Ownership per-risorsa applicativa: `projects.created_by`, `connections.created_by`, `datasources.created_by`, `schedules.created_by`, `file_sources.created_by`. Enforcement via `require_*_owner` in `app/core/auth/auth.py`. Ownership infrastructure: organizzativo. |
| A.8.1.3 | Acceptable use | Y | N/A — Org | Policy corporate |
| A.8.2.* | Information classification | Y | Not implemented | Nessuna classificazione formale a livello record. I dati sensibili (credentials) sono identificati via schema e cifrati a riposo, ma non esiste taxonomy "public / internal / confidential / restricted" applicata. Gap tracciato. |
| A.8.3.* | Media handling | Y | N/A — tech | Nessun media fisico: deploy cloud, backup su S3 cifrato |

## A.9 Access control

| Control | Title | Applicable | Status | Evidence |
|---|---|---|---|---|
| A.9.1.1 | Access control policy | Y | Implemented | RBAC a 5 ruoli documentato in `app/core/auth/auth.py:4-21`. Ownership + group-based access enforcement. |
| A.9.1.2 | Access to networks and services | Y | Partial | Network ACL (SG AWS) gestita a livello infra. A livello app: rate limit login `LOGIN_MAX_ATTEMPTS` + lockout `LOGIN_LOCKOUT_SECONDS` in `auth.py:75-77`. Trusted proxy allowlist in `app/shared/request_utils.py` (vedi ADR-0010). |
| A.9.2.1 | User registration / deregistration | Y | Implemented | Endpoint `POST /auth/users` (admin only) + `DELETE /auth/users/{id}`. Entrambi decorati `@audit_action`. |
| A.9.2.2 | User access provisioning | Y | Implemented | Solo admin/superadmin possono creare utenti. Ruolo assegnato esplicitamente. |
| A.9.2.3 | Management of privileged access rights | Y | Partial | Ruoli `admin`/`superadmin` distinti da `user`. Ogni cambio ruolo passa per `PUT /auth/users/{id}` (audited). Mancanza di "just-in-time elevation" → accettato per scope dimensione team. |
| A.9.2.4 | Management of secret authentication info | Y | Implemented | Password bcrypt (cost 12) via `verify_password` / `hash_password` in `auth.py`. Nessun recupero password self-service: admin esegue `POST /auth/users/{id}/reset-password`. |
| A.9.2.5 | Review of user access rights | Y | Not implemented | Nessun processo automatico di review periodica degli utenti attivi. Accettato per dimensione team attuale; da rivalutare se utenti > 50. |
| A.9.2.6 | Removal or adjustment of access rights | Y | Implemented | Admin puo' `DELETE /auth/users/{id}` oppure flag `is_active=0` (soft disable). JWT revocato in real-time via blacklist Redis (vedi ADR-0009). |
| A.9.3.1 | Use of secret authentication information | Y | Implemented | Password policy min 12 char su change-password (`auth.py:734`). No MFA enforcement (vedi A.9.4.2). |
| A.9.4.1 | Information access restriction | Y | Implemented | Tutti gli endpoint business hanno `Depends(require_*)`. Cross-tenant enforcement via `group_id` filter su query. Test copertura in `tests2/security/` (parametric cross-tenant). |
| A.9.4.2 | Secure log-on procedures | Y | Partial | Login richiede credenziali + MFA TOTP opt-in (vedi `app/core/auth/mfa.py`). Enforcement MFA obbligatoria per admin **non implementato** — flag `MFA_ENFORCEMENT` pianificato in follow-up invariants. Timing-attack safe: `verify_password` chiamato sempre, anche su username inesistente (`auth.py:625-631`). |
| A.9.4.3 | Password management system | Y | Implemented | bcrypt storage, min 12 char policy, niente password in chiaro in log, `@audit_action` su change + reset. |
| A.9.4.4 | Use of privileged utility programs | Y | N/A — Org | Shell access al server gestito dall'infra |
| A.9.4.5 | Access control to program source code | Y | N/A — Org | GitHub permission model |

## A.10 Cryptography

| Control | Title | Applicable | Status | Evidence |
|---|---|---|---|---|
| A.10.1.1 | Policy on the use of cryptographic controls | Y | Implemented | Documentata in `docs/architecture/invariants.md` (chain crypto) + ADR-0003 (Fernet at-rest) + ADR-0004 (JWT HS256). TLS in-transit enforced a livello reverse proxy (NGINX). |
| A.10.1.2 | Key management | Y | Partial | Chiavi simmetriche in SSM SecureString (post-migrazione 4B) con KMS AWS. Rotazione `JWT_SECRET_KEY` documentata (`docs/ops/jwt-incident-response.md`). Rotazione `PLENORA_ENCRYPTION_KEY`: **non supportata a caldo** — scelta deliberata (re-encryption richiederebbe downtime). Documentato in `docs/compliance/secrets-management.md`. |

## A.11 Physical and environmental security

| Control | Title | Applicable | Status | Evidence |
|---|---|---|---|---|
| A.11.* | tutti | Y | N/A — tech / Org | Deploy cloud (AWS). Sicurezza fisica DC gestita da AWS (ISO 27001, SOC2); policy office security: Plenora Holding |

## A.12 Operations security

| Control | Title | Applicable | Status | Evidence |
|---|---|---|---|---|
| A.12.1.1 | Documented operating procedures | Y | Partial | Ops docs in `backend/docs/ops/` (backup, restore, JWT incident response). Runbook completo incidenti: parziale. |
| A.12.1.2 | Change management | Y | Partial | Git flow + ADR. Nessun approval gate formale per PR critical: accettato per dimensione team (pair review sufficiente). |
| A.12.1.3 | Capacity management | Y | Partial | Pool PG con min/max (`DB_POOL_MIN`/`DB_POOL_MAX`), timeout idle-in-transaction (`DB_IDLE_IN_TRANSACTION_TIMEOUT_MS`). Monitoring capacity a livello infra. |
| A.12.1.4 | Separation of environments | Y | Implemented | `ENVIRONMENT=production` attiva fail-fast su chiavi mancanti (`app/shared/crypto.py:34`, `auth.py`). Staging: separato, diverso DB. |
| A.12.2.1 | Controls against malware | Y | N/A — tech | No esecuzione codice arbitrario: formula user-defined validata via whitelist AST (ADR-0001). Upload file validati per MIME + magic bytes (vedi `app/core/files/router.py`). |
| A.12.3.1 | Information backup | Y | Implemented | Backup automatico DB via `backend/scripts/backup_db.py`, tabella `plenora.backup_runs` per osservabilita' (vedi ADR-0008). Retention bucket S3 configurata lifecycle-side. Verify periodico: `plenora-backup-test.service` (restore dry-run su DB temporaneo). |
| A.12.4.1 | Event logging | Y | Implemented | Log strutturato `plenora.logs` + audit trail `plenora.audit_actions`. Trigger PG `block_log_modifications` vieta UPDATE/DELETE senza bypass esplicito (`migrations/2026-04-21_log_immutable_triggers.sql`, vedi Batch 3 stream). |
| A.12.4.2 | Protection of log information | Y | Implemented | Append-only enforcement DB-side (trigger PG) + retention cleanup via flag `SET LOCAL plenora.retention_cleanup = 'true'` esplicito. |
| A.12.4.3 | Administrator and operator logs | Y | Implemented | `@audit_action` decora tutti gli endpoint mutativi (29 endpoint coperti, vedi `STREAM_REPORT.md` Batch 3). Azioni admin visibili in `plenora.audit_actions`. |
| A.12.4.4 | Clock synchronization | Y | N/A — tech | NTP gestito da sistema operativo + AWS Time Sync |
| A.12.5.1 | Installation of software on operational systems | Y | N/A — Org | Policy pacchetti: solo `requirements.txt` + systemd package |
| A.12.6.1 | Management of technical vulnerabilities | Y | Partial | Audit di sicurezza del 2026-04-10 (63 backend fix + 10 frontend fix + 258 unit test, commit `df35930`). Pipeline CVE scanning automatico: **non implementato**. Gap tracciato. |
| A.12.6.2 | Restrictions on software installation | Y | N/A — Org | Deploy via CI/CD solo |
| A.12.7.1 | Information systems audit controls | Y | Implemented | Audit trail app-level (A.12.4.1) + CloudTrail AWS (infra) |

## A.13 Communications security

| Control | Title | Applicable | Status | Evidence |
|---|---|---|---|---|
| A.13.1.1 | Network controls | Y | Partial | SG AWS allowlist, VPC privata. Trusted proxy allowlist a livello app (ADR-0010). |
| A.13.1.2 | Security of network services | Y | Partial | TLS 1.2+ su edge (NGINX). JWT signing HS256 con secret rotante su incidente. |
| A.13.1.3 | Segregation in networks | Y | N/A — Org | VPC AWS |
| A.13.2.1 | Information transfer policies | Y | Partial | Nessun transfer esterno salvo: (a) esecuzione pipeline verso destination dichiarate nel canvas; (b) backup su S3 con server-side encryption AES256. |
| A.13.2.2 | Agreements on information transfer | Y | N/A — Org | DPA con clienti |
| A.13.2.3 | Electronic messaging | Y | N/A — tech | App non invia messaggi fuori perimetro |
| A.13.2.4 | Confidentiality agreements | Y | N/A — Org | NDA a livello corporate |

## A.14 System acquisition, development and maintenance

| Control | Title | Applicable | Status | Evidence |
|---|---|---|---|---|
| A.14.1.1 | Security requirements analysis | Y | Implemented | ADR registry + invariants document. Nuove feature hanno requisiti security espliciti in ADR dedicato. |
| A.14.1.2 | Securing application services on public networks | Y | Implemented | TLS, auth-required su tutti gli endpoint business, rate limit login. |
| A.14.1.3 | Protecting application services transactions | Y | Implemented | HTTPS E2E, JWT Bearer token, blacklist JWT su revoca. |
| A.14.2.1 | Secure development policy | Y | Partial | Code review via PR, ADR su decisioni security. Static analysis formale: **parziale** (test contract + unit + integration, no SAST automatico). |
| A.14.2.2 | System change control procedures | Y | Implemented | Git + PR + ADR + CI. |
| A.14.2.3 | Technical review after platform changes | Y | Partial | Cross-testing manuale. Regression suite: presente, non garantisce 100% branch coverage. |
| A.14.2.5 | Secure system engineering principles | Y | Implemented | Least privilege (RBAC), fail-fast su config mancante, defense-in-depth (whitelist AST + denylist dunder su formula, vedi ADR-0001). |
| A.14.2.6 | Secure development environment | Y | N/A — Org | Laptop management corporate |
| A.14.2.7 | Outsourced development | Y | N/A — Org | Team interno |
| A.14.2.8 | System security testing | Y | Implemented | Test di sicurezza in `backend/tests2/security/` (contract + unit). Incluso: cross-tenant parametric, SQL injection, CSV injection, client IP spoofing, crypto, enumeration, rate limit. |
| A.14.2.9 | System acceptance testing | Y | Implemented | CI runs full test suite before merge. |
| A.14.3.1 | Protection of test data | Y | Partial | Test data sintetico generato nei test. Staging con dati reali: anonimizzazione **non formalizzata**. Gap tracciato. |

## A.15 Supplier relationships

| Control | Title | Applicable | Status | Evidence |
|---|---|---|---|---|
| A.15.* | tutti | Y | N/A — Org | AWS, GitHub, GitLab: contratti e SLA a livello corporate |

## A.16 Information security incident management

| Control | Title | Applicable | Status | Evidence |
|---|---|---|---|---|
| A.16.1.1 | Responsibilities and procedures | Y | Partial | Playbook `docs/ops/jwt-incident-response.md` per incidente JWT. Altri playbook (compromissione nodo, leak DB): **non formalizzati**. |
| A.16.1.2 | Reporting events | Y | Partial | Log app + audit trail → disponibili per indagine. Processo di escalation a security team: organizzativo. |
| A.16.1.3 | Reporting weaknesses | Y | N/A — Org | Canale corporate |
| A.16.1.4-7 | Assessment, response, learning, evidence | Y | N/A — Org | Processo incident response gestito a livello Plenora Holding |

## A.17 Information security aspects of business continuity

| Control | Title | Applicable | Status | Evidence |
|---|---|---|---|---|
| A.17.1.* | BCM | Y | Partial | Backup + restore documentati (ADR-0008). RTO/RPO formali: **non dichiarati**. |
| A.17.2.1 | Availability of information processing facilities | Y | N/A — Org | HA infra AWS, multi-AZ gestito a livello infra |

## A.18 Compliance

| Control | Title | Applicable | Status | Evidence |
|---|---|---|---|---|
| A.18.1.1 | Legal requirements identification | Y | Partial | GDPR identificato (vedi `registro-trattamenti-gdpr.md`). Altri framework settoriali (PSD2, NIS2): da confermare con legal. |
| A.18.1.2 | Intellectual property rights | Y | N/A — Org | License compliance tracking corporate |
| A.18.1.3 | Protection of records | Y | Implemented | Audit trail `plenora.audit_actions` con retention in via di definizione (attuale: infinita). Trigger append-only (Batch 3 stream). |
| A.18.1.4 | Privacy and protection of PII | Y | Partial | Registro trattamenti GDPR (`registro-trattamenti-gdpr.md`). DPIA per singolo cliente: a carico del cliente + Plenora come processor. |
| A.18.1.5 | Regulation of cryptographic controls | Y | Implemented | Nessun uso di crypto proibita. AES-128 (Fernet), bcrypt, HMAC-SHA256, SHA-256. |
| A.18.2.1 | Independent review of information security | Y | Not implemented | Audit indipendente esterno: **non pianificato** per Fase 1. Da includere in roadmap post-shipping. |
| A.18.2.2 | Compliance with security policies and standards | Y | Partial | Questo SoA + ADR registry + invariants document costituiscono il riferimento interno. Review ciclica: **non formalizzata**. |
| A.18.2.3 | Technical compliance review | Y | Partial | Audit tecnico interno del 2026-04-10 (commit `df35930`). Cadenza formale: non definita. |

---

## Gap consolidato (priorita' successiva alla consegna Fase 1)

1. **MFA enforcement per admin/superadmin** (A.9.4.2) — feature flag `MFA_ENFORCEMENT` tracciato in `STREAM_REPORT.md § Follow-up invariants`.
2. **User access review periodica** (A.9.2.5) — processo organizzativo + script di reporting da definire.
3. **Information classification** (A.8.2) — taxonomy formale + tagging record non presente.
4. **CVE scanning automatico** (A.12.6.1) — pipeline CI con `pip-audit` o simile.
5. **Test data anonymization** (A.14.3.1) — formalizzare per staging.
6. **Runbook incidenti** (A.16.1.1) — estendere oltre JWT a: DB compromise, node compromise, S3 leak.
7. **RTO/RPO dichiarati** (A.17.1.*) — concordare con business.
8. **Audit esterno** (A.18.2.1) — post-Fase 1.

## Riferimenti cross-doc

- `backend/docs/architecture/invariants.md` — principi vincolanti (crypto chain, RBAC, logging policy)
- `backend/docs/architecture/decisions/` — ADR registry con rationale per-decisione
- `backend/docs/compliance/secrets-management.md` — policy operativa secret
- `backend/docs/compliance/registro-trattamenti-gdpr.md` — Art. 30 GDPR processing records
- `STREAM_REPORT.md` (ACN) — delta Fase 1 vs settimana 0, controlli mossi da Missing a Partial/Implemented
