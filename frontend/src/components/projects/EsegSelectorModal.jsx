import React, { useState, useEffect } from 'react';
import ModalWrapper, { ModalButton } from '../common/ModalWrapper';

/**
 * Modal di selezione progetto Arricchisci da pubblicare come Eseguibile.
 *
 * State locale per la search query (resettata ad ogni apertura). Il
 * progetto selezionato viene passato al parent via `onSelect(project)`;
 * il parent gestisce il flusso post-selezione (apertura
 * PublishEseguibileModal).
 */
function EsegSelectorModal({
  isOpen,
  arricchisciProjects = [],
  onClose,
  onSelect,
}) {
  const [search, setSearch] = useState('');

  useEffect(() => {
    if (isOpen) setSearch('');
  }, [isOpen]);

  if (!isOpen) return null;

  const filtered = search
    ? arricchisciProjects.filter(p =>
        (p.name || '').toLowerCase().includes(search.toLowerCase()),
      )
    : arricchisciProjects;

  return (
    <ModalWrapper
      isOpen={true}
      onClose={onClose}
      title="Pubblica come Eseguibile"
      icon="📢"
      subtitle="Seleziona un progetto Arricchisci da pubblicare come catena on-demand"
      size="md"
      footer={
        <ModalButton variant="secondary" onClick={onClose}>Annulla</ModalButton>
      }
    >
      <div className="pl__modal-field">
        <input
          type="text"
          className="pl__modal-input"
          placeholder="Cerca progetto Arricchisci..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          autoFocus
        />
      </div>

      <div className="pl__modal-list">
        {arricchisciProjects.length === 0 ? (
          <div className="pl__modal-empty">
            Nessun progetto Arricchisci disponibile.<br />
            <span className="pl__modal-empty-hint">Crea prima un progetto Arricchisci.</span>
          </div>
        ) : (
          filtered.map(p => (
            <div
              key={p.id}
              className="pl__modal-row"
              onClick={() => onSelect?.(p)}
            >
              <span className="pl__modal-row-icon">◈</span>
              <div className="pl__modal-row-body">
                <div className="pl__modal-row-name">{p.name}</div>
                {p.description && (
                  <div className="pl__modal-row-desc">{p.description}</div>
                )}
              </div>
              <span className="pl__modal-row-arrow">→</span>
            </div>
          ))
        )}
      </div>
    </ModalWrapper>
  );
}

export default EsegSelectorModal;
