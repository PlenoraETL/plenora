/**
 * CaricanArcGISNode.jsx — Nodo canvas per scrittura risultati su ArcGIS Feature Service
 *
 * Nodo terminale che riceve l'output dal ResultNode e lo scrive su ArcGIS:
 *  - Add Features (append)
 *  - Update Features (richiede OBJECTID)
 *  - Delete Features (richiede OBJECTID)
 *  - Crea nuovo Feature Service
 *
 * Si aspetta un edge in ingresso da ResultNode (handle "output").
 */
import { useResource } from '../../hooks/useResource';
import '../common/ListState.css';
import React, { useState, memo } from 'react';
import { Position } from '@xyflow/react';
import useCanvasStore from '../../store/useCanvasStore';
import { useColumnsOf } from '../../hooks/useColumns';
import ColumnStatusNote from '../common/ColumnStatusNote';
import {
  listFeatureConnections,
  browseServices,
  browseLayers,
  getLayerSchema,
  listFolders,
} from '../../api/features';
import { humanizeError } from '../../utils/errorMessages';
import { dataFlowEdges } from '../../utils/runtimeBindingEdges';
import './CaricanArcGISNode.css';
import NodeHandle from '../canvas/NodeHandle';
import AutoRunNote from './carica/autoRunNote';
import { READ_ONLY_SAFE } from '../common/readOnlySafe';
import { formatNumber } from '../../utils/format';
import { pressable } from '../../utils/pressable';
import { useCanvasDerived } from '../../hooks/useCanvasDerived';

