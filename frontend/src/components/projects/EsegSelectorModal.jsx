import React, { useEffect } from 'react';
import ModalWrapper, { ModalButton } from '../common/ModalWrapper';
import SearchField, { SearchEmpty } from '../common/SearchField';
import { useSearch } from '../../hooks/useSearch';
import { pressable } from '../../utils/pressable';
import { projectType } from '../../utils/projectTypes';

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
  const search = useSearch();
  const clearSearch = search.clear;

  useEffect(() => {
    if (isOpen) clearSearch();
  }, [isOpen, clearSearch]);

  if (!isOpen) return null;

  const availableProjects = projects || arricchisciProjects;

  const filtered = search.filter(availableProjects, p => [p.name, p.description]);

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
        <SearchField
          search={search}
          label="Cerca progetto"
          shown={filtered.length}
          total={availableProjects.length}
          autoFocus
        />
      </div>

      <div className="pl__modal-list">
        {availableProjects.length === 0 ? (
          <div className="pl__modal-empty">
            Nessun progetto disponibile.
          </div>
        ) : filtered.length === 0 ? (
          <SearchEmpty search={search} shown={0} />
        ) : (
          filtered.map(p => (
            <div
              key={p.id}
              className="pl__modal-row"
              {...pressable(() => onSelect?.(p))}
            >
              <span className="pl__modal-row-icon">{projectType(p.project_type).icon}</span>
              <div className="pl__modal-row-body">
                <div className="pl__modal-row-name">{p.name}</div>
                <div className="pl__modal-row-desc">{projectType(p.project_type).label}</div>
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
