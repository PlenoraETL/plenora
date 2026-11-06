import React from 'react';
import EmptyState from '../common/EmptyState';

/**
 * Sezione di gruppo della dashboard progetti (Estrai / Manipola /
 * Arricchisci). Header con chip etichetta + bottone "+ Nuovo" + la griglia
 * delle card nei children (VirtualList con `pl__cards-grid`, UI-2 V9).
 * Vuota: una riga compatta (DB6, G2), col testo del filtro quando la lista
 * è vuota per un filtro (P18).
 */
function ProjectSection({
  id,
  chipClass,
  chipLabel,
  subtitle,
  onNew,
  canCreate = true,
  empty,
  emptyIcon,
  emptyTitle,
  emptyText,
  children,
}) {
  return (
    <section id={id} className="pl__section">
      <div className="pl__section-header">
        <div className="pl__section-title-wrap">
          <span className={`pl__chip ${chipClass}`}>{chipLabel}</span>
          <span className="pl__section-sub">{subtitle}</span>
        </div>
        {canCreate && (
          <button type="button" className="pl__btn pl__btn--ghost pl__btn--sm" onClick={onNew}>＋ Nuovo</button>
        )}
      </div>

      {empty ? (
        <EmptyState compact icon={emptyIcon} title={emptyTitle} text={emptyText} />
      ) : (
        children
      )}
    </section>
  );
}

export default ProjectSection;
