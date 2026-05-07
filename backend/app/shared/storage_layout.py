"""
Centralized object-storage prefix constants.

Source-of-truth per i prefix usati come root segment delle key/path nel
"""

from __future__ import annotations

from typing import Tuple


# Pipeline result finali user-visible (`results.storage_key`
# Layout: `results/{project_id}/{result_id}.bin`
RESULTS_PREFIX = "results"

# Blob upload utente (`file_sources.storage_key`
# Layout: `file-sources/{key_id}/{filename}` dove `key_id` puo' essere il
# `file_id` single-file o un `storage_key_id` shared multi-layer
FILE_SOURCES_PREFIX = "file-sources"

# Intermediate snapshot full_artifact (`node_meta.artifact_uri`
# Layout deterministico sotto `<prefix>/group=.../project=.../run=.../
# module=.../node=.../kind=.../{data.parquet|manifest.json}`. Il prefix
# qui e' il segmento root; il layout completo e' costruito da
# `intermediate.artifact_writer.build_artifact_key_prefix`
INTERMEDIATE_PREFIX = "intermediate"

# IMDF bundle (zip + meta sidecar). Source-of-truth per l'object storage
# IMDF. Layout: `imdf-bundles/{task_id}.zip` + `imdf-bundles/{task_id}.meta.json`
IMDF_BUNDLES_PREFIX = "imdf-bundles"

# Eseguibili task output. Layout
# `eseguibili-outputs/{task_id}/{filename}`
ESEGUIBILI_OUTPUTS_PREFIX = "eseguibili-outputs"


# Tutti i prefix conosciuti. Il contract test enumera questa tupla per
# garantire unicita' / no-prefix-of-another. Ogni nuovo dominio storage
# deve essere aggiunto qui
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
