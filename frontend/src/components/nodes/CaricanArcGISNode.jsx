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
import React, { useState, useEffect, memo } from 'react';
import { Handle, Position } from 'reactflow';
import useCanvasStore from '../../store/useCanvasStore';
import {
  listFeatureConnections,
  browseServices,
  browseLayers,
  getLayerSchema,
  listFolders,
} from '../../api/features';
import './CaricanArcGISNode.css';

const CaricanArcGISNode = memo(({ data, id }) => {
  const [isExpanded, setIsExpanded] = useState(true);
  const [showMapping, setShowMapping] = useState(false);

  // State ArcGIS
  const [arcgisConnections, setArcgisConnections] = useState([]);
  const [isLoadingArcgisConns, setIsLoadingArcgisConns] = useState(false);
  const [arcgisServices, setArcgisServices] = useState([]);
  const [isLoadingServices, setIsLoadingServices] = useState(false);
  const [arcgisLayers, setArcgisLayers] = useState([]);
  const [isLoadingLayers, setIsLoadingLayers] = useState(false);
  const [arcgisLayerSchema, setArcgisLayerSchema] = useState(null);
  const [isLoadingSchema, setIsLoadingSchema] = useState(false);
  const [arcgisError, setArcgisError] = useState('');
  const [arcgisFolders, setArcgisFolders] = useState([]);
  const [isLoadingFolders, setIsLoadingFolders] = useState(false);

  const arcgisConnectionId = data.arcgis_connection_id || '';
  const arcgisServiceUrl = data.arcgis_service_url || '';
  const arcgisLayerUrl = data.arcgis_layer_url || '';
  const arcgisOperation = data.arcgis_operation || 'add';
  const arcgisNewServiceName = data.arcgis_new_service_name || '';
  const arcgisNewLayerName = data.arcgis_new_layer_name || 'Layer 0';
  const arcgisFolderId = data.arcgis_folder_id || '';
  const arcgisMatchColumn = data.arcgis_match_column || '';
  const columnMappings = data.column_mappings || [];
  const availableColumns = data.availableColumns || [];
  const executeResult = data.executeResult || null;

  // RB.7.C: backend (RB.7.B) marca data.access_denied=true quando la
  // connessione ArcGIS riferita dal canvas non e' visibile al gruppo del
  // progetto (canvas storico cross-tenant, post rebrand 2026-04-18).
  // Non rimuoviamo il nodo: l'utente deve poterlo correggere o eliminare
  // manualmente. Disabilitiamo le chiamate API (eviterebbero 404 fail-loud
  // a vuoto su listFolders/browseServices/...) e marchiamo isValid=false.
  const isAccessDenied = !!data.access_denied;
  const accessDeniedReason = data.access_denied_reason || '';

  const arcgisOperations = [
    { value: 'add', label: '+ Add Features', desc: 'Aggiungi features al layer esistente' },
    { value: 'update', label: 'Update Features', desc: 'Aggiorna features esistenti per colonna chiave' },
    { value: 'upsert', label: 'Upsert Features', desc: 'Aggiorna se esiste, inserisci se nuovo' },
    { value: 'delete', label: 'Delete Features', desc: 'Elimina features per colonna chiave' },
    { value: 'create_service', label: 'Crea Servizio', desc: 'Crea nuovo Feature Service + carica dati' },
  ];

  useEffect(() => {
    loadArcgisConnections();
  }, []);

  useEffect(() => {
    // RB.7.C: skip API calls quando la connection e' marcata access_denied
    // dal backend (RB.7.B). Pulisce stato derivato per evitare flash di
    // dati stantii.
    if (isAccessDenied) {
      setArcgisServices([]); setArcgisLayers([]); setArcgisLayerSchema(null);
      return;
    }
    if (arcgisConnectionId) {
      loadArcgisServices(arcgisConnectionId);
    } else {
      setArcgisServices([]); setArcgisLayers([]); setArcgisLayerSchema(null);
    }
  }, [arcgisConnectionId, isAccessDenied]);

  useEffect(() => {
    if (isAccessDenied) {
      setArcgisLayers([]); setArcgisLayerSchema(null);
      return;
    }
    if (arcgisConnectionId && arcgisServiceUrl && arcgisOperation !== 'create_service') {
      loadArcgisLayers(arcgisConnectionId, arcgisServiceUrl);
    } else {
      setArcgisLayers([]); setArcgisLayerSchema(null);
    }
  }, [arcgisConnectionId, arcgisServiceUrl, arcgisOperation, isAccessDenied]);

  // Carica cartelle quando connessione selezionata e operazione create_service
  useEffect(() => {
    if (isAccessDenied) {
      setArcgisFolders([]);
      return;
    }
    if (arcgisConnectionId && arcgisOperation === 'create_service') {
      loadArcgisFolders(arcgisConnectionId);
    } else {
      setArcgisFolders([]);
    }
  }, [arcgisConnectionId, arcgisOperation, isAccessDenied]);

  useEffect(() => {
    if (isAccessDenied) {
      setArcgisLayerSchema(null);
      return;
    }
    if (arcgisConnectionId && arcgisLayerUrl && arcgisOperation !== 'create_service') {
      loadArcgisLayerSchema(arcgisConnectionId, arcgisLayerUrl);
    } else {
      setArcgisLayerSchema(null);
    }
  }, [arcgisConnectionId, arcgisLayerUrl, arcgisOperation, isAccessDenied]);

  const loadArcgisConnections = async () => {
    setIsLoadingArcgisConns(true);
    try {
      const res = await listFeatureConnections();
      setArcgisConnections(Array.isArray(res) ? res : res.connections || []);
    } catch (error) {
      console.error('Errore caricamento connessioni ArcGIS:', error);
    } finally {
      setIsLoadingArcgisConns(false);
    }
  };

  const loadArcgisServices = async (connId) => {
    setIsLoadingServices(true);
    setArcgisError('');
    try {
      const res = await browseServices(connId);
      setArcgisServices(Array.isArray(res) ? res : res.services || []);
    } catch (error) {
      setArcgisError(`Errore servizi: ${error.message}`);
      setArcgisServices([]);
    } finally {
      setIsLoadingServices(false);
    }
  };

  const loadArcgisLayers = async (connId, serviceUrl) => {
    setIsLoadingLayers(true);
    setArcgisError('');
    try {
      const res = await browseLayers(connId, serviceUrl);
      setArcgisLayers(res.layers || res || []);
    } catch (error) {
      setArcgisError(`Errore layer: ${error.message}`);
      setArcgisLayers([]);
    } finally {
      setIsLoadingLayers(false);
    }
  };

  const loadArcgisFolders = async (connId) => {
    setIsLoadingFolders(true);
    try {
      const res = await listFolders(connId);
      setArcgisFolders(res.folders || res || []);
    } catch (error) {
      console.error('Errore caricamento cartelle ArcGIS:', error);
      setArcgisFolders([{ id: '', title: '(root)' }]);
    } finally {
      setIsLoadingFolders(false);
    }
  };

  const loadArcgisLayerSchema = async (connId, layerUrl) => {
    setIsLoadingSchema(true);
    try {
      const res = await getLayerSchema(connId, layerUrl);
      setArcgisLayerSchema(res);
    } catch (error) {
      console.error('Errore schema layer:', error);
      setArcgisLayerSchema(null);
    } finally {
      setIsLoadingSchema(false);
    }
  };

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
      : (arcgisConnectionId && arcgisLayerUrl && matchColumnValid)
  );

  const arcgisDestFields = (arcgisLayerSchema?.fields || [])
    .filter(f => f.type !== 'esriFieldTypeOID' && f.type !== 'esriFieldTypeGlobalID')
    .map(f => f.name);

  return (
    <div className={`carica-arcgis-node ${!isValid ? 'carica-arcgis-node--invalid' : ''}`}>
      <Handle
        type="target"
        position={Position.Left}
        className="carica-arcgis-node__handle carica-arcgis-node__handle--input"
      />

      <div className="carica-arcgis-node__header" role="button" tabIndex={0}
        onClick={() => setIsExpanded(!isExpanded)}
        onKeyDown={e => (e.key === 'Enter' || e.key === ' ') && setIsExpanded(v => !v)}>
        <div className="carica-arcgis-node__header-left">
          <span className="carica-arcgis-node__icon">🌐</span>
          <span className="carica-arcgis-node__title">CARICA su ArcGIS</span>
        </div>
        <div className="carica-arcgis-node__header-right">
          <span className="carica-arcgis-node__expand-icon">
            {isExpanded ? '▼' : '▶'}
          </span>
          <button type="button"
            onClick={(e) => { e.stopPropagation(); useCanvasStore.getState().deleteNode(id); }}
            className="carica-arcgis-node__delete-btn"
            title="Elimina nodo">
            ×
          </button>
        </div>
      </div>

      {/* Body */}
      {isExpanded && (
        <div className="carica-arcgis-node__body">

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
              <div className="carica-arcgis-node__hint">
                Verra creato un nuovo Feature Service con schema derivato dai dati della pipeline
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

              {/* Layer */}
              {arcgisServiceUrl && (
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
                        {lyr.name} ({lyr.geometry_type || 'Table'})
                      </option>
                    ))}
                  </select>
                </div>
              )}

              {/* Schema info layer */}
              {arcgisLayerUrl && arcgisLayerSchema && (
                <div className="carica-arcgis-node__schema-info">
                  <span>{arcgisLayerSchema.geometry_type || 'Table'}</span>
                  <span>{arcgisLayerSchema.fields?.length || 0} campi</span>
                  {arcgisLayerSchema.feature_count != null && (
                    <span>{arcgisLayerSchema.feature_count.toLocaleString()} features</span>
                  )}
                </div>
              )}

              {/* Colonna match per update/upsert/delete */}
              {['update', 'upsert', 'delete'].includes(arcgisOperation) && arcgisLayerUrl && (
                <div className="carica-arcgis-node__section">
                  <label className="carica-arcgis-node__label">Colonna Match (chiave update)</label>
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
                    Il sistema cerca le features sul layer con lo stesso valore di questa colonna e ne aggiorna i dati
                  </div>
                </div>
              )}
            </>
          )}

          {/* Errori ArcGIS */}
          {arcgisError && (
            <div className="carica-arcgis-node__warning carica-arcgis-node__warning--err">
              {arcgisError}
            </div>
          )}

          {/* MAPPING COLONNE */}
          {(arcgisLayerUrl || arcgisOperation === 'create_service') && (
            <div className="carica-arcgis-node__section">
              <div className="carica-arcgis-node__section-header"
                onClick={() => setShowMapping(!showMapping)}>
                <span>Mapping Colonne</span>
                <span className="carica-arcgis-node__toggle">{showMapping ? '▼' : '▶'}</span>
              </div>

              {showMapping && (
                <div className="carica-arcgis-node__mapping">
                  <div className="carica-arcgis-node__mapping-actions">
                    <button type="button" onClick={autoMapArcgis}
                      className="carica-arcgis-node__btn carica-arcgis-node__btn--secondary">
                      Auto-mapping
                    </button>
                    <button type="button" onClick={handleAddMapping}
                      className="carica-arcgis-node__btn carica-arcgis-node__btn--secondary">
                      + Aggiungi
                    </button>
                  </div>

                  {columnMappings.length === 0 ? (
                    <div className="carica-arcgis-node__hint">
                      {arcgisOperation === 'create_service'
                        ? 'Nessun mapping — tutte le colonne verranno usate per creare lo schema'
                        : arcgisOperation === 'update'
                          ? 'Nessun mapping — aggiornera tutte le colonne presenti nell\'input'
                          : 'Nessun mapping definito. Usa "Auto-mapping" o aggiungi manualmente.'
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

          {/* RISULTATO */}
          {executeResult && (
            <div className={`carica-arcgis-node__result ${executeResult.success ? 'carica-arcgis-node__result--success' : 'carica-arcgis-node__result--error'}`}>
              <span>{executeResult.success ? 'OK' : 'Errore'}</span>
              <span>{executeResult.message || (executeResult.success ? 'Completato!' : 'Errore')}</span>
              {executeResult.count != null && (
                <span className="carica-arcgis-node__result-count">{executeResult.count} features</span>
              )}
              {executeResult.service_url && (
                <span className="carica-arcgis-node__result-url" title={executeResult.service_url}>
                  Servizio creato
                </span>
              )}
            </div>
          )}

          <div className="carica-arcgis-node__auto-info">
            Eseguito automaticamente con <strong>Esegui Query</strong>
          </div>
        </div>
      )}
    </div>
  );
});

CaricanArcGISNode.displayName = 'CaricanArcGISNode';

export default CaricanArcGISNode;
