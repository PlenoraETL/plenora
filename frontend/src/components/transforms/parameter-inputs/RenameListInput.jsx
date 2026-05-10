/**
 * RenameListInput.jsx — Lista rinominazioni colonne (con CSV paste)
 * Usato dal dispatcher per il tipo `rename_list`. Stato locale
 * per il pannello CSV-paste (`showCsvPaste`, `csvText`, `csvError`).
 * Le rinominazioni vivono nel parent (controlled).
 *
 * Supporta separatori , ; e tab nel paste.
 */
import React, { useState } from 'react';

const RenameListInput = ({ value, onChange, columns }) => {
  const renames = Array.isArray(value) ? value : [];
  const [showCsvPaste, setShowCsvPaste] = useState(false);
  const [csvText, setCsvText] = useState('');
  const [csvError, setCsvError] = useState('');

  const addRename = () => {
    onChange([...renames, { old_name: '', new_name: '' }]);
  };

  const loadAllColumns = () => {
    // Colonne già presenti nella lista
    const existing = new Set(renames.map(r => r.old_name).filter(Boolean));
    // Aggiungi solo quelle mancanti
    const newEntries = columns
      .filter(col => !existing.has(col.name))
      .map(col => ({ old_name: col.name, new_name: '' }));
    if (newEntries.length === 0) return;
    onChange([...renames.filter(r => r.old_name), ...newEntries]);
  };

  const parseCsv = () => {
    setCsvError('');
    if (!csvText.trim()) return;

    const lines = csvText.trim().split('\n').filter(l => l.trim());
    const colNames = new Set(columns.map(c => c.name));
    const parsed = [];
    const errors = [];

    for (let i = 0; i < lines.length; i++) {
      const line = lines[i].trim();
      // Supporta: tab, punto e virgola, virgola
      let parts = null;
      if (line.includes('\t')) {
        parts = line.split('\t');
      } else if (line.includes(';')) {
        parts = line.split(';');
      } else if (line.includes(',')) {
        parts = line.split(',');
      }

      if (!parts || parts.length < 2) {
        errors.push(`Riga ${i + 1}: servono 2 colonne separate da , ; o tab`);
        continue;
      }

      const oldName = parts[0].trim();
      const newName = parts[1].trim();

      if (!oldName || !newName) {
        errors.push(`Riga ${i + 1}: nome vuoto`);
        continue;
      }

      if (colNames.size > 0 && !colNames.has(oldName)) {
        errors.push(`Riga ${i + 1}: "${oldName}" non trovata nelle colonne`);
      }

      parsed.push({ old_name: oldName, new_name: newName });
    }

    if (parsed.length > 0) {
      // Merge: sostituisci quelle con stesso old_name, aggiungi nuove
      const existingMap = new Map(renames.filter(r => r.old_name).map(r => [r.old_name, r]));
      parsed.forEach(p => existingMap.set(p.old_name, p));
      onChange(Array.from(existingMap.values()));
      setCsvText('');
      setShowCsvPaste(false);
    }

    if (errors.length > 0) {
      setCsvError(errors.join('\n'));
    }
  };

  const updateRename = (idx, field, newValue) => {
    const updated = [...renames];
    updated[idx] = { ...updated[idx], [field]: newValue };
    onChange(updated);
  };

  const removeRename = (idx) => {
    onChange(renames.filter((_, i) => i !== idx));
  };

  return (
    <div className="rename-list-input">
      {renames.map((rename, idx) => (
        <div key={idx} className="rename-list-input__row">
          <select
            value={rename.old_name || ''}
            onChange={(e) => updateRename(idx, 'old_name', e.target.value)}
            className="rename-list-input__select"
          >
            <option value="">Colonna...</option>
            {columns.map((col, cidx) => (
              <option key={`${col.name}-${cidx}`} value={col.name}>{col.name}</option>
            ))}
          </select>
          <span className="rename-list-input__arrow">→</span>
          <input
            type="text"
            value={rename.new_name || ''}
            onChange={(e) => updateRename(idx, 'new_name', e.target.value)}
            placeholder="Nuovo nome"
            className="rename-list-input__input"
          />
          <button
            type="button"
            onClick={() => removeRename(idx)}
            className="rename-list-input__remove"
          >
            ×
          </button>
        </div>
      ))}
      <div className="rename-list-input__buttons">
        <button
          type="button"
          onClick={addRename}
          className="rename-list-input__add"
        >
          + Aggiungi rinomina
        </button>
        {columns.length > 0 && (
          <button
            type="button"
            onClick={loadAllColumns}
            className="rename-list-input__add rename-list-input__add--load-all"
          >
            📋 Carica tutte ({columns.length})
          </button>
        )}
        <button
          type="button"
          onClick={() => { setShowCsvPaste(!showCsvPaste); setCsvError(''); }}
          className={`rename-list-input__add rename-list-input__add--csv ${showCsvPaste ? 'rename-list-input__add--csv-active' : ''}`}
        >
          📝 Incolla CSV
        </button>
      </div>

      {showCsvPaste && (
        <div className="rename-list-input__csv-panel">
          <div className="rename-list-input__csv-hint">
            Formato: <code>nome_originale, nuovo_nome</code> (una per riga)
            <br />Separatori accettati: virgola, punto e virgola, tab
          </div>
          <textarea
            className="rename-list-input__csv-textarea"
            value={csvText}
            onChange={(e) => setCsvText(e.target.value)}
            placeholder={"col_vecchia, col_nuova\naltro_campo, nuovo_campo\n..."}
            rows={6}
            onMouseDown={(e) => e.stopPropagation()}
          />
          {csvError && (
            <div className="rename-list-input__csv-error">
              {csvError}
            </div>
          )}
          <button
            type="button"
            onClick={parseCsv}
            className="rename-list-input__csv-apply"
            disabled={!csvText.trim()}
          >
            ✓ Applica rinominazioni
          </button>
        </div>
      )}
    </div>
  );
};

export default RenameListInput;
