/**
 * FeatureServicePanel.jsx — Pannello sidebar per browse ArcGIS Feature Service
 * 
 * Posizione: /src/components/FeatureServicePanel.jsx
 * 
 * Funzionalità:
 * - Selezione connessione ArcGIS
 * - Browse servizi FeatureServer
 * - Browse layer con info geometria e conteggio
 * - Drag&drop layer sul canvas → crea FeatureLayerNode
 * - Modal creazione/test rapida connessione ArcGIS
 */

import React, { useState, useEffect, useCallback } from 'react';
import ModalWrapper, { ModalButton } from '../common/ModalWrapper';
import { humanizeError, requestErrorMessage } from '../../utils/errorMessages';
import {
  listFeatureConnections,
  createFeatureConnection,
  browseServices,
  browseLayers,
  getLayerSchema,
} from '../../api/features';
import './FeatureServicePanel.css';

const GEOM_ICONS = {
  'geometry:point': '📍',
  'geometry:multipoint': '📍',
  'geometry:linestring': '〰️',
  'geometry:multilinestring': '〰️',
  'geometry:polygon': '⬡',
  'geometry:multipolygon': '⬡',
  'geometry:envelope': '▢',
  'geometry': '🌐',
};

function getGeomIcon(geomType) {
  if (!geomType) return '📋';
  const key = geomType.toLowerCase();
  return GEOM_ICONS[key] || GEOM_ICONS['geometry'] || '🌐';
}

function getGeomLabel(geomType) {
  if (!geomType) return 'Tabella';
  return geomType.replace('geometry:', '').replace('geometry', 'geometria');
}

