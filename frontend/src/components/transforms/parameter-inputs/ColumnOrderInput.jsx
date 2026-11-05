/**
 * ColumnOrderInput.jsx — Drag&drop riordinamento colonne
 * Usato dal dispatcher per il tipo `column_order`. Stato locale
 * `dragIdx` / `dragOverIdx` per il feedback drag (non vive nel
 * parent).
 *
 * Inizializza `value` con tutte le colonne se vuoto (effect su
 * `columns`).
 *
 * Fase 5 T5 (R8.16): l'elenco mostrato e' l'ordine che il backend applica
 * (manipola/transforms/columns.op_reorder_columns): le colonne salvate che
 * esistono ancora, nell'ordine salvato, poi quelle nuove a monte in coda.
 * Una colonna rimossa a monte non si mostra (il backend la ignora); i nomi
 * ripetuti si contano una volta sola (chiavi uniche).
 */
import React, { useState } from 'react';

/** Ordine effettivo: salvato ∩ attuale, poi le colonne nuove in coda. */
export function effectiveColumnOrder(saved, columns) {
  const names = [];
  const seen = new Set();
  const push = (name) => { if (name && !seen.has(name)) { seen.add(name); names.push(name); } };
  const current = (columns || []).map(c => c.name);
  const known = current.length > 0 ? new Set(current) : null;
  (Array.isArray(saved) ? saved : []).forEach(name => { if (!known || known.has(name)) push(name); });
  current.forEach(push);
  return names;
}

const ColumnOrderInput = ({ value, onChange, columns }) => {
  const [dragIdx, setDragIdx] = useState(null);
  const [dragOverIdx, setDragOverIdx] = useState(null);

  // Inizializza con tutte le colonne se vuoto
  const orderedCols = effectiveColumnOrder(value, columns);

  // Nessuna inizializzazione automatica della config: con value vuoto la
  // lista mostra l'ordine corrente delle colonne (`order` sopra) e il
  // backend accoda comunque le colonne non elencate. Prima un effect
  // scriveva la config al montaggio e rendeva il canvas "modificato"
  // appena aperto (collaudo Fase 3). Il primo trascinamento salva l'ordine.

  const handleDragStart = (idx) => {
    setDragIdx(idx);
  };

  const handleDragOver = (e, idx) => {
    e.preventDefault();
    if (idx !== dragOverIdx) setDragOverIdx(idx);
  };

  const handleDrop = (e, targetIdx) => {
    e.preventDefault();
    if (dragIdx === null || dragIdx === targetIdx) {
      setDragIdx(null);
      setDragOverIdx(null);
      return;
    }
    const updated = [...orderedCols];
    const [moved] = updated.splice(dragIdx, 1);
    updated.splice(targetIdx, 0, moved);
    onChange(updated, updated.map(name => columns.find(c => c.name === name)).filter(Boolean));
    setDragIdx(null);
    setDragOverIdx(null);
  };

  const handleDragEnd = () => {
    setDragIdx(null);
    setDragOverIdx(null);
  };

  // Sposta su/giù con bottoni
  const moveUp = (idx) => {
    if (idx === 0) return;
    const updated = [...orderedCols];
    [updated[idx - 1], updated[idx]] = [updated[idx], updated[idx - 1]];
    onChange(updated, updated.map(name => columns.find(c => c.name === name)).filter(Boolean));
  };

  const moveDown = (idx) => {
    if (idx >= orderedCols.length - 1) return;
    const updated = [...orderedCols];
    [updated[idx], updated[idx + 1]] = [updated[idx + 1], updated[idx]];
    onChange(updated, updated.map(name => columns.find(c => c.name === name)).filter(Boolean));
  };

  // Trova tipo colonna
  const getColType = (name) => {
    const col = columns.find(c => c.name === name);
    return col?.type || '';
  };

  return (
    <div className="column-order-input">
      <div className="column-order-input__header">
        <span className="column-order-input__count">
          {orderedCols.length} colonne
        </span>
      </div>
      <div className="column-order-input__list">
        {orderedCols.map((colName, idx) => (
          <div
            key={colName}
            className={
              'column-order-input__item' +
              (dragIdx === idx ? ' column-order-input__item--dragging' : '') +
              (dragOverIdx === idx ? ' column-order-input__item--over' : '')
            }
            draggable
            onDragStart={() => handleDragStart(idx)}
            onDragOver={(e) => handleDragOver(e, idx)}
            onDrop={(e) => handleDrop(e, idx)}
            onDragEnd={handleDragEnd}
          >
            <span className="column-order-input__grip">⠿</span>
            <span className="column-order-input__pos">{idx + 1}</span>
            <span className="column-order-input__name">{colName}</span>
            <span className="column-order-input__type">{getColType(colName)}</span>
            <div className="column-order-input__buttons">
              <button
                type="button"
                className="column-order-input__btn"
                onClick={() => moveUp(idx)}
                disabled={idx === 0}
                title="Sposta su"
              >▲</button>
              <button
                type="button"
                className="column-order-input__btn"
                onClick={() => moveDown(idx)}
                disabled={idx >= orderedCols.length - 1}
                title="Sposta giù"
              >▼</button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};

export default ColumnOrderInput;
  const columnKey = (col) => col?.id || col?.column_id || col?.name;
