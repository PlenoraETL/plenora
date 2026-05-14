import React from 'react';

/**
 * Card di un singolo progetto (Estrai / Manipola / Arricchisci).
 * Click sulla card apre il progetto; bottoni interni gestiscono
 * delete e schedule. `meta` e' una lista di {key, val} renderizzata
 * come grid di info secondarie.
 */
function ProjectCard({
  project,
  accentClass,
  modLabel,
  meta,
  scheduleCount,
  onOpen,
  onDelete,
  onSchedule,
}) {
  const status = project.last_status;

  const statusLabel = status === 'success' ? 'Completato'
    : status === 'running' ? 'In esecuzione'
    : status === 'error'   ? 'Errore'
    : 'Idle';
  const statusMod = status === 'success' ? 'pl__status--ok'
    : status === 'error' ? 'pl__status--err'
    : '';

  return (
    <div className="pl__card" onClick={onOpen}>
      <div className={`pl__card-accent ${accentClass}`} />
      <div className="pl__card-body">
        <div className="pl__card-header-row">
          <div className="pl__card-name">{project.name}</div>
          <span className="pl__card-mod">{modLabel}</span>
        </div>
        <div className="pl__card-desc">{project.description || 'Nessuna descrizione'}</div>
        <div className="pl__card-meta">
          {meta.map(({ key, val }) => (
            <div key={key}>
              <div className="pl__meta-key">{key}</div>
              <div className="pl__meta-val">{val}</div>
            </div>
          ))}
        </div>
        <div className="pl__card-footer">
          <span className={`pl__status ${statusMod}`}>{statusLabel}</span>
          <div className="pl__card-actions">
            {scheduleCount > 0 && (
              <button type="button" className="pl__btn pl__btn--ghost pl__btn--sm pl__btn--schedule"
                onClick={e => { e.stopPropagation(); onSchedule(); }}
                title="Schedule automatici"
              >
                ⏰ <span className="pl__schedule-badge">{scheduleCount}</span>
              </button>
            )}
            <button type="button" className="pl__btn pl__btn--ghost pl__btn--sm"
              onClick={e => { e.stopPropagation(); onDelete(); }}
            >🗑</button>
            <button type="button" className="pl__btn pl__btn--primary pl__btn--sm"
              onClick={e => { e.stopPropagation(); onOpen(); }}
            >Apri →</button>
          </div>
        </div>
      </div>
    </div>
  );
}

export default ProjectCard;
