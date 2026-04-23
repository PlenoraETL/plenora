"""
IMDF module — lettura ed elaborazione output del servizio imdf.plenora.it.

Lo zip IMDF prodotto contiene:
  {SITE_ID}.gdb/    File Geodatabase con 5 layer (Localita, OperaPrincipale,
                    Livello, Locale, Details)
  {SITE_ID}.ifc     Modello IFC4 (3D)
  {SITE_ID}/        Cartella IMDF GeoJSON (standard Apple Indoor Mapping)
  manifest.json     Metadati del processing
  validation.json   Errori e warning per livello
  renderers.json    Stili ArcGIS in formato Esri drawingInfo

Sotto-moduli:
  loader          Estrazione zip + indicizzazione bundle + caricamento GDB ->
                  GeoDataFrame. Autoritativo per `ImdfBundle.site_id` (R4).
  persist         Move tmp_dir -> `IMDF_BUNDLES_DIR/{task_id}/`, scrive
                  `plenora_metadata.json`, garantisce cleanup tmp_dir in
                  finally. Local materializer puro (non conosce S3).
  storage         Cross-node bundle backup (S3 = source of truth, local FS
                  = cache): save_bundle, ensure_local_bundle, get_meta_only,
                  list_task_ids, delete_bundle.
  router          HTTP boundary + ACL: endpoint REST `/api/imdf/*`,
                  path-traversal guard via `app.shared.path_policy`,
                  group ACL fail-closed, orchestrazione hydrate cache.
  validator       Report read-only pre-publish (`validate_publish`).
  arcgis_uploader Upload dei 5 layer -> ArcGIS Online FeatureService.

Boundary & invariant (D3, design doc
`orchestrate/phase_1_stabilization/d3_imdf_boundaries.md`):
  - loader e' l'unica fonte autoritativa per `bundle.site_id` (R4): mai
    None; stringa vuota = euristica fallita. Reader downstream NON
    riderivano.
  - persist scrive `plenora_metadata.json` dal valore di
    `bundle.site_id`; router e validator leggono `meta["site_id"]`
    (R3). Nessuna scansione dir per riderivare site_id.
  - persist non conosce object storage (R2): l'orchestrazione
    persist -> storage vive nel worker (`enrichment_task`) come passi
    espliciti. Persist resta local materializer puro.
  - Invariant "meta sempre presente nel dest dir": se il write meta
    fallisce, persist esegue rollback della dest dir e solleva
    (D3 commit 5, §9.6).
  - Path-traversal guard centralizzato in
    `app.shared.path_policy.validate_imdf_task_id` (condiviso da
    router, validator, `workers/tasks/imdf_publish_task`).
  - Router decide cache policy HTTP (quando hydratare): `/publish`,
    `/bundles/{id}`, `/validate-publish` tutti chiamano
    `_hydrate_local_cache` per parita' multi-host.

Ratifica boundary: ADR-0016 (D3 commit 6).
"""
