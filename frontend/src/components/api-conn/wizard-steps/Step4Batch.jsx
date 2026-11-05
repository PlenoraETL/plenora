/**
 * Step4Batch.jsx — Step 4 del wizard ApiConnection
 *
 * Configurazione batch mode (max righe / chiave array / formato input
 * / endpoint+method override).
 *
 * Presentational.
 */
import React from 'react';
import FieldInput from '../../common/FieldInput';
import { integer } from '../../../utils/fieldCodecs';

// Righe per chiamata dal campo unico (R8.4): niente default silenzioso.
const MAX_SIZE = integer({ min: 1, max: 10000 });

export default function Step4Batch({ formData, setFormData }) {
  return (
    <div className="acm__wstep-content">
      <div className="acm__wlabel">⚡ Configurazione Batch</div>
      <div className="acm__info-box acm__info-box--mb-md">
        ℹ️ Abilita solo se l'API accetta un array di input in una singola chiamata (es. <code>{`{"items":[...]}`}</code>).
        Per API standard (una chiamata per riga) lascia disabilitato.
      </div>
      <label className="acm__toggle-label acm__toggle-label--mb-md">
        <input type="checkbox" checked={formData.batch?.enabled||false}
          onChange={e=>setFormData(f=>({...f, batch:{...f.batch, enabled:e.target.checked}}))}/>
        <span>Batch mode abilitato</span>
      </label>
      {formData.batch?.enabled && (<>
        <div className="acm__field-row">
          <label className="acm__label">Max righe per chiamata</label>
          <FieldInput className="acm__input" aria-label="Max righe per chiamata" codec={MAX_SIZE}
            value={formData.batch?.max_size ?? 100}
            onCommit={v=>setFormData(f=>({...f, batch:{...f.batch, max_size:v}}))}/>
        </div>
        <div className="acm__field-row">
          <label className="acm__label">Chiave array nel payload</label>
          <input type="text" className="acm__input" placeholder="items"
            value={formData.batch?.input_key||'items'}
            onChange={e=>setFormData(f=>({...f, batch:{...f.batch, input_key:e.target.value}}))}/>
          <div className="acm__field-hint">Es: <code>items</code> → <code>{`{"items":[...]}`}</code></div>
        </div>
        <div className="acm__field-row">
          <label className="acm__label">Formato input</label>
          <select className="acm__input"
            value={formData.batch?.input_format||'array'}
            onChange={e=>setFormData(f=>({...f, batch:{...f.batch, input_format:e.target.value}}))}>
            <option value="array">Array di oggetti — {`[{"city":"Rome"}, ...]`}</option>
            <option value="flat_array">Array piatto — {`["Rome", "Milan", ...]`}</option>
          </select>
          <div className="acm__field-hint"><strong>Array di oggetti</strong>: più parametri per riga. <strong>Array piatto</strong>: una sola colonna come lista di valori.</div>
        </div>
        <div className="acm__field-row">
          <label className="acm__label">Endpoint override (opzionale)</label>
          <input type="text" className="acm__input" placeholder="Lascia vuoto per usare URL principale"
            value={formData.batch?.endpoint_override||''}
            onChange={e=>setFormData(f=>({...f, batch:{...f.batch, endpoint_override:e.target.value}}))}/>
        </div>
        <div className="acm__field-row">
          <label className="acm__label">Metodo HTTP override (opzionale)</label>
          <select className="acm__input"
            value={formData.batch?.method_override||''}
            onChange={e=>setFormData(f=>({...f, batch:{...f.batch, method_override:e.target.value}}))}>
            <option value="">Usa metodo principale ({formData.method})</option>
            <option value="POST">POST</option>
            <option value="GET">GET</option>
            <option value="PUT">PUT</option>
          </select>
        </div>
      </>)}
    </div>
  );
}
