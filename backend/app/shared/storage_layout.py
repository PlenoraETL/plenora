"""Centralized object-storage prefix constants.

Source-of-truth per i prefix usati come root segment delle key/path nel
runtime object storage. Vivevano duplicati in 5 file diversi
(`file_storage_dispatch.py`, `result_store.py`,
`intermediate/artifact_writer.py`, `imdf/storage.py`,
`eseguibili/storage.py`); ogni introduzione di un nuovo dominio rischiava
collisione silenziosa.

Convenzione naming:

- lowercase ASCII;
- `[a-z0-9]` + `-` come unico separatore;
- nessun `/` interno (i prefix sono SEGMENTI, il caller compone il path
  finale con `f"{PREFIX}/{...}"`);
- nessun trailing slash;
- nessuna stringa vuota.

Contratti go-like:

- ogni dominio storage ha esattamente un prefix const qui;
- contract test (`tests2/shared/test_storage_layout_contract.py`) verifica
  che (a) tutti i prefix siano unici e (b) nessuno sia prefix di un
  altro (per evitare che un `list_objects("results/")` raccolga
  oggetti di un dominio diverso che inizia con la stessa stringa).

Go mapping target:

```go
const (
    ResultsPrefix          = "results"
    FileSourcesPrefix      = "file-sources"
    IntermediatePrefix     = "intermediate"
    ImdfBundlesPrefix      = "imdf-bundles"
    EseguibiliOutputsPrefix = "eseguibili-outputs"
)
```
"""

from __future__ import annotations

from typing import Tuple


# Pipeline result finali user-visible (`results.storage_key`).
# Layout: `results/{project_id}/{result_id}.bin`
RESULTS_PREFIX = "results"

# Blob upload utente (`file_sources.storage_key`).
# Layout: `file-sources/{key_id}/{filename}` dove `key_id` puo' essere il
# `file_id` single-file o un `storage_key_id` shared multi-layer.
FILE_SOURCES_PREFIX = "file-sources"

# Intermediate snapshot full_artifact (`node_meta.artifact_uri`).
# Layout deterministico sotto `<prefix>/group=.../project=.../run=.../
# module=.../node=.../kind=.../{data.parquet|manifest.json}`. Il prefix
# qui e' il segmento root; il layout completo e' costruito da
# `intermediate.artifact_writer.build_artifact_key_prefix`.
INTERMEDIATE_PREFIX = "intermediate"

# IMDF bundle (zip + meta sidecar). Source-of-truth per l'object storage
# IMDF. Layout: `imdf-bundles/{task_id}.zip` + `imdf-bundles/{task_id}.meta.json`.
IMDF_BUNDLES_PREFIX = "imdf-bundles"

# Eseguibili task output. Layout:
# `eseguibili-outputs/{task_id}/{filename}`.
ESEGUIBILI_OUTPUTS_PREFIX = "eseguibili-outputs"


# Tutti i prefix conosciuti. Il contract test enumera questa tupla per
# garantire unicita' / no-prefix-of-another. Ogni nuovo dominio storage
# deve essere aggiunto qui.
ALL_PREFIXES: Tuple[str, ...] = (
    RESULTS_PREFIX,
    FILE_SOURCES_PREFIX,
    INTERMEDIATE_PREFIX,
    IMDF_BUNDLES_PREFIX,
    ESEGUIBILI_OUTPUTS_PREFIX,
)


_VALID_PREFIX_CHARS = set("abcdefghijklmnopqrstuvwxyz0123456789-")


def is_valid_prefix(value: str) -> bool:
    """True se `value` rispetta le regole di naming dei prefix.

    Regole:
    - non vuoto;
    - solo `[a-z0-9-]`;
    - nessuno slash, nessun whitespace, nessun trailing/leading `-`.
    """
    if not value:
        return False
    if value.startswith("-") or value.endswith("-"):
        return False
    return all(ch in _VALID_PREFIX_CHARS for ch in value)
