import React from 'react';

const TRANSACTION_MODES = [
  { value: 'all_or_nothing', label: 'Atomica', desc: 'Rollback totale su errore' },
  { value: 'per_batch', label: 'Per batch', desc: 'Commit ogni batch (parziale ok)' },
];

const CONFLICT_POLICIES = [
  { value: 'abort', label: 'Interrompi', desc: 'Blocca se ci sono conflitti' },
  { value: 'skip', label: 'Salta', desc: 'Inserisci solo righe non in conflitto' },
  { value: 'force', label: 'Sovrascrivi', desc: 'Sovrascrivi righe in conflitto' },
];

function DbAdvancedOptions({
  batchSize,
  transactionMode,
  conflictPolicy,
  createSpatialIndex,
  createUniqueIndex,
  uniqueKeyColumn,
  writeMode,
  availableColumns,
  showAdvanced,
  onToggleAdvanced,
  onUpdate,
}) {
  return (
    <div className="carica-node__section">
      <div
        className="carica-node__section-header"
        onClick={onToggleAdvanced}
      >
        <span>⚡ Opzioni Avanzate</span>
        <span className="carica-node__toggle">{showAdvanced ? '▼' : '▶'}</span>
      </div>

      {showAdvanced && (
        <div className="carica-node__advanced">
          <div className="carica-node__advanced-row">
            <label>Batch size:</label>
            <input
              type="number"
              value={batchSize}
              onChange={(e) => onUpdate('batch_size', parseInt(e.target.value) || 1000)}
              min={100} max={10000} step={100}
              className="carica-node__input carica-node__input--small"
            />
          </div>
          <div className="carica-node__advanced-row">
            <label>Transazione:</label>
            <select
              value={transactionMode}
              onChange={(e) => onUpdate('transaction_mode', e.target.value)}
              className="carica-node__select carica-node__select--small"
            >
              {TRANSACTION_MODES.map(mode => (
                <option key={mode.value} value={mode.value}>{mode.label}</option>
              ))}
            </select>
          </div>
          {writeMode === 'append' && (
            <div className="carica-node__advanced-row">
              <label>Se conflitto:</label>
              <select
                value={conflictPolicy}
                onChange={(e) => onUpdate('conflict_policy', e.target.value)}
                className="carica-node__select carica-node__select--small"
              >
                {CONFLICT_POLICIES.map(p => (
                  <option key={p.value} value={p.value}>{p.label}</option>
                ))}
              </select>
            </div>
          )}
          {(writeMode === 'create' || writeMode === 'replace') && (
            <div className="carica-node__advanced-row">
              <label>
                <input
                  type="checkbox"
                  checked={createSpatialIndex}
                  onChange={(e) => onUpdate('create_spatial_index', e.target.checked)}
                />
                Crea indice spaziale
              </label>
            </div>
          )}
          {(writeMode === 'create' || writeMode === 'replace') && (
            <div className="carica-node__advanced-row">
              <label>
                <input
                  type="checkbox"
                  checked={createUniqueIndex}
                  onChange={(e) => {
                    onUpdate('create_unique_index', e.target.checked);
                    if (!e.target.checked) onUpdate('unique_key_column', '');
                  }}
                />
                Crea indice univoco (per UPSERT)
              </label>
              {createUniqueIndex && (
                <select
                  value={uniqueKeyColumn}
                  onChange={(e) => onUpdate('unique_key_column', e.target.value)}
                  className="carica-node__select"
                  style={{ marginTop: '6px' }}
                >
                  <option value="">Seleziona colonna chiave...</option>
                  {availableColumns.map((col, idx) => {
                    const colName = col.name || col;
                    return <option key={idx} value={colName}>{colName}</option>;
                  })}
                </select>
              )}
              {createUniqueIndex && !uniqueKeyColumn && (
                <div className="carica-node__warning">
                  ⚠️ Seleziona la colonna chiave univoca
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default DbAdvancedOptions;
