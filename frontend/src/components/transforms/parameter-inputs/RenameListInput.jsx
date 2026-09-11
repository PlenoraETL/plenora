/**
 * RenameListInput.jsx — Lista rinominazioni colonne (con CSV paste)
 * Usato dal dispatcher per il tipo `rename_list`. Stato locale
 * per il pannello CSV-paste (`showCsvPaste`, `csvText`, `csvError`).
 * Le rinominazioni vivono nel parent (controlled).
 *
 * Supporta separatori , ; e tab nel paste.
 */
import React, { useState } from 'react';

// Suffissi accumulati dai join a catena (es. "_L_L_L", "_R_L_L") — uno o
// più gruppi consecutivi di _L/_R in coda al nome colonna.
const TRAILING_LR_SUFFIX_RE = /(?:_[LR])+$/;

const RenameListInput = ({ value, onChange, columns }) => {
  const renames = Array.isArray(value) ? value : [];
  const [showCsvPaste, setShowCsvPaste] = useState(false);
  const [csvText, setCsvText] = useState('');
  const [csvError, setCsvError] = useState('');

  const columnKey = (col) => col?.id || col?.column_id || col?.name;

  const addRename = () => {
    onChange([...renames, { old_name: '', old_name_id: null, new_name: '' }]);
  };

  const loadAllColumns = () => {
    // Colonne già presenti nella lista
    const existing = new Set(renames.map(r => r.old_name).filter(Boolean));
    // Aggiungi solo quelle mancanti
    const newEntries = columns
      .filter(col => !existing.has(col.name))
      .map(col => ({ old_name: col.name, old_name_id: columnKey(col), new_name: '' }));
    if (newEntries.length === 0) return;
    onChange([...renames.filter(r => r.old_name), ...newEntries]);
  };

  const stripLRSuffixes = () => {
    // Propone una rinomina per ogni colonna il cui nome termina con uno o
    // più suffissi _L/_R accumulati dai join a catena (es. "cd_id_L_L_L" →
    // "cd_id"). Le colonne già pulite (nessun suffisso finale) sono
    // ignorate. In caso di collisione (due colonne diverse che, tolti i
    // suffissi, darebbero lo stesso nome — tipiche chiavi di join
    // duplicate) la colonna non viene proposta: va gestita a mano o con
    // "Elimina colonne", non rinominata alla cieca.
    const stripped = columns
      .map(col => ({ col, cleanName: col.name.replace(TRAILING_LR_SUFFIX_RE, '') }))
      .filter(({ col, cleanName }) => cleanName && cleanName !== col.name);

    const cleanNameCounts = stripped.reduce((acc, { cleanName }) => {
      acc[cleanName] = (acc[cleanName] || 0) + 1;
      return acc;
    }, {});

    const newEntries = stripped
      .filter(({ cleanName }) => cleanNameCounts[cleanName] === 1)
      .map(({ col, cleanName }) => ({ old_name: col.name, old_name_id: columnKey(col), new_name: cleanName }));

    const skippedCollisions = stripped.length - newEntries.length;
    if (skippedCollisions > 0) {
      setCsvError(
        `${skippedCollisions} colonne saltate: più colonne diverse darebbero lo stesso nome dopo la pulizia ` +
        `(probabili chiavi di join duplicate — usa "Elimina colonne" per quelle).`
      );
    } else {
      setCsvError('');
    }

    if (newEntries.length === 0) return;

    // Merge: sostituisci quelle con stesso old_name, aggiungi nuove.
    const existingMap = new Map(renames.filter(r => r.old_name).map(r => [r.old_name, r]));
    newEntries.forEach(e => existingMap.set(e.old_name, e));
    onChange(Array.from(existingMap.values()));
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

      const matched = columns.find(c => c.name === oldName);
      parsed.push({ old_name: oldName, old_name_id: columnKey(matched) || null, new_name: newName });
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
    if (field === 'old_name') {
      const selectedColumn = columns.find(col => col.name === newValue);
      updated[idx].old_name_id = columnKey(selectedColumn) || null;
    }
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
              <option key={`${columnKey(col)}-${cidx}`} value={col.name}>{col.name}</option>
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
        {columns.length > 0 && (
          <button
            type="button"
            onClick={stripLRSuffixes}
            className="rename-list-input__add rename-list-input__add--strip-lr"
            title="Propone un nuovo nome per ogni colonna togliendo i suffissi _L/_R accumulati dai join (es. cd_id_L_L_L → cd_id)"
          >
            🧹 Rimuovi suffissi _L/_R
          </button>
        )}
      </div>

      {csvError && !showCsvPaste && (
        <div className="rename-list-input__csv-error">
          {csvError}
        </div>
      )}

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
