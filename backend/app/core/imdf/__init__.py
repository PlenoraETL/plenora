"""
IMDF module — lettura ed elaborazione output del servizio imdf.plenora.it.

Lo zip IMDF prodotto contiene:
  {SITE_ID}.gdb/    File Geodatabase con 5 layer:
                    Localita, OperaPrincipale, Livello, Locale, Details
  {SITE_ID}.ifc     Modello IFC4 (3D)
  {SITE_ID}/        Cartella IMDF GeoJSON (standard Apple Indoor Mapping)
  manifest.json     Metadati del processing (sito, validation summary, ecc.)
  validation.json   Errori e warning per livello
  renderers.json    Stili ArcGIS in formato Esri drawingInfo

Sotto-moduli:
  loader        Estrazione zip + lettura GDB → GeoDataFrames riproiettati
  arcgis_upload Caricamento dei 5 layer su ArcGIS Online (Fase 1B, TODO)
  processor     Funzione orchestratrice end-to-end (Fase 1C, TODO)
"""
