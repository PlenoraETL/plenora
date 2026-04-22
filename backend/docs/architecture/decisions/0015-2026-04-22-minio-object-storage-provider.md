# ADR-0015: MinIO object storage provider via subclass `AwsS3ObjectStorage`

**Status**: Accepted
**Date**: 2026-04-22
**Deciders**: @CloudIA

## Context

A1 storage policy (design doc `orchestrate/phase_1_stabilization/a1_storage_policy.md §3.4`) nomina MinIO come provider P1: serve perche' on-prem senza bucket AWS richiede un object storage S3-compatibile per poter tenere policy `hybrid` o `object_storage`. Senza un secondo provider reale, la contract parity matrix `policy x provider` (A1 §6.2, scope `A1-B3`) e' vuota — i test possono mockare boto3 ma non validano portability cross-provider.

Boto3 funziona come client S3-compatibile contro MinIO passando `endpoint_url`, credenziali esplicite (MinIO non ha IAM chain), e `addressing_style=path` (MinIO non supporta virtual-hosted per default). La strada minima tracciata dal design e' "AwsS3ObjectStorage riusato con endpoint_url + path-style".

Due punti di tradeoff emergono dall'impl concreta:

1. **Server-side encryption**: la base class forza `ServerSideEncryption=AES256` in ogni `upload`/`upload_bytes`. MinIO vanilla non ha SSE configurato (richiede integrazione KES o auto-encryption a livello bucket); forzarlo a livello client romperebbe l'upload su deploy minimi. Serve decidere se il provider MinIO deve funzionare out-of-the-box o se richiede prerequisiti ops.
2. **Shape della classe**: `AwsS3ObjectStorage` puo' essere esteso con parametri opzionali (endpoint_url, addressing_style, credenziali) oppure specializzato in una subclass dedicata.

Invariant pre-esistente (ADR-0007 + `invariants.md §1.7`): il `Protocol ObjectStorage` e' congelato. Nessun metodo nuovo, nessuna modifica di signature. I consumer (`result_store.py`, `file_service.py`, reader simmetrici) parlano al Protocol e non sanno chi sta sotto.

## Decision

**`MinioObjectStorage` come subclass di `AwsS3ObjectStorage`** con due override minimi:

