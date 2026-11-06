/**
 * MappingsPanel.jsx — Tab "Mappings" del ResponseConfigBuilder
 * Componente presentational: niente fetch, niente API, niente
 * useState. Riceve `iterateOn`, `mappings` + 4 callback.
 *
 * Render:
 *  - sezione "iterate_on": elenco array da esplodere con
 *    alias editabile e bottone rimuovi;
 *  - sezione "mappings": tabella path → colonna con tipo e
 *    bottone rimuovi;
 *  - empty state se nessun campo selezionato (rimanda alla
 *    tab "Struttura").
 */
import React from 'react';

export default function MappingsPanel({
  iterateOn,
  mappings,
  updateIterateAlias,
  toggleIterate,
  updateMappingColumn,
  toggleMapping,
}) {
  return (
    <div className="response-config-builder__panel">
      <div className="response-config-builder__section-header">
        <h4>📋 Campi da estrarre</h4>
        <p>Rinomina le colonne del risultato se serve</p>
      </div>

      {/* Iterate On */}
      {iterateOn.length > 0 && (
        <div className="response-config-builder__iterate-section">
          <h5>🔄 Array da esplodere (iterate_on):</h5>
          <div className="response-config-builder__iterate-list">
            {iterateOn.map((item, idx) => (
              <div key={idx} className="response-config-builder__iterate-item">
                <span className="response-config-builder__iterate-level">Livello {idx + 1}:</span>
                <code className="response-config-builder__iterate-path">{item.path || '(root)'}</code>
                <span className="response-config-builder__iterate-arrow">→</span>
                <input
                  type="text"
                  value={item.as}
                  onChange={e => updateIterateAlias(idx, e.target.value)}
                  className="response-config-builder__input response-config-builder__input--small"
                  placeholder="alias"
                />
                <button type="button"
                  onClick={() => toggleIterate(item.path)}
                  className="response-config-builder__btn response-config-builder__btn--danger response-config-builder__btn--icon"
                >
                  🗑️
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Mappings */}
      {mappings.length === 0 ? (
        <div className="response-config-builder__empty-section">
          <p>Nessun campo selezionato. Vai alla tab "Struttura" per selezionare i campi.</p>
        </div>
      ) : (
        <div className="response-config-builder__mappings-list">
          <div className="response-config-builder__mapping-header">
            <span>Path sorgente</span>
            <span></span>
            <span>Nome colonna</span>
            <span>Tipo</span>
            <span></span>
          </div>
          {mappings.map((m, idx) => (
            <div key={idx} className="response-config-builder__mapping-item">
              <code className="response-config-builder__mapping-path">{m.path}</code>
              <span className="response-config-builder__mapping-arrow">→</span>
              <input
                type="text"
                value={m.column}
                onChange={e => updateMappingColumn(idx, e.target.value)}
                className="response-config-builder__input"
              />
              <span className={`response-config-builder__type-badge response-config-builder__type-badge--${m.type}`}>
                {m.type}
              </span>
              <button type="button"
                onClick={() => toggleMapping(m.path, m.column, m.type)}
                className="response-config-builder__btn response-config-builder__btn--danger response-config-builder__btn--icon"
              >
                🗑️
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
