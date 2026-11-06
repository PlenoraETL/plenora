/**
 * Step5TestSave.jsx — Step 5 del wizard ApiConnection
 *
 * Test endpoint:
 *  - Input dati per parametri mappati (testParams).
 *  - Esecuzione test via callback `handleTest`.
 *  - Visualizzazione risultato (success/error).
 *  - Toggle "struttura dati complessa" -> ResponseConfigBuilder.
 *  - Selezione campi output (toggleOutputField/updateOutputColumn).
 *
 * Presentational + callbacks.
 */
import React from 'react';
import ResponseConfigBuilder from '../../panels/ResponseConfigBuilder';

const SELECTABLE_OUTPUT_TYPES = new Set(['string', 'integer', 'number', 'boolean', 'geojson']);

export default function Step5TestSave({
  formData,
  setFormData,
  testParams,
  setTestParams,
  testResult,
  testRawResponse,
  testing,
  showAdvancedConfig,
  setShowAdvancedConfig,
  handleTest,
  toggleOutputField,
  applySuggestedOutputMapping,
  clearOutputMapping,
  updateOutputColumn,
  handleResponseConfigChange,
}) {
  const suggestedCount = testResult?.suggested_output_mapping?.length
    || (testResult?.available_fields || [])
      .filter(field => SELECTABLE_OUTPUT_TYPES.has(field.type))
      .length;

  return (
    <div className="acm__wstep-content">
      <div className="acm__wlabel">📝 Dati di test</div>
      {formData.parameters.length===0 ? (
        <div className="acm__info-box">ℹ️ Nessun parametro — l'endpoint verrà chiamato direttamente.</div>
      ) : formData.parameters.map((p,i)=>{
        const isFixed = p.mode==='fixed'||formData.api_type==='generator';
        return (
          <div key={i} className="acm__test-param">
            <label className="acm__test-param-label">{p.name} <span className={`acm__mini-badge${isFixed?' acm__mini-badge--fixed':''}`}>{isFixed?'📌 fisso':'📥 mappato'}</span></label>
            <input type={p.type==='number'?'number':'text'} className={`acm__input${isFixed?' acm__input--dim':''}`} value={isFixed?(formData.static_parameters[p.name]||''):(testParams[p.name]||'')} onChange={e=>!isFixed&&setTestParams({...testParams,[p.name]:e.target.value})} disabled={isFixed} placeholder={isFixed?'':`Valore test per ${p.name}`}/>
          </div>
        );
      })}
      <button type="button" className="acm__test-run-btn" onClick={handleTest} disabled={testing}>
        {testing?'⏳ Test in corso…':'🚀 Esegui Test'}
      </button>

      {testResult && (
        <div className={`acm__test-result${testResult.success?' acm__test-result--ok':' acm__test-result--err'}`}>
          <div className="acm__test-result-title">{testResult.success?'✅ Test riuscito!':'❌ Test fallito'}</div>
          {testResult.success && (<>
            <label className="acm__toggle-label">
              <input type="checkbox" checked={showAdvancedConfig} onChange={e=>setShowAdvancedConfig(e.target.checked)}/>
              <span>🔧 Struttura dati complessa (array annidati)</span>
            </label>
            <div className="acm__field-hint acm__field-hint--mb-sm">Attiva solo se la risposta ha array annidati o richiede iterazione/trasformazioni. Per JSON piatto basta selezionare i campi sotto.</div>
            {showAdvancedConfig && testRawResponse && (
              <div className="acm__response-builder">
                <ResponseConfigBuilder sampleData={testRawResponse} initialConfig={{preset:formData.response_preset,root_path:formData.response_config?.root_path||'',iterate_on:formData.response_config?.iterate_on||[],mappings:formData.response_config?.mappings||[],transforms:formData.response_config?.transforms||[]}} onChange={handleResponseConfigChange}/>
              </div>
            )}
            {!showAdvancedConfig && testResult.available_fields?.length > 0 && (<>
              <div className="acm__qt-help acm__qt-help--sm">Seleziona i campi da importare:</div>
              <div className="acm__mapping-actions">
                {suggestedCount > 0 && (
                  <button type="button" className="acm__btn acm__btn--ok acm__btn--sm" onClick={applySuggestedOutputMapping}>
                    Applica suggeriti ({suggestedCount})
                  </button>
                )}
                {formData.output_mapping.length > 0 && (
                  <button type="button" className="acm__btn acm__btn--ghost acm__btn--sm" onClick={clearOutputMapping}>
                    Pulisci selezione
                  </button>
                )}
              </div>
              <div className="acm__fields-grid">
                {testResult.available_fields.map((f,i)=>{
                  const sel = formData.output_mapping.find(m=>m.path===f.path);
                  return (
                    <div key={i} className={`acm__field-item${sel?' acm__field-item--on':''}`} role="button" tabIndex={0} onClick={()=>toggleOutputField(f)} onKeyDown={e=>(e.key==='Enter'||e.key===' ')&&toggleOutputField(f)}>
                      <input type="checkbox" checked={!!sel} onChange={()=>{}} />
                      <div className="acm__field-info">
                        <span className="acm__field-name">{f.name}</span>
                        <span className={`acm__field-type acm__field-type--${f.type}`}>{f.type}</span>
                        {f.sample_value && <span className="acm__field-sample">{String(f.sample_value).substring(0,50)}</span>}
                      </div>
                    </div>
                  );
                })}
              </div>
              {formData.output_mapping.length > 0 && (
                <div className="acm__selected-fields">
                  <div className="acm__wlabel">Campi selezionati ({formData.output_mapping.length})</div>
                  <div className="acm__field-hint acm__field-hint--mb-sm"><strong>path nella risposta</strong> → <strong>nome colonna</strong> nel risultato. Un campo che è una lista può generare più righe.</div>
                  {formData.output_mapping.map((m,i)=>(
                    <div key={i} className="acm__selected-row">
                      <span className="acm__selected-path">{m.path}</span>
                      <span className="acm__selected-arrow">→</span>
                      <input className="acm__input" value={m.column} onChange={e=>updateOutputColumn(m.path,e.target.value)}/>
                    </div>
                  ))}
                </div>
              )}
            </>)}
          </>)}
          {!testResult.success && <div className="acm__test-err-msg">{testResult.error}</div>}
        </div>
      )}
    </div>
  );
}
