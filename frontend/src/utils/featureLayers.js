/**
 * featureLayers — UNA costruzione del nodo layer ArcGIS (Fase 5, T6, R3.15, N1).
 *
 * Prima c'erano tre ingressi con tre regole: il trascinamento dal pannello
 * dei servizi (nodo senza colonne ne' relazioni, schema caricato solo
 * aprendo il pannello del nodo), il layer correlato ("+ Join", tipo
 * `field_type` cioe' il tipo Esri grezzo) e il caricamento nel nodo (tipo
 * `type`, che lo schema non ha, quindi sempre 'str', e alias di
 * visualizzazione usato come rinomina).
 *
 * Regola unica, dallo schema del layer (GET /features/connections/{id}/schema,
 * backend features/arcgis_read.get_layer_schema):
 *  - colonne: nome del campo, tipo `urby_type` (la mappatura Esri -> tipi del
 *    canvas del backend, shared/connections/esri_types), `alias: null` (l'alias
 *    ArcGIS e' un'etichetta, non una rinomina: il backend usa sempre il nome);
 *  - relazioni, geometria e numero di elementi dello schema;
 *  - il nodo nasce gia' completo: niente dipende dall'apertura del pannello.
 */

/** Colonne del nodo dai campi dello schema del layer. */
export function featureLayerColumns(fields) {
  return (fields || []).filter(f => f?.name).map(f => ({
    name: f.name,
    type: f.urby_type || f.type || 'str',
    alias: null,
    visible: true,
  }));
}

/** `data` del nodo layer dallo schema e dai metadati della connessione. */
export function featureLayerData(schema, { layerUrl, layerName, connectionId, connectionName, serviceName, geometryType, featureCount }) {
  return {
    type: 'feature_source',
    layer_name: schema?.name || layerName || 'Layer',
    layer_url: layerUrl,
    connection_id: connectionId,
    connection_name: connectionName || '',
    service_name: serviceName || '',
    geometry_type: schema?.geometry_type_urby || schema?.geometry_type || geometryType || '',
    feature_count: schema?.feature_count ?? featureCount ?? null,
    where: '',
    columns: featureLayerColumns(schema?.fields),
    relationships: schema?.relationships || [],
  };
}
