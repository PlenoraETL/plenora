/**
 * Step1BasicInfo.jsx — Step 1 del wizard ApiConnection
 *
 * Nome / descrizione / URL endpoint / metodo + opzioni avanzate
 * (rate limit / timeout / retry / parallel workers).
 *
 * Presentational.
 */
import React, { useState } from 'react';

export default function Step1BasicInfo({ formData, setFormData, onImportCurl }) {
  const [curlText, setCurlText] = useState('');
  const [curlStatus, setCurlStatus] = useState(null);

  const importCurl = () => {
    try {
      const result = onImportCurl(curlText);
      const suffix = result?.warnings?.length
        ? ` (${result.warnings.join(' · ')})`
        : '';
      setCurlStatus({ type: 'ok', message: `cURL importato${suffix}` });
    } catch (err) {
      setCurlStatus({ type: 'err', message: err.message || 'cURL non valido' });
    }
  };

  return (
    <div className="acm__wstep-content">
      {formData.connection_type !== 'rest_file' && (
        <details className="acm__details">
          <summary>Importa da cURL</summary>
          <div className="acm__field-group">
            <textarea
              className="acm__textarea acm__input--mono"
              rows={4}
              value={curlText}
              onChange={e=>setCurlText(e.target.value)}
              placeholder={"curl -X POST 'https://service.test/items' -H 'Authorization: Bearer ...' --data-raw '{\"city\":\"Roma\"}'"}
            />
            <div className="acm__curl-import-actions">
              <button type="button" className="acm__btn acm__btn--ok acm__btn--sm" onClick={importCurl}>
                Importa cURL
              </button>
              {curlStatus && (
                <span className={`acm__curl-import-status acm__curl-import-status--${curlStatus.type}`}>
                  {curlStatus.message}
                </span>
              )}
            </div>
          </div>
        </details>
      )}
      <div className="acm__field-group">
        <div className="acm__label">Nome API *</div>
        <input className="acm__input" autoComplete="off" value={formData.name} onChange={e=>setFormData({...formData,name:e.target.value})} placeholder="Es: Nominatim Geocoding" />
      </div>
      <div className="acm__field-group">
        <div className="acm__label">Descrizione</div>
        <textarea className="acm__textarea" value={formData.description} onChange={e=>setFormData({...formData,description:e.target.value})} placeholder="Descrizione opzionale…" rows={2}/>
      </div>
      <div className="acm__form-row">
        <div className="acm__field-group acm__field-group--flex-3">
          <div className="acm__label">URL Endpoint *</div>
          <input className="acm__input acm__input--mono" autoComplete="off" value={formData.url} onChange={e=>setFormData({...formData,url:e.target.value})} placeholder="https://api.example.com/endpoint"/>
          <div className="acm__field-hint">Usa <code>{'{nome}'}</code> per un parametro nel path (es. <code>/geocode/{'{lat}'}/{'{lon}'}</code>). Solo HTTPS per URL esterni.</div>
        </div>
        <div className="acm__field-group acm__field-group--flex-1">
          <div className="acm__label">Metodo</div>
          <select className="acm__select" value={formData.method} onChange={e=>setFormData({...formData,method:e.target.value})}
            title="GET = parametri in query string · POST = parametri nel body JSON (o multipart se Processore File)">
            <option value="GET">GET</option>
            <option value="POST">POST</option>
            <option value="PUT">PUT</option>
            <option value="PATCH">PATCH</option>
            <option value="DELETE">DELETE</option>
          </select>
          <div className="acm__field-hint">GET = query · POST = body JSON</div>
        </div>
      </div>
      <details className="acm__details">
        <summary>⚙️ Opzioni avanzate</summary>
        <div className="acm__form-row acm__form-row--gap-md">
          {[['Rate Limit (req/s)','rate_limit',1,100],['Timeout (sec)','timeout',5,300],['Max Retry','retry_max',0,10]].map(([label,key,min,max])=>(
            <div key={key} className="acm__field-group">
              <div className="acm__label">{label}</div>
              <input className="acm__input" type="number" min={min} max={max} value={formData.options[key]} onChange={e=>setFormData({...formData,options:{...formData.options,[key]:parseInt(e.target.value)||min}})}/>
            </div>
          ))}
        </div>
      {formData.api_type==='enricher' && (
        <div className="acm__field-row acm__field-row--mt-sm">
          <label className="acm__label">🔀 Worker paralleli
            <span className="acm__hint-meta acm__hint-meta--strong">— richieste simultanée per riga</span>
          </label>
          <input type="number" className="acm__input" min={1} max={16}
            value={formData.options?.parallel_workers||1}
            onChange={e=>setFormData(f=>({...f,options:{...f.options,parallel_workers:Math.max(1,Math.min(16,parseInt(e.target.value)||1))}}))}/>
          <div className="acm__field-hint">1 = sequenziale · 2-4 consigliato · max 16. Rispetta sempre il Rate Limit.</div>
        </div>
      )}
      </details>
    </div>
  );
}
