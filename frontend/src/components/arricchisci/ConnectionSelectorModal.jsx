/**
 * ConnectionSelectorModal.jsx — Selezione connessione API salvata
 *
 * Modal generico per selezionare un connettore API esistente da quelli
 * configurati in ApiConnectionManager. Usato dall'editor Arricchisci
 * quando si aggiunge un nuovo nodo ApiEnrich senza creare un nuovo connettore.
 */
import React, { useState, useEffect, useMemo } from 'react';
import { useResource } from '../../hooks/useResource';
import { listApiConnections } from '../../api/apiConnections';
import ModalWrapper, { ModalButton } from '../common/ModalWrapper';
import { humanizeError } from '../../utils/errorMessages';
import './ConnectionSelectorModal.css';

/**
 * ConnectionSelectorModal - REFACTORED with ModalWrapper
 * Modal generico per selezionare connessioni API salvate
 */
function ConnectionSelectorModal({ 
  isOpen, 
  onClose, 
  onSelect, 
  category,
  title,
  icon,
  emptyMessage,
  emptyHint,
  createLink
}) {
  // Fase 5 T2: elenco dall'hook comune (id di richiesta per categoria).
  const resource = useResource(async () => (await listApiConnections({ connection_type: category })).api_connections || [], {
    key: ['api-connections', category || null],
    enabled: Boolean(isOpen),
    errorMessage: (err) => humanizeError(err).message,
  });
  const connections = resource.data || [];
  const loading = resource.initialLoading;
  const error = resource.error;
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedId, setSelectedId] = useState(null);


  // Reset quando si chiude
  useEffect(() => {
    if (!isOpen) {
      setSearchQuery('');
      setSelectedId(null);
    }
  }, [isOpen]);

  // Filtra connessioni
  const filteredConnections = useMemo(() => {
    if (!searchQuery.trim()) return connections;
    
    const query = searchQuery.toLowerCase();
    return connections.filter(c => 
      c.name?.toLowerCase().includes(query) ||
      c.description?.toLowerCase().includes(query) ||
      c.endpoint?.url?.toLowerCase().includes(query)
    );
  }, [connections, searchQuery]);

  // Seleziona e conferma
  const handleConfirm = () => {
    const selected = connections.find(c => c.id === selectedId);
    if (selected) {
      onSelect(selected);
      onClose();
    }
  };

  // Helper per icona tipo API
  const getApiTypeIcon = (apiType) => {
    return apiType === 'generator' ? '📥' : '🔄';
  };

  // Helper per icona metodo HTTP
  const getMethodBadgeClass = (method) => {
    const m = (method || 'GET').toUpperCase();
    return `connection-selector__method connection-selector__method--${m.toLowerCase()}`;
  };

  // Footer
  const footer = (
    <>
      <div className="connection-selector__footer-hint">
        💡 Doppio clic per selezionare rapidamente
      </div>
      <div className="connection-selector__footer-actions">
        <ModalButton variant="secondary" onClick={onClose}>
          Annulla
        </ModalButton>
        <ModalButton 
          variant="primary" 
          onClick={handleConfirm}
          disabled={!selectedId}
        >
          ✔ Usa questa connessione
        </ModalButton>
      </div>
    </>
  );

  return (
    <ModalWrapper
      isOpen={isOpen}
      onClose={onClose}
      title={title}
      icon={icon}
      size="lg"
      variant="arricchisci"
      footer={footer}
      loading={loading}
      loadingText="Caricamento connessioni..."
    >
      {/* Search */}
      <div className="connection-selector__toolbar">
        <div className="connection-selector__search">
          <span className="connection-selector__search-icon">🔍</span>
          <input
            type="text"
            value={searchQuery}
            onChange={e => setSearchQuery(e.target.value)}
            placeholder="Cerca per nome, descrizione o URL..."
            className="connection-selector__search-input"
          />
          {searchQuery && (
            <button type="button" 
              className="connection-selector__search-clear"
              onClick={() => setSearchQuery('')}
            >
              ✕
            </button>
          )}
        </div>
        <div className="connection-selector__count">
          {filteredConnections.length} / {connections.length}
        </div>
      </div>

      {/* Content */}
      <div className="connection-selector__content">
        {error ? (
          <div className="connection-selector__error">
            <span>❌</span>
            <p>Errore: {error}</p>
            <button type="button" 
              onClick={() => window.location.reload()} 
              className="connection-selector__retry-btn"
            >
              🔄 Riprova
            </button>
          </div>
        ) : filteredConnections.length === 0 ? (
          <div className="connection-selector__empty">
            <span className="connection-selector__empty-icon">🔭</span>
            <p className="connection-selector__empty-message">
              {searchQuery ? `Nessun risultato per "${searchQuery}"` : emptyMessage}
            </p>
            {!searchQuery && emptyHint && (
              <p className="connection-selector__empty-hint">{emptyHint}</p>
            )}
            {!searchQuery && createLink && (
              <a href={createLink} className="connection-selector__create-link">
                ➕ Crea nuova connessione
              </a>
            )}
          </div>
        ) : (
          <div className="connection-selector__list">
            {filteredConnections.map(connection => (
              <div
                key={connection.id}
                className={`connection-selector__item ${selectedId === connection.id ? 'connection-selector__item--selected' : ''}`}
                onClick={() => setSelectedId(connection.id)}
                onDoubleClick={() => {
                  setSelectedId(connection.id);
                  handleConfirm();
                }}
              >
                <div className="connection-selector__item-header">
                  <span className="connection-selector__item-icon">
                    {getApiTypeIcon(connection.api_type)}
                  </span>
                  <div className="connection-selector__item-info">
                    <h4 className="connection-selector__item-name">{connection.name}</h4>
                    {connection.description && (
                      <p className="connection-selector__item-desc">{connection.description}</p>
                    )}
                  </div>
                  <div className="connection-selector__item-badges">
                    <span className={getMethodBadgeClass(connection.endpoint?.method)}>
                      {connection.endpoint?.method || 'GET'}
                    </span>
                    <span className={`connection-selector__type ${connection.api_type === 'generator' ? 'connection-selector__type--generator' : ''}`}>
                      {connection.api_type === 'generator' ? 'Generatore' : 'Arricchitore'}
                    </span>
                  </div>
                </div>

                <div className="connection-selector__item-details">
                  {connection.endpoint?.url && (
                    <div className="connection-selector__item-url">
                      <span className="connection-selector__label">URL:</span>
                      <code>{connection.endpoint.url}</code>
                    </div>
                  )}
                  <div className="connection-selector__item-meta">
                    {connection.parameters?.length > 0 && (
                      <span className="connection-selector__meta-item">
                        📥 {connection.parameters.length} parametri
                      </span>
                    )}
                    {connection.output_mapping?.length > 0 && (
                      <span className="connection-selector__meta-item">
                        📤 {connection.output_mapping.length} campi in uscita
                      </span>
                    )}
                    {connection.auth?.type !== 'none' && (
                      <span className="connection-selector__meta-item">
                        🔐 {connection.auth.type}
                      </span>
                    )}
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </ModalWrapper>
  );
}

export default ConnectionSelectorModal;