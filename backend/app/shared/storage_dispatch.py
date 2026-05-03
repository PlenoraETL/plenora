"""Pure decision boundary: DB vs object storage payload target.

Source-of-truth per la decisione "questo payload va in DB BYTEA o su
runtime object storage?". Vivevano duplicati in
`app.utils.result_store._should_use_object_storage` e
`app.services.file_storage_dispatch.dispatch_blob_storage` (ramo
policy/threshold). Estratto in `BE-DATA-PLANE-001B` come funzione PURA
condivisa.

Vincoli go-like:

- pure: zero env reads, zero import da `app.config.settings`, zero
  import da services/db/object_storage. Solo stdlib `typing`;
- decisione = solo `{"db", "object_storage"}`. Niente side-effect, niente
  upload, niente save.
- env parsing resta nei moduli dominio (`result_store`, `file_storage_dispatch`):
  questo modulo accetta i valori gia' risolti come argomenti keyword;
- DB hard cap NON e' parte della decisione: e' una guard post-decisione,
  enforced dal caller (`result_store._enforce_db_hard_cap`). Lasciato
  fuori per non confondere "decisione" con "fail-fast policy=db oversize";
- object-storage capability NON e' consultata qui: la coerenza
  policy/capability e' garantita al boot da
  `cloud_backends.validate_storage_config`. Aggiungerla qui sarebbe
  duplicare il check, oltre a cambiare il behavior runtime.

Mapping go target:

```go
type PayloadTarget string

const (
    PayloadTargetDB            PayloadTarget = "db"
    PayloadTargetObjectStorage PayloadTarget = "object_storage"
)

func ResolvePayloadTarget(policy string, sizeBytes int64, thresholdBytes int64) PayloadTarget {
    switch strings.ToLower(strings.TrimSpace(policy)) {
    case "db":
        return PayloadTargetDB
    case "object_storage":
        return PayloadTargetObjectStorage
    default:
        if sizeBytes >= thresholdBytes {
            return PayloadTargetObjectStorage
        }
        return PayloadTargetDB
    }
}
```
"""

from __future__ import annotations

from typing import Literal


PayloadTarget = Literal["db", "object_storage"]


# Costanti policy: il caller normalizza l'env value e ce la passa.
# Valori normalizzati ammessi (post-`strip().lower()`).
POLICY_DB = "db"
POLICY_OBJECT_STORAGE = "object_storage"
POLICY_HYBRID = "hybrid"


def resolve_payload_target(
    *,
    policy: str,
    size_bytes: int,
    threshold_bytes: int,
) -> PayloadTarget:
    """Decide se un payload va in DB BYTEA o su object storage.

    Pure function: stessa decisione presa pre-001B nei due dispatch
    inline (`result_store._should_use_object_storage` +
    `file_storage_dispatch.dispatch_blob_storage` ramo policy).

    Behavior bit-per-bit preservato:

    - `policy == "db"`             -> sempre `"db"` (il caller applica
                                     l'eventuale hard cap come guard
                                     post-decisione separata).
    - `policy == "object_storage"` -> sempre `"object_storage"` (il caller
                                     fail-closed se l'upload fallisce).
    - `policy == "hybrid"` o qualunque altro valore (incluso unknown
      policy):
                                     `size_bytes >= threshold_bytes` ->
                                     `"object_storage"`, altrimenti
                                     `"db"`.

    Il match policy e' case-insensitive con strip difensivo: replica il
    `(settings.get(...) or "hybrid").strip().lower()` che i caller
    esistenti gia' applicavano. Cosi' chiamate con `"DB"`/`" db "` non
    cadono silenziosamente nel ramo `else`.

    Args:
        policy: valore di STORAGE_POLICY normalizzato (ma il modulo lo
            re-normalizza). Valori canonici: `"db"`, `"object_storage"`,
            `"hybrid"`. Stringhe non riconosciute cadono nel branch
            threshold-based (preserva il comportamento pre-001B).
        size_bytes: dimensione del payload in byte.
        threshold_bytes: soglia per il branch hybrid. Per il dominio
            result e' `RESULT_S3_THRESHOLD_BYTES` (default 50 MB), per
            file-source e' `FILE_SOURCE_S3_THRESHOLD_BYTES` (default 50
            MB). Sono env distinte e restano tali (ops puo' tunarle in
            modo indipendente).

    Returns:
        `"db"` o `"object_storage"`.

    Note:
        Decisione su soglia inclusiva (`>=`): un payload esattamente di
        `threshold_bytes` byte va in object storage. E' il comportamento
        pre-001B.
    """
    normalized = (policy or "").strip().lower()

    if normalized == POLICY_DB:
        return "db"
    if normalized == POLICY_OBJECT_STORAGE:
        return "object_storage"
    # hybrid + qualunque altro valore (preserva il fallback pre-001B)
    if size_bytes >= threshold_bytes:
        return "object_storage"
    return "db"
