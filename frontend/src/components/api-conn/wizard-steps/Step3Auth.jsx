/**
 * Step3Auth.jsx — Step 3 del wizard ApiConnection
 *
 * Autenticazione (none / api_key / basic_auth / oauth2_password /
 * oauth2_client_credentials / arcgis_token) + headers personalizzati.
 *
 * Presentational + riceve callback CRUD custom headers dal wizard.
 */
import React from 'react';

export default function Step3Auth({
  formData,
  setFormData,
  customHeadersList,
  addCustomHeader,
  updateCustomHeader,
  removeCustomHeader,
}) {
  return (
    <div className="acm__wstep-content">
      <div className="acm__wlabel">🔐 Autenticazione</div>
      <div className="acm__auth-opts">
        {[{value:'none',label:'Nessuna',icon:'🔓'},{value:'api_key',label:'API Key',icon:'🔑'},{value:'basic_auth',label:'Basic Auth',icon:'👤'},{value:'oauth2_password',label:'OAuth2 Password',icon:'🔐'},{value:'oauth2_client_credentials',label:'OAuth2 Client Creds',icon:'🏢'},{value:'arcgis_token',label:'ArcGIS Token',icon:'🌐'}].map(opt=>(
          <label key={opt.value} className={`acm__auth-opt${formData.auth.type===opt.value?' acm__auth-opt--on':''}`}>
            <input type="radio" name="authType" value={opt.value} checked={formData.auth.type===opt.value} onChange={e=>setFormData({...formData,auth:{type:e.target.value}})}/>
            <span>{opt.icon}</span> {opt.label}
          </label>
        ))}
      </div>

      {formData.auth.type==='api_key' && (
        <div className="acm__auth-config">
          <div className="acm__form-row">
            <div className="acm__field-group"><div className="acm__label">Nome chiave *</div><input className="acm__input" value={formData.auth.key_name||''} onChange={e=>setFormData({...formData,auth:{...formData.auth,key_name:e.target.value}})} placeholder="X-API-Key"/></div>
            <div className="acm__field-group"><div className="acm__label">Valore *</div><input type="password" className="acm__input" value={formData.auth.key_value||''} onChange={e=>setFormData({...formData,auth:{...formData.auth,key_value:e.target.value}})} placeholder="La tua API key"/></div>
          </div>
          <div className="acm__auth-pos">
            {['header','query'].map(loc=>(
              <label key={loc} className={`acm__auth-opt${(formData.auth.location||'header')===loc?' acm__auth-opt--on':''}`}>
                <input type="radio" name="keyLoc" value={loc} checked={(formData.auth.location||'header')===loc} onChange={e=>setFormData({...formData,auth:{...formData.auth,location:e.target.value}})}/> {loc==='header'?'Header':'Query param'}
              </label>
            ))}
          </div>
        </div>
      )}
      {(formData.auth.type==='oauth2_password'||formData.auth.type==='basic_auth') && (
        <div className="acm__auth-config">
          {formData.auth.type==='oauth2_password' && <div className="acm__field-group"><div className="acm__label">Token URL *</div><input className="acm__input acm__input--mono" value={formData.auth.token_url||''} onChange={e=>setFormData({...formData,auth:{...formData.auth,token_url:e.target.value}})} placeholder="https://…/token"/></div>}
          <div className="acm__form-row">
            <div className="acm__field-group"><div className="acm__label">Username *</div><input className="acm__input" value={formData.auth.username||''} onChange={e=>setFormData({...formData,auth:{...formData.auth,username:e.target.value}})} placeholder="Username"/></div>
            <div className="acm__field-group"><div className="acm__label">Password *</div><input type="password" className="acm__input" value={formData.auth.password||''} onChange={e=>setFormData({...formData,auth:{...formData.auth,password:e.target.value}})} placeholder="••••••••"/></div>
          </div>
        </div>
      )}
      {formData.auth.type==='oauth2_client_credentials' && (
        <div className="acm__auth-config">
          <div className="acm__field-group"><div className="acm__label">Token URL *</div><input className="acm__input acm__input--mono" value={formData.auth.token_url||''} onChange={e=>setFormData({...formData,auth:{...formData.auth,token_url:e.target.value}})} placeholder="https://auth.example.com/oauth/token"/></div>
          <div className="acm__form-row">
            <div className="acm__field-group"><div className="acm__label">Client ID *</div><input className="acm__input" value={formData.auth.client_id||''} onChange={e=>setFormData({...formData,auth:{...formData.auth,client_id:e.target.value}})} placeholder="client_id"/></div>
            <div className="acm__field-group"><div className="acm__label">Client Secret *</div><input type="password" className="acm__input" value={formData.auth.client_secret||''} onChange={e=>setFormData({...formData,auth:{...formData.auth,client_secret:e.target.value}})} placeholder="••••••••"/></div>
          </div>
          <div className="acm__field-group"><div className="acm__label">Scope (opzionale)</div><input className="acm__input" value={formData.auth.scope||''} onChange={e=>setFormData({...formData,auth:{...formData.auth,scope:e.target.value}})} placeholder="read write"/></div>
        </div>
      )}
      {formData.auth.type==='arcgis_token' && (
        <div className="acm__auth-config">
          <div className="acm__info-box acm__info-box--mb-md">
            ℹ️ ArcGIS Token Service (ESRI). Il token viene generato chiamando <code>generateToken</code>,
            cachato in memoria, e iniettato come query param <code>?token=...</code> nelle chiamate API.
            Auto-renewal alla scadenza.
          </div>
          <div className="acm__field-group">
            <div className="acm__label">Token URL *</div>
            <input className="acm__input acm__input--mono"
              value={formData.auth.token_url||''}
              onChange={e=>setFormData({...formData,auth:{...formData.auth,token_url:e.target.value}})}
              placeholder="https://server.tld/arcgis/admin/generateToken"/>
          </div>
          <div className="acm__form-row">
            <div className="acm__field-group">
              <div className="acm__label">Username *</div>
              <input className="acm__input"
                value={formData.auth.username||''}
                onChange={e=>setFormData({...formData,auth:{...formData.auth,username:e.target.value}})}
                placeholder="utente"/>
            </div>
            <div className="acm__field-group">
              <div className="acm__label">Password *</div>
              <input type="password" className="acm__input"
                value={formData.auth.password||''}
                onChange={e=>setFormData({...formData,auth:{...formData.auth,password:e.target.value}})}
                placeholder="••••••••"/>
            </div>
          </div>
          <div className="acm__form-row">
            <div className="acm__field-group">
              <div className="acm__label">Client</div>
              <select className="acm__input"
                value={formData.auth.client||'requestip'}
                onChange={e=>setFormData({...formData,auth:{...formData.auth,client:e.target.value}})}>
                <option value="requestip">requestip (IP del server URBY)</option>
                <option value="referer">referer (URL specifico)</option>
                <option value="ip">ip (IP custom)</option>
              </select>
            </div>
            <div className="acm__field-group">
              <div className="acm__label">Scadenza (minuti)</div>
              <input type="number" min={1} max={1440} className="acm__input"
                value={formData.auth.expiration||60}
                onChange={e=>setFormData({...formData,auth:{...formData.auth,expiration:parseInt(e.target.value)||60}})}/>
            </div>
          </div>
          {formData.auth.client==='referer' && (
            <div className="acm__field-group">
              <div className="acm__label">Referer URL *</div>
              <input className="acm__input acm__input--mono"
                value={formData.auth.referer||''}
                onChange={e=>setFormData({...formData,auth:{...formData.auth,referer:e.target.value}})}
                placeholder="https://myapp.example.com"/>
            </div>
          )}
          {formData.auth.client==='ip' && (
            <div className="acm__field-group">
              <div className="acm__label">IP *</div>
              <input className="acm__input acm__input--mono"
                value={formData.auth.ip||''}
                onChange={e=>setFormData({...formData,auth:{...formData.auth,ip:e.target.value}})}
                placeholder="123.45.67.89"/>
            </div>
          )}
        </div>
      )}

      <div className="acm__wlabel acm__wlabel--mt-lg">📋 Headers personalizzati</div>
      <div className="acm__params-list">
        {customHeadersList.map((h,i)=>(
          <div key={i} className="acm__param-row">
            <input className="acm__input" value={h.key} onChange={e=>updateCustomHeader(i,'key',e.target.value)} placeholder="Nome header"/>
            <input className="acm__input" value={h.value} onChange={e=>updateCustomHeader(i,'value',e.target.value)} placeholder="Valore"/>
            <button type="button" className="acm__del-btn" onClick={()=>removeCustomHeader(i)} aria-label="Rimuovi header">🗑</button>
          </div>
        ))}
        <button type="button" className="acm__add-param-btn" onClick={addCustomHeader}>＋ Aggiungi header</button>
      </div>
    </div>
  );
}
