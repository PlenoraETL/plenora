import React from 'react';
import EseguibileCard from './EseguibileCard';

/**
 * Sezione "Eseguibili" della dashboard. Non riutilizza `ProjectSection`
 * perche` ha layout diverso: empty state piu` ricco con messaggio
 * differenziato admin/non-admin, e bottone "+ Nuovo" visibile solo a
 * admin (apre il selettore di progetto Arricchisci da pubblicare).
 */
function EseguibiliSection({
  items = [],
  isAdmin,
  onNew,
  onExecute,
  onDelete,
}) {
  return (
    <section id="pl-section-eseguibili" className="pl__section">
      <div className="pl__section-header">
        <div className="pl__section-title-wrap">
          <span className="pl__chip pl__chip--eseg">🚀 Eseguibili</span>
          <span className="pl__section-sub">Catene on-demand — l'utente carica un file e ottiene il risultato</span>
        </div>
        {isAdmin && (
          <button
            type="button"
            className="pl__btn pl__btn--ghost pl__btn--sm"
            onClick={onNew}
            title="Pubblica un progetto Arricchisci come eseguibile"
          >＋ Nuovo</button>
        )}
      </div>

      {items.length === 0 ? (
        <div className="pl__empty">
          <canvas className="pl__empty-hex" />
          <div className="pl__empty-icon">🚀</div>
          <div className="pl__empty-title">Nessun eseguibile pubblicato</div>
          <div className="pl__empty-text">
            {isAdmin
              ? 'Pubblica un progetto Arricchisci come eseguibile usando il pulsante 📢 sulla sua card o "+ Nuovo" qui sopra.'
              : 'Chiedi a un amministratore di pubblicare una catena come eseguibile.'}
          </div>
        </div>
      ) : (
        <div className="pl__cards-grid">
          {items.map(item => (
            <EseguibileCard
              key={item.id}
              item={item}
              isAdmin={isAdmin}
              onExecute={() => onExecute(item)}
              onDelete={() => onDelete(item)}
            />
          ))}
        </div>
      )}
    </section>
  );
}

export default EseguibiliSection;
