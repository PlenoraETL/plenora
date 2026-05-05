"""Resource Engine loaders.

BE-GO-READY-RESOURCE-ENGINE-002-RESULT-BLOB-LOADER-PILOT.

v1 ships exactly one loader (`ResultBlobLoader`). Other kinds
(file_source_blob, intermediate_artifact, eseguibili_output,
imdf_bundle) remain in the audit (sec. 2.1) and join only after
their tenant binding is plumbed through.

Anti-god-engine rules apply: no `pandas` / `geopandas` / `fiona` /
`fastapi` / `app.core.auth` / `app.core.artifacts` imports under
this package. Locked by
`tests2/shared/test_resource_engine_anti_god_engine_lock.py`.
"""

from app.core.resources.loaders.result_blob import ResultBlobLoader

__all__ = ["ResultBlobLoader"]
