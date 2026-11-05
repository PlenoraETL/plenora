import React, { useState } from 'react';
import { postCaricaAutoMapping } from '../../../api/carica';
import { requestErrorMessage } from '../../../utils/errorMessages';
import { useBusy } from '../../../hooks/useBusy';
import '../../common/ListState.css';
import { READ_ONLY_SAFE } from '../../common/readOnlySafe';

function ColumnMappingPanel({
  columnMappings,
  availableColumns,
  connectionId,
  schemaName,
  tableName,
  showMapping,
  onToggleMapping,
  onUpdate,
}) {
  // Fase 5 T2 (R4.17): l'errore della mappatura automatica si vede (prima
  // solo in console); un secondo clic durante la richiesta non parte.
  const busy = useBusy();
  const [autoError, setAutoError] = useState(null);
  const loadAutoMapping = () => busy.run('auto', async () => {
    if (!connectionId || availableColumns.length === 0) return;
    setAutoError(null);
    try {
      // Payload al backend: solo name e type (alias non accettato dal modello)
      const data = await postCaricaAutoMapping({
        connection_id: connectionId,
        schema_name: schemaName,
        table_name: tableName,
        source_columns: availableColumns.map(c => ({
          name: c.name || c,
          type: c.type || 'str',
        })),
      });
      // Normalizza formato backend → frontend.
      // source = nome colonna originale (NON alias) — il backend usa il nome originale per leggere il DataFrame
      const normalized = (data.mappings || []).map(m => ({
        source: m.source_column || m.source || '',
        dest:   m.dest_column   || m.dest   || '',
        is_pk:  m.is_primary_key || false,
        dest_type: m.dest_type || '',
        is_geometry: m.is_geometry || false,
        skip:   m.skip || false,
      }));
      onUpdate('column_mappings', normalized);
    } catch (error) {
      console.warn('Errore auto-mapping:', error);
      setAutoError(requestErrorMessage(error));
    }
  });

  const handleMappingChange = (index, field, value) => {
    const newMappings = [...columnMappings];
    newMappings[index] = { ...newMappings[index], [field]: value };
    onUpdate('column_mappings', newMappings);
  };

  const handleAddMapping = () => {
    onUpdate('column_mappings', [...columnMappings, { source: '', dest: '', is_pk: false }]);
  };

  const handleRemoveMapping = (index) => {
    onUpdate('column_mappings', columnMappings.filter((_, i) => i !== index));
  };

  return (
    <div className="carica-node__section">
      <div
        className="carica-node__section-header"
        onClick={onToggleMapping}
      >
        <span>🔀 Mappatura delle colonne</span>
        <span className="carica-node__toggle">{showMapping ? '▼' : '▶'}</span>
      </div>

      {showMapping && (
        <div className="carica-node__mapping">
          {autoError && (
            <div className="carica-node__hint carica-node__hint--warn" role="alert">
              Mappatura automatica non riuscita: {autoError}{' '}
              <button type="button" {...READ_ONLY_SAFE} className="nodrag list-state__retry" onClick={loadAutoMapping}>Riprova</button>
            </div>
          )}
          <div className="carica-node__mapping-actions">
            <button type="button"
              onClick={loadAutoMapping}
              disabled={busy.isBusy('auto')}
              className="carica-node__btn carica-node__btn--secondary"
            >
              {busy.isBusy('auto') ? '⏳ Mappatura…' : '🪄 Mappatura automatica'}
            </button>
            <button type="button"
              onClick={handleAddMapping}
              className="carica-node__btn carica-node__btn--secondary"
            >
              + Aggiungi
            </button>
          </div>

          {columnMappings.length === 0 ? (
            <div className="carica-node__hint">
              Nessuna mappatura definita. Usa «Mappatura automatica» o aggiungi a mano.
            </div>
          ) : (
            <div className="carica-node__mapping-list">
              {columnMappings.map((mapping, idx) => (
                <div key={idx} className="carica-node__mapping-row">
                  <select
                    value={mapping.source || ''}
                    onChange={(e) => handleMappingChange(idx, 'source', e.target.value)}
                    className="carica-node__mapping-select"
                  >
                    <option value="">Sorgente...</option>
                    {availableColumns.map((col, colIdx) => {
                      const originalName = col.name || col;
                      const displayName = col.alias ? col.alias + ' (' + originalName + ')' : originalName;
                      return (
                        <option key={colIdx} value={originalName}>
                          {displayName}
                        </option>
                      );
                    })}
                  </select>
                  <span className="carica-node__mapping-arrow">→</span>
                  <input
                    type="text"
                    value={mapping.dest || ''}
                    onChange={(e) => handleMappingChange(idx, 'dest', e.target.value)}
                    placeholder="Destinazione..."
                    className="carica-node__mapping-input"
                  />
                  <button type="button"
                    onClick={() => handleRemoveMapping(idx)}
                    className="carica-node__mapping-remove"
                  >
                    ×
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default ColumnMappingPanel;
