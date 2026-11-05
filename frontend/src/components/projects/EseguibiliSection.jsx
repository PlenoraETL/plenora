import React from 'react';
import EseguibileCard from './EseguibileCard';
import EmptyState from '../common/EmptyState';

/**
 * Sezione "Eseguibili" della dashboard. Non riutilizza `ProjectSection`
 * perche` ha layout diverso: empty state piu` ricco con messaggio
 * differenziato admin/non-admin, e bottone "+ Nuovo" visibile solo a
 * admin (apre il selettore di progetto Arricchisci da pubblicare).
 */
function EseguibiliSection({
  items = [],
  canCreate = false,
  canDelete = () => false,
  onNew,
  onExecute,
  onDelete,
  filtered = false,
}) {
  return (
    <section id="pl-section-eseguibili" className="pl__section">
      <div className="pl__section-header">
        <div className="pl__section-title-wrap">
          <span className="pl__chip pl__chip--eseg">🚀 Eseguibili</span>
          <span className="pl__section-sub">Catene on-demand — l'utente carica un file e ottiene il risultato</span>
        </div>
        {canCreate && (
          <button
            type="button"
            className="pl__btn pl__btn--ghost pl__btn--sm"
            onClick={onNew}
            title="Pubblica un progetto come eseguibile"
          >＋ Nuovo</button>
        )}
      </div>

      {items.length === 0 ? (
        <EmptyState
          compact
          icon="🚀"
          title={filtered ? 'Nessun eseguibile con questa ricerca' : 'Nessun eseguibile pubblicato'}
          text={filtered
            ? 'Cambia o cancella la ricerca.'
            : canCreate
              ? 'Pubblica un progetto come eseguibile con «＋ Nuovo» qui sopra.'
              : 'Chiedi a un amministratore di pubblicare una catena come eseguibile.'}
        />
      ) : (
        <div className="pl__cards-grid">
          {items.map(item => (
            <EseguibileCard
              key={item.id}
              item={item}
              onExecute={() => onExecute(item)}
              onDelete={canDelete(item) ? () => onDelete(item) : null}
              canManage={canDelete(item)}
            />
          ))}
        </div>
      )}
    </section>
  );
}

export default EseguibiliSection;
