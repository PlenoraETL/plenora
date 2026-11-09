import React, { useState } from 'react';
import ActionMenu from '../common/ActionMenu';
import { prefetchProjectEditor } from '../../utils/editorChunks';
import ProjectTags from './ProjectTags';

/**
 * Card di un singolo progetto (Estrai / Manipola / Arricchisci).
 * Il nome e «Apri →» aprono il progetto (pulsanti veri, a11y-1: niente
 * <div onClick>); rinomina, clona ed elimina stanno nel menu «⋯» (G3, DB1:
 * l'eliminazione non è a vista accanto ad «Apri»). Campi vuoti nascosti
 * (DB3: niente «Nessuna descrizione»). `meta` e' una lista di {key, val}.
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
  onSetTags,
  onSchedule,
}) {
  const [editing, setEditing] = useState(false);
  const [tempName, setTempName] = useState(project.name);

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
  // progetto. Nessuna run → "Mai eseguito". Stato non caricato (Fase 5 T2,
  // R4.9) → "Stato non disponibile", mai "Mai eseguito".
  const status = runStatus?.status || project.last_status;

  const statusLabel = runStatus?.status === 'unavailable' ? 'Stato non disponibile'
    : status === 'success' ? 'Completato'
    : status === 'running' ? 'In esecuzione'
    : (status === 'failure' || status === 'error') ? 'Errore'
    : 'Mai eseguito';
  const statusMod = status === 'success' ? 'pl__status--ok'
    : (status === 'failure' || status === 'error') ? 'pl__status--err'
    : status === 'running' ? 'pl__status--run'
    : '';

  return (
    // Passando sul progetto si scarica gia' il suo editor (UI-4, V13).
    <div className="pl__card" onMouseEnter={() => prefetchProjectEditor(project.project_type)} onFocus={() => prefetchProjectEditor(project.project_type)}>
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
            <button type="button" className="pl__card-name pl__card-open" onClick={onOpen} title={project.name}>{project.name}</button>
          )}
          <span className="pl__card-mod">{modLabel}</span>
        </div>
        {project.description && <div className="pl__card-desc">{project.description}</div>}
        <div className="pl__card-meta">
          {meta.map(({ key, val }) => (
            <div key={key}>
              <div className="pl__meta-key">{key}</div>
              <div className="pl__meta-val">{val}</div>
            </div>
          ))}
        </div>

        {/* Tag (#10): chip rimovibili + aggiunta inline (solo se onSetTags). */}
        <ProjectTags tags={project.tags} onSetTags={onSetTags} />
        <div className="pl__card-footer">
          <span className={`pl__status ${statusMod}`}>{statusLabel}</span>
          <div className="pl__card-actions">
            {scheduleCount > 0 && (
              <button type="button" className="pl__btn pl__btn--ghost pl__btn--sm pl__btn--schedule"
                onClick={e => { e.stopPropagation(); onSchedule(); }}
                title="Pianificazioni automatiche"
              >
                ⏰ <span className="pl__schedule-badge">{scheduleCount}</span>
              </button>
            )}
            <ActionMenu
              label={`Azioni per ${project.name}`}
              items={[
                onRename && { label: '✎ Rinomina', onSelect: () => { setTempName(project.name); setEditing(true); } },
                onClone && { label: '⧉ Clona', onSelect: onClone },
                onDelete && { label: '🗑 Elimina', onSelect: onDelete, danger: true },
              ]}
            />
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