const CaricanArcGISNode = memo(({ data, id }) => {
  const [showMapping, setShowMapping] = useState(false);

  // State ArcGIS

  const arcgisConnectionId = data.arcgis_connection_id || '';
  const arcgisServiceUrl = data.arcgis_service_url || '';
  const arcgisLayerUrl = data.arcgis_layer_url || '';
  const arcgisOperation = data.arcgis_operation || 'add';
  const arcgisNewServiceName = data.arcgis_new_service_name || '';
  const arcgisNewLayerName = data.arcgis_new_layer_name || 'Layer 0';
  const arcgisFolderId = data.arcgis_folder_id || '';
  const arcgisMatchColumn = data.arcgis_match_column || '';
  const arcgisSourceSrid = data.source_srid ?? '';
  const columnMappings = data.column_mappings || [];
  // Colonne che escono dall'Output (ingresso di questo nodo), con gli id:
  // dalla fonte unica (Blocco A, regola 4), non salvate nel nodo.
  const inputResult = useColumnsOf(id, { side: 'input' });
  const availableColumns = inputResult.columns;

  // Multi-layer: se a monte c'e' un api_enrich con output_kind='imdf', l'upstream
  // e' un bundle `plenora.indoor.bundle`. In quel caso il sink ArcGIS scrive N
  // layer in una-run (vedi carica_task._maybe_multilayer_arcgis_write): layer,
  // chiavi di match e renderer arrivano dal bundle, quindi i campi mono-layer
  // (layer url / match column / nome layer) non servono. Il backend rileva il
  // bundle dai bytes; qui adattiamo solo la UX.
  // Visita a monte solo quando cambia il contenuto (UI-2, V5).
  const isMultiLayerUpstream = useCanvasDerived((state) => {
    const edges = dataFlowEdges(state.edges);
    const nodes = state.nodes || [];
    const byId = new Map(nodes.map(n => [n.id, n]));
    const seen = new Set();
    let frontier = edges.filter(e => e.target === id).map(e => e.source);
    while (frontier.length) {
      const next = [];
      for (const src of frontier) {
        if (seen.has(src)) continue;
        seen.add(src);
        const node = byId.get(src);
        if (node?.type === 'api_enrich' && node.data?.output_kind === 'imdf') return true;
        edges.filter(e => e.target === src).forEach(e => next.push(e.source));
      }
      frontier = next;
    }
    return false;
  }, { deps: [id] });

  // RB.7.C: backend (RB.7.B) marca data.access_denied=true quando la
  // connessione ArcGIS riferita dal canvas non e' visibile al gruppo del
  // progetto (canvas storico cross-tenant, post rebrand 2026-04-18).
  // Non rimuoviamo il nodo: l'utente deve poterlo correggere o eliminare
  // manualmente. Disabilitiamo le chiamate API (eviterebbero 404 fail-loud
  // a vuoto su listFolders/browseServices/...) e marchiamo isValid=false.
  const isAccessDenied = !!data.access_denied;
  const accessDeniedReason = data.access_denied_reason || '';

  const arcgisOperations = isMultiLayerUpstream
    ? [
        { value: 'create_service', label: 'Crea servizio (N layer)', desc: 'Crea un Feature Service con tutti i layer del pacchetto' },
        { value: 'add', label: '+ Aggiungi elementi', desc: 'Aggiunge gli elementi di ogni layer al servizio esistente (corrispondenza per nome)' },
        { value: 'upsert', label: 'Aggiorna o inserisci elementi', desc: 'Aggiorna o inserisce per chiave (dal pacchetto) su ogni layer' },
        { value: 'update', label: 'Aggiorna elementi', desc: 'Aggiorna per chiave (dal pacchetto) su ogni layer' },
      ]
    : [
        { value: 'add', label: '+ Aggiungi elementi', desc: 'Aggiunge elementi al layer esistente' },
        { value: 'update', label: 'Aggiorna elementi', desc: 'Aggiorna gli elementi esistenti per colonna chiave' },
        { value: 'upsert', label: 'Aggiorna o inserisci elementi', desc: 'Aggiorna se esiste, inserisce se nuovo' },
        { value: 'delete', label: 'Elimina elementi', desc: 'Elimina gli elementi per colonna chiave' },
        { value: 'create_service', label: 'Crea servizio', desc: 'Crea un nuovo Feature Service e carica i dati' },
      ];

  // Fase 5 T2 (R4.5, R4.17): connessioni, servizi, layer, cartelle e schema
  // del layer dall'hook comune. Ogni scelta cambiata e' una nuova richiesta
  // (la risposta di prima non scrive sulla scelta nuova); ogni errore e'
  // mostrato con «Riprova» (prima connessioni, cartelle e schema finivano
  // solo in console). RB.7.C: niente richieste con la connessione
  // access_denied.
  const live = !isAccessDenied;
  const connsResource = useResource(async () => {
    const res = await listFeatureConnections();
    return Array.isArray(res) ? res : res.connections || [];
  }, { key: 'arcgis-connections', errorMessage: (e) => humanizeError(e).message });
  const servicesResource = useResource(async () => {
    const res = await browseServices(arcgisConnectionId);
    return Array.isArray(res) ? res : res.services || [];
  }, { key: ['arcgis-services', arcgisConnectionId || null], enabled: live && Boolean(arcgisConnectionId), errorMessage: (e) => humanizeError(e).message });
  const layersResource = useResource(async () => {
    const res = await browseLayers(arcgisConnectionId, arcgisServiceUrl);
    return res.layers || res || [];
  }, {
    key: ['arcgis-layers', arcgisConnectionId || null, arcgisServiceUrl || null],
    enabled: live && Boolean(arcgisConnectionId && arcgisServiceUrl) && arcgisOperation !== 'create_service',
    errorMessage: (e) => humanizeError(e).message,
  });
  const foldersResource = useResource(async () => {
    const res = await listFolders(arcgisConnectionId);
    return res.folders || res || [];
  }, {
    key: ['arcgis-folders', arcgisConnectionId || null],
    enabled: live && Boolean(arcgisConnectionId) && arcgisOperation === 'create_service',
    errorMessage: (e) => humanizeError(e).message,
  });
  const schemaResource = useResource(() => getLayerSchema(arcgisConnectionId, arcgisLayerUrl), {
    key: ['arcgis-layer-schema', arcgisConnectionId || null, arcgisLayerUrl || null],
    enabled: live && Boolean(arcgisConnectionId && arcgisLayerUrl) && arcgisOperation !== 'create_service',
    errorMessage: (e) => humanizeError(e).message,
  });
  const arcgisConnections = connsResource.data || [];
  const isLoadingArcgisConns = connsResource.loading;
  const arcgisServices = servicesResource.data || [];
  const isLoadingServices = servicesResource.loading;
  const arcgisLayers = layersResource.data || [];
  const isLoadingLayers = layersResource.loading;
  // Senza cartelle note la root resta scegliibile.
  const arcgisFolders = foldersResource.status === 'error' ? [{ id: '', title: '(root)' }] : (foldersResource.data || []);
  const isLoadingFolders = foldersResource.loading;
  const arcgisLayerSchema = schemaResource.data || null;
  const isLoadingSchema = schemaResource.loading;
  const failed = [
    ['Connessioni ArcGIS non caricate', connsResource],
    ['Servizi non caricati', servicesResource],
    ['Layer non caricati', layersResource],
    ['Cartelle non caricate', foldersResource],
    ['Schema del layer non caricato', schemaResource],
  ].find(([, r]) => r.status === 'error');
  const arcgisError = failed ? `${failed[0]}: ${failed[1].error}` : '';
  const retryArcgis = failed ? failed[1].reload : null;

  const autoMapArcgis = () => {
    if (!arcgisLayerSchema?.fields || availableColumns.length === 0) return;
    const destFields = arcgisLayerSchema.fields
      .filter(f => f.type !== 'esriFieldTypeOID' && f.type !== 'esriFieldTypeGlobalID')
      .map(f => f.name);
    const mappings = [];
    for (const srcCol of availableColumns) {
      const srcName = (srcCol.name || srcCol).toLowerCase();
      const match = destFields.find(d => d.toLowerCase() === srcName);
      if (match) {
        mappings.push({ source: srcCol.name || srcCol, dest: match });
      }
    }
    handleUpdate('column_mappings', mappings);
  };

  const _updateNodeData = useCanvasStore(s => s.updateNodeData);

  const handleUpdate = (field, value) => {
    _updateNodeData(id, { [field]: value });
  };

  const handleMultiUpdate = (updates) => {
    _updateNodeData(id, updates);
  };

  const handleArcgisConnectionChange = (newConnId) => {
    handleMultiUpdate({
      arcgis_connection_id: newConnId,
      arcgis_service_url: '',
      arcgis_layer_url: '',
      column_mappings: [],
    });
  };

  const handleArcgisServiceChange = (serviceUrl) => {
    handleMultiUpdate({
      arcgis_service_url: serviceUrl,
      arcgis_layer_url: '',
      column_mappings: [],
    });
  };

  const handleArcgisLayerChange = (layerUrl) => {
    handleMultiUpdate({
      arcgis_layer_url: layerUrl,
      column_mappings: [],
    });
  };

  const handleArcgisOperationChange = (op) => {
    const updates = { arcgis_operation: op };
    if (op === 'create_service') {
      updates.arcgis_service_url = '';
      updates.arcgis_layer_url = '';
    }
    handleMultiUpdate(updates);
  };

  const handleMappingChange = (index, field, value) => {
    const newMappings = [...columnMappings];
    newMappings[index] = { ...newMappings[index], [field]: value };
    handleUpdate('column_mappings', newMappings);
  };

  const handleAddMapping = () => {
    handleUpdate('column_mappings', [...columnMappings, { source: '', dest: '' }]);
  };

  const handleRemoveMapping = (index) => {
    handleUpdate('column_mappings', columnMappings.filter((_, i) => i !== index));
  };

  const needsMatchColumn = ['update', 'upsert', 'delete'].includes(arcgisOperation);
  const matchColumnValid = !needsMatchColumn || arcgisMatchColumn;

  const isValid = !isAccessDenied && (
    arcgisOperation === 'create_service'
      ? (arcgisConnectionId && arcgisNewServiceName)
      : isMultiLayerUpstream
        // multi-layer: basta il Feature Service; il match e' per nome layer (dal bundle)
        ? (arcgisConnectionId && arcgisServiceUrl)
        : (arcgisConnectionId && arcgisLayerUrl && matchColumnValid)
  );

  const arcgisDestFields = (arcgisLayerSchema?.fields || [])
    .filter(f => f.type !== 'esriFieldTypeOID' && f.type !== 'esriFieldTypeGlobalID')
    .map(f => f.name);

  return (
    <div className={`carica-arcgis-node ${!isValid ? 'carica-arcgis-node--invalid' : ''}`}>
      <NodeHandle
        type="target"
        position={Position.Left}
        className="carica-arcgis-node__handle carica-arcgis-node__handle--input"
      />

      {/* Titolo, chiusura ed eliminazione sono del pannello (NodeInspector,
          regola 6 del Blocco A); il modulo resta sempre aperto. */}
      <div className="carica-arcgis-node__body">
        <ColumnStatusNote result={inputResult} />

          {/* RB.7.C: connessione ArcGIS non accessibile al gruppo del progetto.
              Banner visibile, no rimozione automatica del nodo. */}
          {isAccessDenied && (
            <div
              className="plen-banner-warn"
              role="alert"
              title={accessDeniedReason || 'Connessione ArcGIS non accessibile'}
            >
              🔒 Connessione ArcGIS non accessibile al tuo gruppo.
              {accessDeniedReason ? (
                <div className="plen-banner-warn__detail">
                  {accessDeniedReason}
                </div>
              ) : null}
            </div>
          )}

          {/* Multi-layer (bundle IMDF): badge informativo */}
          {isMultiLayerUpstream && (
            <div className="carica-arcgis-node__hint" style={{ background: 'rgba(124,58,237,0.10)', border: '1px solid rgba(124,58,237,0.35)', borderRadius: 6, padding: '6px 8px', color: '#7C3AED' }}>
              📦 Pacchetto con più layer (IMDF): i layer, le chiavi di corrispondenza e
              la simbologia arrivano dal pacchetto. Configura solo la destinazione.
            </div>
          )}

          {/* Operazione */}
          <div className="carica-arcgis-node__section">
            <label className="carica-arcgis-node__label">Operazione</label>
            <select
              value={arcgisOperation}
              onChange={(e) => handleArcgisOperationChange(e.target.value)}
              className="carica-arcgis-node__select">
              {arcgisOperations.map(op => (
                <option key={op.value} value={op.value}>{op.label}</option>
              ))}
            </select>
            <div className="carica-arcgis-node__hint">
              {arcgisOperations.find(o => o.value === arcgisOperation)?.desc}
            </div>
          </div>

          {/* Connessione ArcGIS */}
          <div className="carica-arcgis-node__section">
            <label className="carica-arcgis-node__label">Connessione ArcGIS</label>
            <select
              value={arcgisConnectionId}
              onChange={(e) => handleArcgisConnectionChange(e.target.value)}
              className="carica-arcgis-node__select"
              disabled={isLoadingArcgisConns}>
              <option value="">
                {isLoadingArcgisConns ? 'Caricamento...' : 'Seleziona connessione...'}
              </option>
              {arcgisConnections.map(conn => (
                <option key={conn.id} value={conn.id}>
                  {conn.name} ({conn.server_type})
                </option>
              ))}
            </select>
          </div>

          {/* SRID sorgente (CRS dei dati in ingresso). Non in multi-layer:
              il CRS di ogni layer arriva dal descrittore del bundle. */}
          {!isMultiLayerUpstream && arcgisConnectionId && (
            <div className="carica-arcgis-node__section">
              <label className="carica-arcgis-node__label">SRID sorgente (EPSG)</label>
              <input
                type="number"
                value={arcgisSourceSrid}
                onChange={(e) => handleUpdate('source_srid', e.target.value === '' ? null : parseInt(e.target.value, 10))}
                placeholder="4326 (predefinito)"
                className="carica-arcgis-node__input"
              />
              <div className="carica-arcgis-node__hint">
                CRS dei dati in ingresso. Vuoto = 4326 (WGS84).
              </div>
            </div>
          )}

          {/* CREA NUOVO SERVIZIO */}
          {arcgisOperation === 'create_service' && arcgisConnectionId && (
            <>
              <div className="carica-arcgis-node__section">
                <label className="carica-arcgis-node__label">Cartella Destinazione</label>
                <select
                  value={arcgisFolderId}
                  onChange={(e) => handleUpdate('arcgis_folder_id', e.target.value)}
                  className="carica-arcgis-node__select"
                  disabled={isLoadingFolders}>
                  <option value="">
                    {isLoadingFolders ? 'Caricamento cartelle...' : '(root)'}
                  </option>
                  {arcgisFolders.filter(f => f.id).map((folder, idx) => (
                    <option key={idx} value={folder.id}>{folder.title}</option>
                  ))}
                </select>
              </div>
              <div className="carica-arcgis-node__section">
                <label className="carica-arcgis-node__label">Nome Nuovo Servizio</label>
                <input
                  type="text"
                  value={arcgisNewServiceName}
                  onChange={(e) => handleUpdate('arcgis_new_service_name', e.target.value)}
                  placeholder="es. dati_particelle_2026"
                  className="carica-arcgis-node__input"
                />
              </div>
              {!isMultiLayerUpstream && (
                <div className="carica-arcgis-node__section">
                  <label className="carica-arcgis-node__label">Nome Layer</label>
                  <input
                    type="text"
                    value={arcgisNewLayerName}
                    onChange={(e) => handleUpdate('arcgis_new_layer_name', e.target.value)}
                    placeholder="Layer 0"
                    className="carica-arcgis-node__input"
                  />
                </div>
              )}
              <div className="carica-arcgis-node__hint">
                {isMultiLayerUpstream
                  ? 'Verrà creato un Feature Service con tutti i layer del pacchetto (nomi, schema, chiavi e simbologia dal pacchetto)'
                  : 'Verrà creato un nuovo Feature Service con lo schema dei dati del progetto'}
              </div>
            </>
          )}

          {/* ADD / UPDATE / DELETE — seleziona servizio e layer */}
          {arcgisOperation !== 'create_service' && arcgisConnectionId && (
            <>
              {/* Servizio */}
              <div className="carica-arcgis-node__section">
                <label className="carica-arcgis-node__label">Feature Service</label>
                <select
                  value={arcgisServiceUrl}
                  onChange={(e) => handleArcgisServiceChange(e.target.value)}
                  className="carica-arcgis-node__select"
                  disabled={isLoadingServices}>
                  <option value="">
                    {isLoadingServices ? 'Caricamento servizi...' : 'Seleziona servizio...'}
                  </option>
                  {arcgisServices.map((svc, idx) => (
                    <option key={idx} value={svc.url}>
                      {svc.name} {svc.folder ? `[${svc.folder}]` : ''}
                    </option>
                  ))}
                </select>
              </div>

              {/* Multi-layer: nessuna selezione layer singola — match per nome dal bundle */}
              {isMultiLayerUpstream && arcgisServiceUrl && (
                <div className="carica-arcgis-node__hint">
                  Tutti i layer del pacchetto verranno scritti sul servizio, abbinati per nome.
                </div>
              )}

              {/* Layer (solo mono-layer) */}
              {!isMultiLayerUpstream && arcgisServiceUrl && (
                <div className="carica-arcgis-node__section">
                  <label className="carica-arcgis-node__label">Layer Destinazione</label>
                  <select
                    value={arcgisLayerUrl}
                    onChange={(e) => handleArcgisLayerChange(e.target.value)}
                    className="carica-arcgis-node__select"
                    disabled={isLoadingLayers}>
                    <option value="">
                      {isLoadingLayers ? 'Caricamento layer...' : 'Seleziona layer...'}
                    </option>
                    {arcgisLayers.map((lyr, idx) => (
                      <option key={idx} value={lyr.url}>
                        {lyr.name} ({lyr.geometry_type || 'Tabella'})
                      </option>
                    ))}
                  </select>
                </div>
              )}

              {/* Schema info layer */}
              {arcgisLayerUrl && arcgisLayerSchema && (
                <div className="carica-arcgis-node__schema-info">
                  <span>{arcgisLayerSchema.geometry_type || 'Tabella'}</span>
                  <span>{arcgisLayerSchema.fields?.length || 0} campi</span>
                  {arcgisLayerSchema.feature_count != null && (
                    <span>{formatNumber(arcgisLayerSchema.feature_count)} elementi</span>
                  )}
                </div>
              )}

              {/* Colonna match per update/upsert/delete */}
              {['update', 'upsert', 'delete'].includes(arcgisOperation) && arcgisLayerUrl && (
                <div className="carica-arcgis-node__section">
                  <label className="carica-arcgis-node__label">Colonna di corrispondenza (chiave di aggiornamento)</label>
                  <select
                    value={arcgisMatchColumn}
                    onChange={(e) => handleUpdate('arcgis_match_column', e.target.value)}
                    className="carica-arcgis-node__select">
                    <option value="">Seleziona colonna chiave...</option>
                    {availableColumns.map((col, idx) => {
                      const name = col.name || col;
                      return <option key={idx} value={name}>{name}</option>;
                    })}
                  </select>
                  <div className="carica-arcgis-node__hint">
                    Il sistema cerca sul layer gli elementi con lo stesso valore di questa colonna e ne aggiorna i dati
                  </div>
                </div>
              )}
            </>
          )}

          {/* Errori ArcGIS */}
          {arcgisError && retryArcgis && (
            <button type="button" {...READ_ONLY_SAFE} className="nodrag list-state__retry" onClick={() => retryArcgis()}>Riprova</button>
          )}
          {arcgisError && (
            <div className="carica-arcgis-node__warning carica-arcgis-node__warning--err">
              {arcgisError}
            </div>
          )}

          {/* MAPPING COLONNE — non in multi-layer: lo schema/i campi di ogni
              layer arrivano dal descrittore del bundle, non si mappano qui. */}
          {!isMultiLayerUpstream && (arcgisLayerUrl || arcgisOperation === 'create_service') && (
            <div className="carica-arcgis-node__section">
              <div className="carica-arcgis-node__section-header"
                {...pressable(() => setShowMapping(!showMapping))}>
                <span>Mappatura delle colonne</span>
                <span className="carica-arcgis-node__toggle">{showMapping ? '▼' : '▶'}</span>
              </div>

              {showMapping && (
                <div className="carica-arcgis-node__mapping">
                  <div className="carica-arcgis-node__mapping-actions">
                    <button type="button" onClick={autoMapArcgis}
                      className="carica-arcgis-node__btn carica-arcgis-node__btn--secondary">
                      Mappatura automatica
                    </button>
                    <button type="button" onClick={handleAddMapping}
                      className="carica-arcgis-node__btn carica-arcgis-node__btn--secondary">
                      + Aggiungi
                    </button>
                  </div>

                  {columnMappings.length === 0 ? (
                    <div className="carica-arcgis-node__hint">
                      {arcgisOperation === 'create_service'
                        ? 'Nessuna mappatura: tutte le colonne verranno usate per creare lo schema'
                        : arcgisOperation === 'update'
                          ? 'Nessuna mappatura: aggiornerà tutte le colonne presenti in ingresso'
                          : 'Nessuna mappatura definita. Usa «Mappatura automatica» o aggiungi a mano.'
                      }
                    </div>
                  ) : (
                    <div className="carica-arcgis-node__mapping-list">
                      {columnMappings.map((mapping, idx) => (
                        <div key={idx} className="carica-arcgis-node__mapping-row">
                          <select
                            value={mapping.source || ''}
                            onChange={(e) => handleMappingChange(idx, 'source', e.target.value)}
                            className="carica-arcgis-node__mapping-select">
                            <option value="">Sorgente...</option>
                            {availableColumns.map((col, colIdx) => {
                              const originalName = col.name || col;
                              const displayName = col.alias ? col.alias + ' (' + originalName + ')' : originalName;
                              return (
                                <option key={colIdx} value={originalName}>{displayName}</option>
                              );
                            })}
                          </select>
                          <span className="carica-arcgis-node__mapping-arrow">&rarr;</span>
                          {arcgisOperation !== 'create_service' && arcgisDestFields.length > 0 ? (
                            <select
                              value={mapping.dest || ''}
                              onChange={(e) => handleMappingChange(idx, 'dest', e.target.value)}
                              className="carica-arcgis-node__mapping-select">
                              <option value="">Destinazione...</option>
                              {arcgisDestFields.map((f, fIdx) => (
                                <option key={fIdx} value={f}>{f}</option>
                              ))}
                            </select>
                          ) : (
                            <input
                              type="text"
                              value={mapping.dest || ''}
                              onChange={(e) => handleMappingChange(idx, 'dest', e.target.value)}
                              placeholder="Destinazione..."
                              className="carica-arcgis-node__mapping-input"
                            />
                          )}
                          <button type="button" onClick={() => handleRemoveMapping(idx)}
                            className="carica-arcgis-node__mapping-remove">
                            ×
                          </button>
                        </div>
                      ))}
                    </div>
                  )}

                  {arcgisOperation === 'update' && (
                    <div className="carica-arcgis-node__hint">
                      Le colonne non mappate non verranno modificate sul layer
                    </div>
                  )}
                </div>
              )}
            </div>
          )}

          {/* PREVIEW / STATUS */}
          {isValid && (
            <div className="carica-arcgis-node__preview">
              <div className="carica-arcgis-node__preview-item">
                <span className="carica-arcgis-node__preview-label">Operazione:</span>
                <span className="carica-arcgis-node__preview-value">
                  {arcgisOperations.find(o => o.value === arcgisOperation)?.label}
                </span>
              </div>
              {arcgisOperation === 'create_service' ? (
                <div className="carica-arcgis-node__preview-item">
                  <span className="carica-arcgis-node__preview-label">Nuovo:</span>
                  <span className="carica-arcgis-node__preview-value">{arcgisNewServiceName}</span>
                </div>
              ) : isMultiLayerUpstream ? (
                <div className="carica-arcgis-node__preview-item">
                  <span className="carica-arcgis-node__preview-label">Servizio:</span>
                  <span className="carica-arcgis-node__preview-value" title={arcgisServiceUrl}>
                    {arcgisServices.find(s => s.url === arcgisServiceUrl)?.name || '...'}
                  </span>
                </div>
              ) : (
                <div className="carica-arcgis-node__preview-item">
                  <span className="carica-arcgis-node__preview-label">Layer:</span>
                  <span className="carica-arcgis-node__preview-value" title={arcgisLayerUrl}>
                    {arcgisLayers.find(l => l.url === arcgisLayerUrl)?.name || '...'}
                  </span>
                </div>
              )}
              {availableColumns.length > 0 && (
                <div className="carica-arcgis-node__preview-item">
                  <span className="carica-arcgis-node__preview-label">Colonne:</span>
                  <span className="carica-arcgis-node__preview-value">
                    {columnMappings.length || availableColumns.length}
                  </span>
                </div>
              )}
            </div>
          )}


          <AutoRunNote className="carica-arcgis-node__auto-info" />
      </div>
    </div>
  );
});

CaricanArcGISNode.displayName = 'CaricanArcGISNode';

export default CaricanArcGISNode;
