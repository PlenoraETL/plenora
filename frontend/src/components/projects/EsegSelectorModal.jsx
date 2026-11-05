import React, { useState, useEffect } from 'react';
import ModalWrapper, { ModalButton } from '../common/ModalWrapper';
import { pressable } from '../../utils/pressable';

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
  projects,
  arricchisciProjects = [],
  onClose,
  onSelect,
}) {
  const [search, setSearch] = useState('');

  useEffect(() => {
    if (isOpen) setSearch('');
  }, [isOpen]);

  if (!isOpen) return null;

  const availableProjects = projects || arricchisciProjects;

  const filtered = search
    ? availableProjects.filter(p =>
        (p.name || '').toLowerCase().includes(search.toLowerCase()),
      )
    : availableProjects;

  return (
    <ModalWrapper
      isOpen={true}
      onClose={onClose}
      title="Pubblica come Eseguibile"
      icon="📢"
      subtitle="Seleziona un progetto Estrai, Manipola o Arricchisci"
      size="md"
      footer={
        <ModalButton variant="secondary" onClick={onClose}>Annulla</ModalButton>
      }
    >
      <div className="pl__modal-field">
        <input
          type="text"
          className="pl__modal-input"
          placeholder="Cerca progetto..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          autoFocus
        />
      </div>

      <div className="pl__modal-list">
        {availableProjects.length === 0 ? (
          <div className="pl__modal-empty">
            Nessun progetto disponibile.
          </div>
        ) : (
          filtered.map(p => (
            <div
              key={p.id}
              className="pl__modal-row"
              {...pressable(() => onSelect?.(p))}
            >
              <span className="pl__modal-row-icon">◈</span>
              <div className="pl__modal-row-body">
                <div className="pl__modal-row-name">{p.name}</div>
                <div className="pl__modal-row-desc">{String(p.project_type || '').toUpperCase()}</div>
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