// canCreateConnection = can.createConnection(user) dalla pagina (EstraiEditor):
// la '+' di una nuova connessione ArcGIS si mostra solo a chi puo' crearla.
function FeatureServicePanel({ onLayerReady, layersInCanvas = new Set(), lockedConnection = null, canCreateConnection = false }) {
  // State navigazione: connections → services → layers
  const [view, setView] = useState(lockedConnection ? 'services' : 'connections'); // connections | services | layers
  
  // Connessioni
  const [connections, setConnections] = useState([]);
  const [selectedConnection, setSelectedConnection] = useState(lockedConnection);
  const [loadingConnections, setLoadingConnections] = useState(false);
  
  // Servizi
  const [services, setServices] = useState([]);
  const [loadingServices, setLoadingServices] = useState(false);
  
  // Layer
  const [layers, setLayers] = useState([]);
  const [selectedService, setSelectedService] = useState(null);
  const [loadingLayers, setLoadingLayers] = useState(false);
  const [layerSchemas, setLayerSchemas] = useState({});
  
  // Search
  const [searchQuery, setSearchQuery] = useState('');
  
  // Modal nuova connessione
  const [showNewConn, setShowNewConn] = useState(false);
  const [newConnForm, setNewConnForm] = useState({
    name: '',
    server_url: '',
    username: '',
    password: '',
    auth_type: 'public',
  });
  const [newConnTesting, setNewConnTesting] = useState(false);
  const [newConnError, setNewConnError] = useState('');
  
  // Error
  const [error, setError] = useState('');

  useEffect(() => {
    if (lockedConnection) {
      // Connessione bloccata dal progetto: carica servizi direttamente
      handleSelectConnection(lockedConnection);
    } else {
      loadConnections();
    }
  }, []);

  const loadConnections = async () => {
    setLoadingConnections(true);
    setError('');
    try {
      const data = await listFeatureConnections();
      setConnections(data.connections || data || []);
    } catch (err) {
      setError(humanizeError(err).message);
    } finally {
      setLoadingConnections(false);
    }
  };

  const handleSelectConnection = async (conn) => {
    setSelectedConnection(conn);
    setView('services');
    setLoadingServices(true);
    setError('');
    setSearchQuery('');
    try {
      const data = await browseServices(conn.id);
      setServices(data.services || data || []);
    } catch (err) {
      setError(humanizeError(err).message);
      setServices([]);
    } finally {
      setLoadingServices(false);
    }
  };

  const handleSelectService = async (service) => {
    setSelectedService(service);
    setView('layers');
    setLoadingLayers(true);
    setError('');
    setSearchQuery('');
    try {
      const data = await browseLayers(selectedConnection.id, service.url);
      setLayers(data.layers || data || []);
    } catch (err) {
      setError(humanizeError(err).message);
      setLayers([]);
    } finally {
      setLoadingLayers(false);
    }
  };

  const loadLayerSchema = useCallback(async (layer) => {
    const cacheKey = layer.url;
    if (layerSchemas[cacheKey]) return layerSchemas[cacheKey];
    
    try {
      const schema = await getLayerSchema(selectedConnection.id, layer.url);
      setLayerSchemas(prev => ({ ...prev, [cacheKey]: schema }));
      return schema;
    } catch (err) {
      console.error('Errore schema layer:', err);
      return null;
    }
  }, [selectedConnection, layerSchemas]);

  const handleDragStart = useCallback((e, layer) => {
    const dragData = {
      type: 'featureLayer',
      connectionId: selectedConnection.id,
      connectionName: selectedConnection.name,
      serviceName: selectedService?.name || '',
      layerId: layer.id,
      layerName: layer.name,
      layerUrl: layer.url,
      geometryType: layer.geometry_type || '',
      featureCount: layer.feature_count || null,
    };
    e.dataTransfer.setData('featureLayerData', JSON.stringify(dragData));
    e.dataTransfer.effectAllowed = 'move';
  }, [selectedConnection, selectedService]);

  const handleCreateConnection = async () => {
    if (!newConnForm.name.trim() || !newConnForm.server_url.trim()) {
      setNewConnError('Nome e URL sono obbligatori');
      return;
    }
    setNewConnTesting(true);
    setNewConnError('');
    try {
      const created = await createFeatureConnection(newConnForm);
      setConnections(prev => [...prev, created]);
      setShowNewConn(false);
      setNewConnForm({ name: '', server_url: '', username: '', password: '', auth_type: 'public' });
    } catch (err) {
      setNewConnError(requestErrorMessage(err));
    } finally {
      setNewConnTesting(false);
    }
  };

  const handleBack = () => {
    setError('');
    setSearchQuery('');
    if (view === 'layers') {
      setView('services');
      setLayers([]);
      setSelectedService(null);
    } else if (view === 'services' && !lockedConnection) {
      setView('connections');
      setServices([]);
      setSelectedConnection(null);
    }
  };

  const filterItems = (items, nameKey = 'name') => {
    if (!searchQuery) return items;
    const q = searchQuery.toLowerCase();
    return items.filter(item => 
      (item[nameKey] || '').toLowerCase().includes(q)
    );
  };

  return (
    <div className="fs-panel">
      {/* Header con breadcrumb */}
      <div className="fs-panel__header">
        {view !== 'connections' && !(view === 'services' && lockedConnection) && (
          <button type="button" className="fs-panel__back-btn" onClick={handleBack}>
            ←
          </button>
        )}
        <div className="fs-panel__breadcrumb">
          {view === 'connections' && (
            <span className="fs-panel__breadcrumb-item">🌐 Connessioni ArcGIS</span>
          )}
          {view === 'services' && (
            <>
              {!lockedConnection && (
                <>
                  <span 
                    className="fs-panel__breadcrumb-link" 
                    onClick={() => { setView('connections'); setSelectedConnection(null); }}
                  >
                    Connessioni
                  </span>
                  <span className="fs-panel__breadcrumb-sep">›</span>
                </>
              )}
              <span className="fs-panel__breadcrumb-item">
                🌐 {selectedConnection?.name}
              </span>
            </>
          )}
          {view === 'layers' && (
            <>
              {!lockedConnection && (
                <>
                  <span 
                    className="fs-panel__breadcrumb-link"
                    onClick={() => { setView('connections'); setSelectedConnection(null); }}
                  >
                    Conn.
                  </span>
                  <span className="fs-panel__breadcrumb-sep">›</span>
                </>
              )}
              <span 
                className="fs-panel__breadcrumb-link"
                onClick={() => { setView('services'); setSelectedService(null); }}
              >
                {selectedConnection?.name}
              </span>
              <span className="fs-panel__breadcrumb-sep">›</span>
              <span className="fs-panel__breadcrumb-item">{selectedService?.name}</span>
            </>
          )}
        </div>
      </div>

      {/* Search + action */}
      <div className="fs-panel__toolbar">
        <input
          type="text"
          className="fs-panel__search"
          placeholder={
            view === 'connections' ? '🔍 Cerca connessione...' :
            view === 'services' ? '🔍 Cerca servizio...' :
            '🔍 Cerca layer...'
          }
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
        />
        {view === 'connections' && canCreateConnection && (
          <button type="button" 
            className="fs-panel__add-btn"
            onClick={() => setShowNewConn(true)}
            title="Nuova connessione ArcGIS"
          >
            +
          </button>
        )}
      </div>

      {error && (
        <div className="fs-panel__error">
          ⚠️ {error}
        </div>
      )}

      {/* Contenuto */}
      <div className="fs-panel__list">
        {view === 'connections' && (
          loadingConnections ? (
            <div className="fs-panel__loading">Caricamento connessioni...</div>
          ) : connections.length === 0 ? (
            <div className="fs-panel__empty">
              <p>Nessuna connessione ArcGIS configurata</p>
              {canCreateConnection ? (
                <button type="button"
                  className="fs-panel__empty-btn"
                  onClick={() => setShowNewConn(true)}
                >
                  + Crea connessione
                </button>
              ) : (
                <p>Chiedi a un amministratore di configurarne una.</p>
              )}
            </div>
          ) : (
            filterItems(connections).map(conn => (
              <div
                key={conn.id}
                className="fs-panel__item fs-panel__item--connection"
                onClick={() => handleSelectConnection(conn)}
              >
                <span className="fs-panel__item-icon">🌐</span>
                <div className="fs-panel__item-info">
                  <span className="fs-panel__item-name">{conn.name}</span>
                  <span className="fs-panel__item-detail">
                    {conn.auth_type === 'public' ? '🔓 Pubblico' : '🔐 Autenticato'}
                  </span>
                </div>
                <span className="fs-panel__item-arrow">›</span>
              </div>
            ))
          )
        )}

        {view === 'services' && (
          loadingServices ? (
            <div className="fs-panel__loading">Caricamento servizi...</div>
          ) : services.length === 0 ? (
            <div className="fs-panel__empty">
              <p>Nessun FeatureServer trovato</p>
            </div>
          ) : (
            filterItems(services).map((svc, idx) => (
              <div
                key={`${svc.name}-${idx}`}
                className="fs-panel__item fs-panel__item--service"
                onClick={() => handleSelectService(svc)}
              >
                <span className="fs-panel__item-icon">📦</span>
                <div className="fs-panel__item-info">
                  <span className="fs-panel__item-name">{svc.name}</span>
                  {svc.folder && (
                    <span className="fs-panel__item-detail">📁 {svc.folder}</span>
                  )}
                </div>
                <span className="fs-panel__item-arrow">›</span>
              </div>
            ))
          )
        )}

        {view === 'layers' && (
          <>
            {loadingLayers ? (
              <div className="fs-panel__loading">Caricamento layer...</div>
            ) : layers.length === 0 ? (
              <div className="fs-panel__empty">
                <p>Nessun layer trovato</p>
              </div>
            ) : (
              <>
                <div className="fs-panel__hint">
                  Trascina un layer nel canvas →
                </div>
                {filterItems(layers).map(layer => {
                  const isInCanvas = layersInCanvas.has(layer.url);
                  return (
                    <div
                      key={layer.id}
                      className={`fs-panel__item fs-panel__item--layer ${isInCanvas ? 'fs-panel__item--used' : ''}`}
                      draggable={!isInCanvas}
                      onDragStart={(e) => handleDragStart(e, layer)}
                    >
                      <span className="fs-panel__item-icon">
                        {getGeomIcon(layer.geometry_type)}
                      </span>
                      <div className="fs-panel__item-info">
                        <span className="fs-panel__item-name">{layer.name}</span>
                        <span className="fs-panel__item-detail">
                          {getGeomLabel(layer.geometry_type)}
                          {layer.feature_count != null && ` · ${layer.feature_count.toLocaleString()} feat.`}
                        </span>
                      </div>
                      {isInCanvas ? (
                        <span className="fs-panel__item-badge">✓</span>
                      ) : (
                        <span className="fs-panel__item-drag-hint">⠿</span>
                      )}
                    </div>
                  );
                })}
              </>
            )}
          </>
        )}
      </div>

      {/* MODAL NUOVA CONNESSIONE */}
      {showNewConn && (
        <ModalWrapper
          isOpen={true}
          onClose={() => setShowNewConn(false)}
          title="Nuova connessione ArcGIS"
          icon="🌐"
          size="md"
          busy={newConnTesting}
          footer={
            <>
              <ModalButton variant="secondary" onClick={() => setShowNewConn(false)} disabled={newConnTesting}>
                Annulla
              </ModalButton>
              <ModalButton variant="primary" onClick={handleCreateConnection} loading={newConnTesting}>
                ✓ Crea
              </ModalButton>
            </>
          }
        >
            <div className="fs-panel__form-group">
              <label>Nome *</label>
              <input
                type="text"
                value={newConnForm.name}
                onChange={e => setNewConnForm(f => ({ ...f, name: e.target.value }))}
                placeholder="Es. ArcGIS Comune di Roma"
              />
            </div>

            <div className="fs-panel__form-group">
              <label>URL server *</label>
              <input
                type="text"
                value={newConnForm.server_url}
                onChange={e => setNewConnForm(f => ({ ...f, server_url: e.target.value }))}
                placeholder="https://server.arcgis.com/arcgis/rest/services"
              />
            </div>

            <div className="fs-panel__form-group">
              <label>Autenticazione</label>
              <select
                value={newConnForm.auth_type}
                onChange={e => setNewConnForm(f => ({ ...f, auth_type: e.target.value }))}
              >
                <option value="public">Pubblico (nessuna)</option>
                <option value="token">Token (nome utente e password)</option>
              </select>
            </div>

            {newConnForm.auth_type === 'token' && (
              <>
                <div className="fs-panel__form-group">
                  <label>Nome utente</label>
                  <input
                    type="text"
                    value={newConnForm.username}
                    onChange={e => setNewConnForm(f => ({ ...f, username: e.target.value }))}
                  />
                </div>
                <div className="fs-panel__form-group">
                  <label>Password</label>
                  <input
                    type="password"
                    value={newConnForm.password}
                    onChange={e => setNewConnForm(f => ({ ...f, password: e.target.value }))}
                  />
                </div>
              </>
            )}

            {newConnError && (
              <div className="fs-panel__form-error">❌ {newConnError}</div>
            )}
        </ModalWrapper>
      )}
    </div>
  );
}

export default FeatureServicePanel;
