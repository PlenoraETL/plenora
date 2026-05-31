"""
IMDF module — adattatore del formato ``plenora.indoor.bundle``.

Dopo il collasso a una-run, IMDF e' un semplice *format adapter*: il
convertitore esterno (imdf.plenora.it) emette un bundle JSON self-describing;
qui lo si legge e lo si traduce in layer GeoDataFrame + spec ArcGIS.

Moduli:
- ``bundle_loader``: parse del contratto ``plenora.indoor.bundle`` (core puro).
- ``bundle_geo``: assembla i layer in GeoDataFrame (boundary dataframes).
- ``bundle_arcgis_adapter``: registra l'adapter bundle -> N ``ArcGISLayerSpec``
  nel registry multi-layer di CARICA (il publish ArcGIS e' un nodo carica_arcgis
  downstream, in-pipeline).
"""
