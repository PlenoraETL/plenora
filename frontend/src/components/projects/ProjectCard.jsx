import React, { useState } from 'react';

/**
 * Card di un singolo progetto (Estrai / Manipola / Arricchisci).
 * Click sulla card apre il progetto; bottoni interni gestiscono
 * delete e schedule. `meta` e' una lista di {key, val} renderizzata
 * come grid di info secondarie.
 */
function ProjectCard({
  project,
  runStatus,
  accentClass,
  modLabel,
  meta,
  scheduleCount,
  onOpen,
  onDelete,
  onClone,
  onRename,
  onSchedule,
}) {
  const [editing, setEditing] = useState(false);
  const [tempName, setTempName] = useState(project.name);

  const startRename = (e) => {
    e.stopPropagation();
    setTempName(project.name);
    setEditing(true);
  };
  const commitRename = () => {
    setEditing(false);
    const v = tempName.trim();
    if (v && v !== project.name) onRename(v);
    else setTempName(project.name);
  };
  const cancelRename = () => {
    setTempName(project.name);
    setEditing(false);
  };

  // Stato dell'ultima run (#11): preferisce `runStatus` dalla mappa log_runs
  // (status: success | failure | running), fallback al campo legacy del
  // progetto. Nessuna run → "Mai eseguito".
  const status = runStatus?.status || project.last_status;

  const statusLabel = status === 'success' ? 'Completato'
    : status === 'running' ? 'In esecuzione'
    : (status === 'failure' || status === 'error') ? 'Errore'
    : 'Mai eseguito';
  const statusMod = status === 'success' ? 'pl__status--ok'
    : (status === 'failure' || status === 'error') ? 'pl__status--err'
    : status === 'running' ? 'pl__status--run'
    : '';

  return (
    <div className="pl__card" onClick={onOpen}>
      <div className={`pl__card-accent ${accentClass}`} />
      <div className="pl__card-body">
        <div className="pl__card-header-row">
          {editing ? (
            <input
              className="pl__card-name-input"
              value={tempName}
              autoFocus
              onClick={e => e.stopPropagation()}
              onChange={e => setTempName(e.target.value)}
              onBlur={commitRename}
              onKeyDown={e => {
                if (e.key === 'Enter') commitRename();
                else if (e.key === 'Escape') cancelRename();
              }}
            />
          ) : (
            <div className="pl__card-name">{project.name}</div>
          )}
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
            {onRename && (
              <button type="button" className="pl__btn pl__btn--ghost pl__btn--sm"
                onClick={startRename}
                title="Rinomina progetto"
              >✎</button>
            )}
            {onClone && (
              <button type="button" className="pl__btn pl__btn--ghost pl__btn--sm"
                onClick={e => { e.stopPropagation(); onClone(); }}
                title="Clona progetto"
              >⧉</button>
            )}
            <button type="button" className="pl__btn pl__btn--ghost pl__btn--sm"
              onClick={e => { e.stopPropagation(); onDelete(); }}
              title="Elimina progetto"
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
