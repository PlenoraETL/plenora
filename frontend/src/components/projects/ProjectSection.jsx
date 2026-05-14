import React from 'react';

/**
 * Sezione di gruppo della dashboard progetti (Estrai / Manipola /
 * Arricchisci). Header con chip etichetta + bottone "+ Nuovo" +
 * empty state decorato + griglia dei children (cards).
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
        <div className="pl__empty">
          <canvas className="pl__empty-hex" />
          <div className="pl__empty-icon">{emptyIcon}</div>
          <div className="pl__empty-title">{emptyTitle}</div>
          <div className="pl__empty-text">{emptyText}</div>
        </div>
      ) : (
        <div className="pl__cards-grid">{children}</div>
      )}
    </section>
  );
}

export default ProjectSection;