1. `_client()` — costruisce `boto3.client("s3", ...)` con `endpoint_url`, `aws_access_key_id`, `aws_secret_access_key`, `config=Config(s3={"addressing_style": "path"})`. Region default `us-east-1` (convenzione MinIO; il campo e' richiesto da boto3 ma ignorato dal server MinIO).
2. `_extra_args()` — ritorna `{"StorageClass": "STANDARD"}` senza `ServerSideEncryption`. Base class mantiene `{"ServerSideEncryption": "AES256", "StorageClass": "STANDARD"}` invariato (comportamento AWS bit-identico pre/post ADR).

Factory `get_object_storage()` (`app/shared/cloud_backends.py:271`) ha nuovo branch `provider == "minio"` che valida 4 env required (`BACKUP_BUCKET`, `MINIO_ENDPOINT_URL`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`) e costruisce la subclass. `get_secrets_backend()` (riga 241) con provider `minio` solleva errore esplicito — MinIO e' solo object storage, non fornisce secrets.

Boot validator `validate_storage_config()` aggiorna `IMPLEMENTED_PROVIDERS = ("aws", "minio", "none")`; nuovo blocco check nel ramo `policy != db` per le 3 env MinIO (fail-fast a boot, no probe di rete).

Log archive (`get_log_archive_storage()`) resta AWS-only in questo batch — follow-up separato se/quando richiesto.

## Rationale

- **Subclass vs estensione base class**: `AwsS3ObjectStorage` configurato con `endpoint_url` e credenziali esplicite e' funzionalmente MinIO, ma il nome della classe mentirebbe. Una subclass `MinioObjectStorage` dichiara il dominio senza duplicare logica: tutti i 6 metodi del Protocol (`list_objects`, `upload`, `upload_bytes`, `download`, `get_object_stream`, `delete`) sono ereditati senza modifiche. Zero duplicazione di codice S3.
- **Opzione B su SSE (default `_extra_args()` senza `ServerSideEncryption`)**: MinIO deve funzionare out-of-the-box. Forzare SSE a livello client su un deploy MinIO vanilla produce `InvalidRequest`. L'ops che vuole encryption at-rest la configura a livello bucket policy MinIO (auto-encryption built-in dal v2021+), indipendente dal client boto3. `StorageClass=STANDARD` resta perche' MinIO ignora `StorageClass` silenziosamente — non serve override per quello.
- **`_extra_args()` come hook sulla base class** (vs duplicare `upload` e `upload_bytes` nella subclass): 1 riga di diff sulla base (dict inline → `self._extra_args()`), 2 riferimenti aggiornati, override in subclass di 2 righe. Zero duplicazione. Comportamento AWS bit-identico.
- **`addressing_style=path`**: MinIO supporta sia path-style sia virtual-hosted dal v2022+, ma path-style e' l'unico universale su deploy piu' vecchi e su configurazioni dietro proxy/TLS semplici. Safe default.
- **Credenziali esplicite (`MINIO_ACCESS_KEY`/`SECRET_KEY`) vs IAM chain**: MinIO non ha IAM. Le credenziali vivono in `.env` come oggi per AWS se senza ruolo IAM. Stesso profilo di rischio del path AWS gia' in prod.
- **Secrets backend con MinIO = errore esplicito**: "MinIO non supporta secrets" e' piu' utile di un fallthrough generico "non supportato". Il decoupling di `SECRETS_PROVIDER` da `CLOUD_PROVIDER` e' riconosciuto dal design (A1 §7.3) come follow-up out-of-scope A1-B2.
- **Log archive AWS-only in questo batch**: `get_log_archive_storage()` ha proprio ciclo di governance (ADR-0014). Estenderlo a MinIO e' decisione separata; in A1-B2 resta fuori scope per evitare commit mixing.

## Alternatives considered

- **Opzione A — forzare SSE anche su MinIO** — *Rejected*. Ottiene simmetria con AWS ma rompe MinIO vanilla (upload fallisce con `InvalidRequest` su bucket senza SSE configurato). Richiederebbe runbook ops "abilita SSE MinIO prima del flip" — peggiora la story on-prem che A1 vuole migliorare.
- **Opzione C — env `MINIO_SERVER_SIDE_ENCRYPTION=on|off`** — *Rejected*. +1 env, +complessita' di validazione, nessun use case reale richiede SSE-S3 client-side su MinIO (la configurazione nativa MinIO e' gia' bucket-level). YAGNI.
- **Classe separata from scratch `MinioObjectStorage`** — *Rejected*. Duplicherebbe ~100 righe di boto3 wrapping senza benefit. Il Protocol e' gia' l'astrazione; il codice S3 e' identico per costruzione.
- **Estendere `AwsS3ObjectStorage` con parametri opzionali** (`endpoint_url=None`, `access_key=None`, `secret_key=None`, `addressing_style=None`) + factory sceglie il preset — *Rejected*. Il nome `AwsS3ObjectStorage` mentirebbe quando la classe rappresenta MinIO. Inoltre `__init__` crescerebbe da 2 a 5 parametri opzionali, con combinazioni inutili. Subclass dichiara l'intento e il contratto dei parametri.
- **Azure / GCP ora** — *Rejected*. Restano P2 (A1 §3.4). Gli SDK Azure Blob / GCS non sono compatibili boto3 — richiedono classi from-scratch. Prima di aprire quel lavoro va provato il Protocol su 2 provider "parenti" (AWS + MinIO); Azure/GCP arriva dopo che la parity matrix e' stabile.
- **Modificare `get_log_archive_storage()` a supportare MinIO** — *Deferred*, non rejected. Log archive ha ADR-0014 dedicato; la decisione se/come portare MinIO li' e' separata. Se arriva una richiesta ops di archive su MinIO, si apre ADR follow-up.

## Consequences

### Positive

- **Unblocks `A1-B3`**: con 2 provider reali, la contract parity matrix `policy x provider` (A1 §6.2) puo' essere scritta e validata. Prima di oggi era vuota (solo AWS).
- **On-prem senza AWS funzionante**: `STORAGE_POLICY=hybrid` + `CLOUD_PROVIDER=minio` diventa deployment supportato out-of-the-box, senza prerequisiti ops di SSE.
- **Zero regressione AWS**: `AwsS3ObjectStorage` mantiene SSE AES256 + StorageClass STANDARD identici pre/post commit. I test AWS esistenti (`tests2/shared/test_cloud_backends.py §3`) passano invariati.
- **Zero impatto consumer**: i 13 call site censiti in A1-B1 (dispatcher canonici, reader simmetrici, ortogonali) non cambiano. Parlano al Protocol.
- **Boot fail-fast esteso**: env MinIO mancante → boot fallisce prima del primo upload (coerente con policy A1 §5 "fail-fast su config, non reachability").

### Negative

- **Credenziali MinIO in `.env`**: non c'e' IAM chain. Mitigazione invariata rispetto a oggi: `.env` protetto, gestione via `load_secrets.py` (se deploy AWS-based) o cifrato at-rest. Stesso profilo AWS senza ruolo.
- **MinIO senza SSE by default**: deploy che richiedono encryption at-rest devono abilitarla via bucket policy MinIO, non via client. Documentare in runbook deploy on-prem.
- **`CLOUD_PROVIDER=minio` + `load_secrets.py`**: se qualcuno lancia lo script di bootstrap secrets con provider `minio`, fallira' con errore esplicito. Documentare: chi usa MinIO per object storage deve comunque puntare AWS SSM (o equivalente) per secrets, oppure iniettare via `.env` cifrato. Decoupling provider e' follow-up A1 §7.3.

### Neutral

- **Base class tocca 1 metodo nuovo (`_extra_args()`)**: cambio meccanico, nessun test AWS richiede update. Pattern hook standard per estensione.
- **Region stub su MinIO**: `AWS_REGION` default `us-east-1` passato a boto3 ma ignorato dal server MinIO. boto3 lo richiede per costruire il client. Nessun impatto funzionale.
- **Multipart upload su MinIO**: `TransferConfig(multipart_threshold=8MB)` eredita da `AwsS3ObjectStorage.upload_bytes`. MinIO supporta multipart S3-compliant. Overhead I/O accettabile, identico ad AWS.

## References

- Source: `backend/app/shared/cloud_backends.py:139` (`AwsS3ObjectStorage` + `_extra_args`), `:231` (`MinioObjectStorage`), `:280` (factory branch), `:425` (validate env block)
- Config: `backend/app/config.py:149-152` (DEFAULTS `MINIO_*`)
- Test: `backend/tests2/shared/test_cloud_backends.py §5` (provider impl), `backend/tests2/utils/test_storage_policy.py §5.bis` (boot check)
- Commit impl: `a5956f7`
- Related ADR: [ADR-0007](0007-2026-04-21-protocol-runtime-checkable-vs-abc.md) (Protocol runtime_checkable), [ADR-0011](0011-2026-04-21-file-sources-dispatch-s3-threshold.md) (FS dispatch symmetry), [ADR-0014](0014-2026-04-21-log-archive-pipeline.md) (log archive pipeline)
- Design: `orchestrate/phase_1_stabilization/a1_storage_policy.md §3.4` (roadmap provider), `§4` (config env), `§5` (validate fail-fast), `§7.2` (portability)
- Invariants: `invariants.md §1.7` (entry point + provider disponibili), `§1.7.bis` (ortogonali, registro A1-B1)
- Follow-up: `A1-B3` (contract parity matrix `policy x provider`)
