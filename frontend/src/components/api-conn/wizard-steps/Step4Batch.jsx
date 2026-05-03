/**
 * Step4Batch.jsx — Step 4 del wizard ApiConnection
 *
 * Configurazione batch mode (max righe / chiave array / formato input
 * / endpoint+method override).
 *
 * Estratto in `FE-PHASE2-SPLIT-001B`. Presentational.
 */
import React from 'react';

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
          <input type="number" className="acm__input" min={1} max={10000}
            value={formData.batch?.max_size||100}
            onChange={e=>setFormData(f=>({...f, batch:{...f.batch, max_size:parseInt(e.target.value)||100}}))}/>
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
