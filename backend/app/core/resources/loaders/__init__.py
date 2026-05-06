"""Resource Engine loaders.

BE-GO-READY-RESOURCE-ENGINE-002-RESULT-BLOB-LOADER-PILOT.
BE-GO-READY-RESOURCE-ENGINE-020-FILE-SOURCE-BLOB-LOADER.

v2 ships two loaders:
  - ``ResultBlobLoader``      — kind ``result_blob``
  - ``FileSourceBlobLoader``  — kind ``file_source_blob``

Other kinds (intermediate_artifact, eseguibili_output,
imdf_bundle) remain in the audit (sec. 2.1) and join only after
their tenant binding is plumbed through.

Anti-god-engine rules apply: no `pandas` / `geopandas` / `fiona` /
`fastapi` / `app.core.auth` / `app.core.artifacts` imports under
this package. Locked by
`tests2/shared/test_resource_engine_anti_god_engine_lock.py`.
"""

from app.core.resources.loaders.result_blob import ResultBlobLoader
from app.core.resources.loaders.file_source_blob import FileSourceBlobLoader

__all__ = ["ResultBlobLoader", "FileSourceBlobLoader"]
