import React from 'react';
import '../styles/SourcesSidebar.css'

/**
 * QuickActions - Sezione azioni rapide per sidebar
 * 
 * Props:
 * - actions: Array - lista azioni [{label, icon, onClick, variant, disabled}]
 * - title: string - titolo sezione
 * - accentColor: string - colore accent
 */
function QuickActions({
  actions = [],
  title = "Azioni Rapide",
  accentColor = '#9C27B0',
  className = ''
}) {
  if (actions.length === 0) return null;

  return (
    <div className={`quick-actions ${className}`}>
      <h4 className="quick-actions__title">{title}</h4>
      
      <div className="quick-actions__list">
        {actions.map((action, index) => (
          <button
            key={index}
            className={`quick-actions__btn quick-actions__btn--${action.variant || 'default'}`}
            onClick={action.onClick}
            disabled={action.disabled}
            style={{ '--accent-color': accentColor }}
            title={action.tooltip}
          >
            {action.icon && <span className="quick-actions__btn-icon">{action.icon}</span>}
            <span className="quick-actions__btn-label">{action.label}</span>
          </button>
        ))}
      </div>
    </div>
  );
}

export default QuickActions;
