/**
 * PivotMappingBuilder.jsx
 *
 * Builder per il mapping pivot: associa valori sorgente a nomi colonne output.
 * L'utente aggiunge righe con coppie { valore_nella_colonna_pivot → nome_colonna_output }.
 *
 * Output (onChange): oggetto { "canone": "val_canone", "rendita": "val_rendita" }
 * Se vuoto → {} → il backend crea una colonna per ogni valore unico (auto).
 *
 * Fase 5 T5 (R8.3, R1.9):
 *  - le righe vengono SEMPRE dal valore salvato (`value`): dopo un undo o un
 *    cambio da un altro pannello si vede quello che c'e' nella config. Solo le
 *    righe nuove ancora senza valore sorgente sono locali;
 *  - i due campi hanno il buffer locale (FieldInput) e si consegnano al blur o
 *    con Invio: scrivendo "canone" la colonna in uscita non resta "c". Se la
 *    colonna in uscita e' vuota vale il valore sorgente (si vede nel campo
 *    dopo il commit: quello che si vede e' quello che si salva);
 *  - un valore sorgente gia' mappato in un'altra riga e' un errore del campo
 *    (prima la seconda riga sovrascriveva la prima in silenzio).
 */
import React, { useState } from 'react';
import FieldInput from '../common/FieldInput';
import { text } from '../../utils/fieldCodecs';
import './PivotMappingBuilder.css';

const SOURCE = text();
const OUTPUT = text();

const entriesOf = (dict) => (dict && typeof dict === 'object' ? Object.entries(dict) : []);

const PivotMappingBuilder = ({ value, onChange, disabled = false }) => {
  const entries = entriesOf(value);
  // Righe nuove senza valore sorgente: le sole locali (non sono nella config).
  const [pending, setPending] = useState(0);

  const emit = (nextEntries) => onChange(Object.fromEntries(nextEntries));

  const duplicateOf = (index) => (key) => (
    key && entries.some(([k], i) => i !== index && k === key) ? 'Valore già mappato in un\'altra riga' : null
  );

  const renameSource = (index, key) => {
    if (!key) return; // svuotare la sorgente non cancella la riga: c'e' ×
    emit(entries.map(([k, v], i) => (i === index ? [key, v === k ? key : v] : [k, v])));
  };
  const setOutput = (index, out) => {
    emit(entries.map(([k, v], i) => (i === index ? [k, out || k] : [k, v])));
  };
  const removeEntry = (index) => emit(entries.filter((_e, i) => i !== index));
  const commitPending = (key) => {
    if (!key) return;
    setPending(p => Math.max(0, p - 1));
    emit([...entries, [key, key]]);
  };
  const isEmpty = entries.length === 0 && pending === 0;

  return (
    <div className="pivot-mapping-builder">
      {!isEmpty && (
        <div className="pivot-mapping-builder__header">
          <span className="pivot-mapping-builder__header-col">Valore sorgente</span>
          <span className="pivot-mapping-builder__header-arrow"></span>
          <span className="pivot-mapping-builder__header-col">Nome della colonna in uscita</span>
          <span className="pivot-mapping-builder__header-action"></span>
        </div>
      )}

      {entries.map(([key, out], idx) => (
        <div key={`e${idx}`} className="pivot-mapping-builder__row">
          <span className="pivot-mapping-builder__cell">
            <FieldInput
              codec={SOURCE}
              value={key}
              commitOn="blur"
              validate={duplicateOf(idx)}
              onCommit={(k) => renameSource(idx, k)}
              placeholder="es: canone"
              aria-label="Valore sorgente"
              className="pivot-mapping-builder__input"
              disabled={disabled}
            />
          </span>
          <span className="pivot-mapping-builder__arrow">→</span>
          <span className="pivot-mapping-builder__cell">
            <FieldInput
              codec={OUTPUT}
              value={out}
              commitOn="blur"
              onCommit={(o) => setOutput(idx, o)}
              placeholder={key || 'es: val_canone'}
              aria-label="Nome della colonna in uscita"
              className="pivot-mapping-builder__input"
              disabled={disabled}
            />
          </span>
          <button type="button" onClick={() => removeEntry(idx)} className="pivot-mapping-builder__remove" title="Rimuovi" disabled={disabled}>
            ×
          </button>
        </div>
      ))}

      {Array.from({ length: pending }, (_x, p) => (
        <div key={`p${entries.length + p}`} className="pivot-mapping-builder__row">
          <span className="pivot-mapping-builder__cell">
            <FieldInput
              codec={SOURCE}
              value=""
              commitOn="blur"
              validate={duplicateOf(-1)}
              onCommit={commitPending}
              placeholder="es: canone"
              aria-label="Valore sorgente"
              className="pivot-mapping-builder__input"
              disabled={disabled}
            />
          </span>
          <span className="pivot-mapping-builder__arrow">→</span>
          <span className="pivot-mapping-builder__cell">
            <input
              type="text"
              value=""
              disabled
              placeholder="prima il valore sorgente"
              aria-label="Nome della colonna in uscita"
              className="pivot-mapping-builder__input"
            />
          </span>
          <button type="button" onClick={() => setPending(n => Math.max(0, n - 1))} className="pivot-mapping-builder__remove" title="Rimuovi" disabled={disabled}>
            ×
          </button>
        </div>
      ))}

      <button type="button" onClick={() => setPending(n => n + 1)} className="pivot-mapping-builder__add" disabled={disabled}>
        + Aggiungi mappatura
      </button>

      {isEmpty && (
        <div className="pivot-mapping-builder__hint">
          Nessuna mappatura: verrà creata una colonna per ogni valore unico
        </div>
      )}
    </div>
  );
};

export default PivotMappingBuilder;
