import React from 'react';

/**
 * Card di un singolo eseguibile (catena on-demand). Click esegue
 * il wizard; bottoni interni gestiscono esecuzione e delete (admin
 * only). Visual style derivato dal modular system di ProjectCard
 * ma piu' compatto (no schedule, no last-run status).
 */
function EseguibileCard({ item, isAdmin, onExecute, onDelete }) {
  const fmt = (item.default_output_format || 'xlsx').toUpperCase();
  return (
    <div className="pl__card" onClick={onExecute}>
      <div className="pl__card-accent pl__card-accent--eseg" />
      <div className="pl__card-body">
        <div className="pl__card-header-row">
          <div className="pl__card-name">{item.name}</div>
          <span className="pl__card-mod pl__card-mod--eseg">🚀 {fmt}</span>
        </div>
        <div className="pl__card-desc">{item.description || 'Nessuna descrizione'}</div>
        <div className="pl__card-meta">
          <div>
            <div className="pl__meta-key">Colonne attese</div>
            <div className="pl__meta-val">{item.expected_columns_count ?? '—'}</div>
          </div>
          <div>
            <div className="pl__meta-key">Output</div>
            <div className="pl__meta-val">{fmt}</div>
          </div>
        </div>
        <div className="pl__card-footer">
          <span className="pl__status">On-demand</span>
          <div className="pl__card-actions">
            {isAdmin && (
              <button type="button" className="pl__btn pl__btn--ghost pl__btn--sm"
                onClick={e => { e.stopPropagation(); onDelete(); }}
                title="Elimina eseguibile"
              >🗑</button>
            )}
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
