# Phase 1 - Stabilization 2 Brief

Scopo: stabilizzare i problemi emersi dalla nuova consolidation.

Focus:
- piccoli fix mirati;
- test fragili;
- logger/error handling incoerenti;
- import/dependency cleanup;
- regressioni nei moduli appena estratti.

## Starting Point

Phase 0 e' chiusa come consolidation read-only. Non ripartire con extraction
massiva: la prima stabilizzazione deve essere piccola, reversibile e con
behavior invariato.

## Stream P1-A - Legacy Marker Hygiene

Pre-step consigliato: fix doc-only di A0 da
`phase_0_consolidation/delta_post_storage_2026_04_28.md`, cioe' aggiornare
docstring/commenti interni che descrivono ancora `full_artifact` come non
raggiungibile o "Batch 5". Scope: `app/core/intermediate/policy.py`,
`app/core/intermediate/snapshot.py`, `app/config.py`,
`app/shared/cloud_backends.py` e `app/db/intermediate_schema.sql`.

Scope consigliato:

- `app/shared/api_utils.py`
  - marcare `get_oauth2_token` e
    `get_oauth2_client_credentials_token` come wrapper deprecated nelle
    docstring;
  - aggiungere warning one-shot quando viene usato
    `URBY_ALLOW_LOOPBACK_OUTBOUND` senza il canonical
    `PLENORA_ALLOW_LOOPBACK_OUTBOUND`.

Contratto:

- nessun cambio SSRF policy;
- nessun cambio token OAuth;
- warning idempotente per processo;
- commit piccolo.

## Stream P1-B - Logger-Bound Test Modernization

Scope opzionale, test-only:

- `tests2/shared/test_artifact_resolver_trusted_roots.py`
- `tests2/services/test_project_service_canvas_parser_narrow.py`
- `tests2/security/unit/test_audit_action_decorator.py`
- `tests2/security/unit/test_auth_delete_group_logging_contract.py`

Obiettivo: sostituire patch del logger module-level con cattura behavior-bound
su handler/metodi logger, senza toccare codice runtime.

## Stream P1-C - SSRF Single-Source Design Gate

Solo dopo P1-A/P1-B:

- trasformare il design di `phase_0_3_security_env.md` in proposta
  implementativa;
- modernizzare i test che patchano `_validate_outbound_url` prima di spostare
  la primitive;
- nessun merge raise/return-string senza copertura behavior-bound.
