import React from 'react';
import ActionMenu from '../common/ActionMenu';
import BindingWarnings from '../eseguibili/BindingWarnings';
import DashboardBadge from '../eseguibili/DashboardBadge';
import { runInputSummary } from '../eseguibili/runForm';

/**
 * Card di un singolo eseguibile (catena on-demand). Il nome ed
 * «Esegui» aprono il wizard; l'eliminazione (admin) sta nel menu «⋯»
 * (G3, DB1). Visual style derivato dal modular system di ProjectCard
 * ma piu' compatto (no schedule, no last-run status).
 */
// onDelete arriva solo se l'utente puo' eliminare (can.manageEseguibile);
// canManage (stessa regola) mostra l'avviso sui binding non letti.
function EseguibileCard({ item, onExecute, onDelete, canManage = false }) {
  const fmt = (item.default_output_format || 'xlsx').toUpperCase();
  return (
    <div className="pl__card">
      <div className="pl__card-accent pl__card-accent--eseg" />
      <div className="pl__card-body">
        <div className="pl__card-header-row">
          <button type="button" className="pl__card-name pl__card-open" onClick={onExecute} title={item.name}>{item.name}</button>
          <DashboardBadge item={item} />
          <span className="pl__card-mod pl__card-mod--eseg">🚀 {fmt}</span>
        </div>
        {item.description && <div className="pl__card-desc">{item.description}</div>}
        {canManage && <BindingWarnings warnings={item.binding_warnings} />}
        <div className="pl__card-meta">
          <div>
            {/* UI conferma U14: stessa regola della finestra (runForm). */}
            <div className="pl__meta-key">Da preparare</div>
            <div className="pl__meta-val">{runInputSummary(item)}</div>
          </div>
          <div>
            <div className="pl__meta-key">Output</div>
            <div className="pl__meta-val">{fmt}</div>
          </div>
        </div>
        <div className="pl__card-footer">
          <span className="pl__status">On-demand</span>
          <div className="pl__card-actions">
            <ActionMenu
              label={`Azioni per ${item.name}`}
              items={[onDelete && { label: '🗑 Elimina', onSelect: onDelete, danger: true }]}
            />
            <button type="button" className="pl__btn pl__btn--primary pl__btn--sm"
              onClick={e => { e.stopPropagation(); onExecute(); }}
            >▶ Esegui</button>
          </div>
        </div>
      </div>
    </div>
  );
}

export default EseguibileCard;
