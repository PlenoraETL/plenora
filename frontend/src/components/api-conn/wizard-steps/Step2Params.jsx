/**
 * Step2Params.jsx — Step 2 del wizard ApiConnection
 *
 * Parametri:
 *  - Variant `connection_type === 'rest_file'`: campi multipart
 *    (file_fields / static_fields / timeout / output_extension).
 *  - Variant standard: lista parametri + paginazione (per generator).
 *
 * Estratto in `FE-PHASE2-SPLIT-001B`. Presentational + riceve callback
 * di CRUD parametri dal wizard.
 */
import React from 'react';

export default function Step2Params({
  formData,
  setFormData,
  wizardStepError,
  addParameter,
  updateParameter,
  removeParameter,
}) {
  return (
    <div className="acm__wstep-content">
      {wizardStepError && (
        <div className="acm__validation-error">
          <span>⚠️</span><span>{wizardStepError}</span>
        </div>
      )}

      {/* ── REST FILE PROCESSOR: campi multipart + risposta binaria ── */}
      {formData.connection_type==='rest_file' ? (
        <>
          <div className="acm__info-box">📦 Configura i campi multipart che invierai al servizio REST e il formato del file di output.</div>

          <div className="acm__field-group">
            <div className="acm__label">Estensione file di output *</div>
            <input className="acm__input acm__input--mono acm__input--w-160"
              value={formData.response?.output_extension||'.zip'}
              onChange={e=>setFormData({...formData,response:{...(formData.response||{format:'binary_file'}),output_extension:e.target.value}})}
              placeholder=".zip"/>
            <div className="acm__field-hint">Estensione del file restituito dal servizio (es. .zip, .json, .pdf)</div>
          </div>

          <div className="acm__field-group acm__field-group--mt-lg">
            <div className="acm__label">📎 File da inviare (multipart)</div>
            <div className="acm__field-hint acm__field-hint--mb-sm">
              <strong>Sorgente nodo</strong>: il file arriva dal file_source collegato via edge (il "file principale", uno solo per nodo).
              <br/><strong>Extra (upload inline)</strong>: l'utente carica il file direttamente nel nodo canvas (es. Excel opzionale per il servizio IMDF).
            </div>
            <div className="acm__params-list">
              {(formData.request?.file_fields||[]).map((ff,i)=>(
                <div key={i} className="acm__field-card">
                  <div className="acm__param-row acm__param-row--inline">
                    <input className="acm__input" value={ff.name||''}
                      onChange={e=>{const arr=[...(formData.request?.file_fields||[])]; arr[i]={...arr[i],name:e.target.value}; setFormData({...formData,request:{...(formData.request||{}),file_fields:arr}});}}
                      placeholder="Nome campo (es: file, excel)"/>
                    <select className="acm__select acm__input--w-180"
                      value={ff.source||'node_input'}
                      onChange={e=>{const arr=[...(formData.request?.file_fields||[])]; arr[i]={...arr[i],source:e.target.value}; setFormData({...formData,request:{...(formData.request||{}),file_fields:arr}});}}>
                      <option value="node_input">Sorgente nodo (edge)</option>
                      <option value="extra_input">Extra (upload inline)</option>
                    </select>
                    <label className="acm__check-label-inline">
                      <input type="checkbox" checked={!!ff.required}
                        onChange={e=>{const arr=[...(formData.request?.file_fields||[])]; arr[i]={...arr[i],required:e.target.checked}; setFormData({...formData,request:{...(formData.request||{}),file_fields:arr}});}}/>
                      Obbligatorio
                    </label>
                    <button type="button" className="acm__del-btn"
                      onClick={()=>{const arr=[...(formData.request?.file_fields||[])]; arr.splice(i,1); setFormData({...formData,request:{...(formData.request||{}),file_fields:arr}});}}>🗑</button>
                  </div>
                  {ff.source === 'extra_input' && (
                    <div className="acm__param-row acm__param-row--inline">
                      <input className="acm__input acm__input--mono"
                        value={ff.accept||''}
                        onChange={e=>{const arr=[...(formData.request?.file_fields||[])]; arr[i]={...arr[i],accept:e.target.value}; setFormData({...formData,request:{...(formData.request||{}),file_fields:arr}});}}
                        placeholder="Accept (es: .xlsx,.xls) — opzionale"/>
                      <input className="acm__input"
                        value={ff.label||''}
                        onChange={e=>{const arr=[...(formData.request?.file_fields||[])]; arr[i]={...arr[i],label:e.target.value}; setFormData({...formData,request:{...(formData.request||{}),file_fields:arr}});}}
                        placeholder="Label UI (es: Excel arricchimento)"/>
                    </div>
                  )}
                </div>
              ))}
              <button type="button" className="acm__add-param-btn"
                onClick={()=>setFormData({...formData,request:{...(formData.request||{body_type:'multipart'}),file_fields:[...(formData.request?.file_fields||[]),{name:'',source:'node_input',required:false}]}})}>＋ Aggiungi campo file</button>
            </div>
          </div>

          <div className="acm__field-group acm__field-group--mt-lg">
            <div className="acm__label">📝 Campi statici (multipart)</div>
            <div className="acm__field-hint acm__field-hint--mb-sm">Valori fissi inviati a ogni chiamata. Se imposti un Content-Type il valore viene inviato come file in-memory (utile per JSON inline tipo <code>styles</code> e <code>mapping</code>).</div>
            <div className="acm__params-list">
              {(formData.request?.static_fields||[]).map((sf,i)=>(
                <div key={i} className="acm__field-card">
                  <div className="acm__param-row acm__param-row--inline">
                    <input className="acm__input" value={sf.name||''}
                      onChange={e=>{const arr=[...(formData.request?.static_fields||[])]; arr[i]={...arr[i],name:e.target.value}; setFormData({...formData,request:{...(formData.request||{}),static_fields:arr}});}}
                      placeholder="Nome campo (es: styles)"/>
                    <input className="acm__input acm__input--mono acm__input--w-200" value={sf.content_type||''}
                      onChange={e=>{const arr=[...(formData.request?.static_fields||[])]; arr[i]={...arr[i],content_type:e.target.value}; setFormData({...formData,request:{...(formData.request||{}),static_fields:arr}});}}
                      placeholder="Content-Type (opz.)"/>
                    <button type="button" className="acm__del-btn"
                      onClick={()=>{const arr=[...(formData.request?.static_fields||[])]; arr.splice(i,1); setFormData({...formData,request:{...(formData.request||{}),static_fields:arr}});}}>🗑</button>
                  </div>
                  <textarea className="acm__textarea acm__input--mono" rows={4}
                    value={sf.value||''}
                    onChange={e=>{const arr=[...(formData.request?.static_fields||[])]; arr[i]={...arr[i],value:e.target.value}; setFormData({...formData,request:{...(formData.request||{}),static_fields:arr}});}}
                    placeholder='Valore (es. {"layer":"foo","color":[255,0,0]})'/>
                </div>
              ))}
              <button type="button" className="acm__add-param-btn"
                onClick={()=>setFormData({...formData,request:{...(formData.request||{body_type:'multipart'}),static_fields:[...(formData.request?.static_fields||[]),{name:'',value:'',content_type:''}]}})}>＋ Aggiungi campo statico</button>
            </div>
          </div>

          <div className="acm__field-group acm__field-group--mt-lg">
            <div className="acm__label">⏱ Timeout (secondi)</div>
            <input type="number" className="acm__input acm__input--w-160" min={10} max={3600}
              value={formData.request?.timeout||600}
              onChange={e=>setFormData({...formData,request:{...(formData.request||{}),timeout:Math.max(10,Math.min(3600,parseInt(e.target.value)||600))}})}/>
            <div className="acm__field-hint">Tempo massimo di attesa della risposta. Default 600s (10 min).</div>
          </div>
        </>
      ) : (
      <>
      <div className="acm__info-box">{formData.api_type==='enricher'?<>📥 <strong>Mappato</strong> = da colonna &nbsp;|&nbsp; 📌 <strong>Fisso</strong> = costante</>:<>ℹ️ Parametri fissi per la chiamata API</>}</div>
      <div className="acm__params-list">
        {formData.parameters.map((p,i)=>(
          <div key={i} className="acm__param-row">
            <input className="acm__input" value={p.name} onChange={e=>updateParameter(i,'name',e.target.value)} placeholder="Nome"/>
            <select className="acm__select" value={p.type} onChange={e=>updateParameter(i,'type',e.target.value)}>
              <option value="string">Testo</option><option value="number">Numero</option><option value="boolean">Bool</option>
            </select>
            {formData.api_type==='enricher' && (
              <select className={`acm__select acm__select--${p.mode||'mapped'}`} value={p.mode||'mapped'} onChange={e=>updateParameter(i,'mode',e.target.value)}>
                <option value="mapped">📥 Mappato</option><option value="fixed">📌 Fisso</option>
              </select>
            )}
            {(p.mode==='fixed'||formData.api_type==='generator') && (
              <input className="acm__input" value={formData.static_parameters[p.name]||''} onChange={e=>setFormData({...formData,static_parameters:{...formData.static_parameters,[p.name]:e.target.value}})} placeholder="Valore fisso"/>
            )}
            <button type="button" className="acm__del-btn" onClick={()=>removeParameter(i)} aria-label="Rimuovi parametro">🗑</button>
          </div>
        ))}
        <button type="button" className="acm__add-param-btn" onClick={addParameter}>＋ Aggiungi parametro</button>
      </div>

      {formData.api_type==='generator' && (
        <div className="acm__pagination-section">
          <div className="acm__wlabel">📄 Paginazione</div>
          <div className="acm__param-row">
            <span className="acm__param-label">Tipo</span>
            <select className="acm__select" value={formData.pagination?.type||''} onChange={e=>setFormData({...formData,pagination:{...formData.pagination,type:e.target.value}})}>
              <option value="">Nessuna</option><option value="offset">Offset/Limit</option><option value="cursor">Cursor/Token</option><option value="link">Link (next URL)</option>
            </select>
          </div>
          {formData.pagination?.type && (
            <div className="acm__pagination-fields">
              <div className="acm__param-row">
                <span className="acm__param-label">Righe/pagina</span>
                <input className="acm__input acm__input--sm" type="number" min="1" max="10000" value={formData.pagination?.page_size??100} onChange={e=>setFormData({...formData,pagination:{...formData.pagination,page_size:parseInt(e.target.value)||100}})}/>
                <span className="acm__param-label">Max righe</span>
                <input className="acm__input acm__input--sm" type="number" min="1" value={formData.pagination?.max_rows??10000} onChange={e=>setFormData({...formData,pagination:{...formData.pagination,max_rows:parseInt(e.target.value)||10000}})}/>
              </div>
              {formData.pagination?.type==='offset' && (<>
                <div className="acm__param-row">
                  <span className="acm__param-label">Param offset</span>
                  <input className="acm__input" value={formData.pagination?.offset_param??'offset'} onChange={e=>setFormData({...formData,pagination:{...formData.pagination,offset_param:e.target.value}})} placeholder="offset"/>
                  <span className="acm__param-label">Param limit</span>
                  <input className="acm__input" value={formData.pagination?.limit_param??'limit'} onChange={e=>setFormData({...formData,pagination:{...formData.pagination,limit_param:e.target.value}})} placeholder="limit"/>
                </div>
              </>)}
              {formData.pagination?.type==='cursor' && (
                <div className="acm__param-row">
                  <span className="acm__param-label">Param cursor</span>
                  <input className="acm__input" value={formData.pagination?.cursor_param??'cursor'} onChange={e=>setFormData({...formData,pagination:{...formData.pagination,cursor_param:e.target.value}})} placeholder="cursor"/>
                  <span className="acm__param-label">Path risposta</span>
                  <input className="acm__input" value={formData.pagination?.cursor_path??'next_cursor'} onChange={e=>setFormData({...formData,pagination:{...formData.pagination,cursor_path:e.target.value}})} placeholder="next_cursor"/>
                </div>
              )}
              {formData.pagination?.type==='link' && (
                <div className="acm__param-row">
                  <span className="acm__param-label">Path URL next</span>
                  <input className="acm__input" value={formData.pagination?.link_path??'next'} onChange={e=>setFormData({...formData,pagination:{...formData.pagination,link_path:e.target.value}})} placeholder="next"/>
                </div>
              )}
            </div>
          )}
        </div>
      )}
      </>
      )}
    </div>
  );
}
