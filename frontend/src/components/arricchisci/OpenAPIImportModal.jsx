/**
 * OpenAPIImportModal.jsx — Importazione schema OpenAPI/Swagger
 * Redesign Plenora design system
 */
import React, { useState } from 'react';
import { fetchOpenApiSchema } from '../../api/apiConnections';
import ModalWrapper, { ModalButton } from '../common/ModalWrapper';
import './OpenAPIImportModal.css';

function OpenAPIImportModal({ onClose, onImport }) {
  const [importMode, setImportMode] = useState('url');
  const [url, setUrl] = useState('');
  const [pastedSchema, setPastedSchema] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [parsedSpec, setParsedSpec] = useState(null);
  const [selectedEndpoints, setSelectedEndpoints] = useState([]);
  const [expandedEndpoint, setExpandedEndpoint] = useState(null);
  const [selectedFields, setSelectedFields] = useState({});
  const [manualBaseUrl, setManualBaseUrl] = useState('');
  const [authConfig, setAuthConfig] = useState({ type: 'none', key_name: 'X-API-Key', key_value: '', location: 'header' });

  // ── Parsing ────────────────────────────────────────────────────────────
  const parseOpenAPISchema = (schema) => {
    try {
      const spec = typeof schema === 'string' ? JSON.parse(schema) : schema;
      const baseUrl = spec.servers?.[0]?.url || '';
      const apiName = spec.info?.title || 'API Importata';
      const apiDescription = spec.info?.description || '';

      const resolveRef = (obj) => {
        if (!obj || typeof obj !== 'object') return obj;
        if (obj['$ref']) {
          const path = obj['$ref'].replace('#/', '').split('/');
          let resolved = spec;
          for (const part of path) resolved = resolved?.[part];
          return resolved || obj;
        }
        return obj;
      };

      const extractOutputFields = (schema, prefix, fields, depth = 0) => {
        if (depth > 5 || !schema) return;
        schema = resolveRef(schema);
        if (schema.type === 'object' && schema.properties) {
          Object.entries(schema.properties).forEach(([name, prop]) => {
            prop = resolveRef(prop);
            const path = prefix ? `${prefix}.${name}` : name;
            if (prop.type === 'object' || prop.properties) { extractOutputFields(prop, path, fields, depth + 1); }
            else if (prop.type === 'array' && prop.items) {
              const arrayPath = `${path}[*]`;
              const items = resolveRef(prop.items);
              if (items.type === 'object' || items.properties) extractOutputFields(items, arrayPath, fields, depth + 1);
              else fields.push({ path: arrayPath, name, type: items.type || 'string' });
            } else { fields.push({ path, name, type: prop.type || 'string' }); }
          });
        } else if (schema.type === 'array' && schema.items) {
          extractOutputFields(schema.items, `${prefix}[*]`, fields, depth + 1);
        }
      };

      const endpoints = [];
      Object.entries(spec.paths || {}).forEach(([path, methods]) => {
        Object.entries(methods).forEach(([method, details]) => {
          if (!['get','post','put','delete'].includes(method.toLowerCase())) return;
          const parameters = [];
          (details.parameters || []).forEach(p => parameters.push({ name:p.name, type:p.schema?.type||'string', required:p.required||false, location:p.in, description:p.description||'' }));
          if (details.requestBody?.content?.['application/json']?.schema) {
            const bs = details.requestBody.content['application/json'].schema;
            if (bs.properties) Object.entries(bs.properties).forEach(([pn, ps]) => parameters.push({ name:pn, type:ps.type||'string', required:(bs.required||[]).includes(pn), location:'body', description:ps.description||'' }));
          }
          const outputFields = [];
          let rs = details.responses?.['200']?.content?.['application/json']?.schema;
          if (rs) { rs = resolveRef(rs); extractOutputFields(rs, '', outputFields); }
          endpoints.push({ id:`${method.toUpperCase()}_${path}`, path, method:method.toUpperCase(), summary:details.summary||`${method.toUpperCase()} ${path}`, description:details.description||'', parameters, outputFields, selected:false });
        });
      });
      return { baseUrl, apiName, apiDescription, endpoints, securitySchemes: spec.components?.securitySchemes||{} };
    } catch (e) { throw new Error(`Errore parsing OpenAPI: ${e.message}`); }
  };

  const autoDetectAuth = (schemes, baseUrl) => {
    for (const key of Object.keys(schemes)) {
      const s = schemes[key];
      if (s.type === 'oauth2') {
        const flow = s.flows?.password || s.flows?.clientCredentials;
        if (flow) {
          let tokenUrl = flow.tokenUrl || '';
          if (tokenUrl && !tokenUrl.startsWith('http') && baseUrl) {
            const base = baseUrl.endsWith('/') ? baseUrl.slice(0,-1) : baseUrl;
            tokenUrl = `${base}${tokenUrl.startsWith('/')?tokenUrl:'/'+tokenUrl}`;
          }
          setAuthConfig(prev => ({ ...prev, type: s.flows?.password ? 'oauth2_password' : 'oauth2_client_credentials', token_url: tokenUrl }));
          return;
        }
      }
      if (s.type === 'apiKey') { setAuthConfig(prev => ({ ...prev, type:'api_key', key_name:s.name||'X-API-Key' })); return; }
      if (s.type === 'http' && s.scheme === 'basic') { setAuthConfig(prev => ({ ...prev, type:'basic_auth' })); return; }
    }
  };

  const handleFetchSchema = async () => {
    setLoading(true); setError(null); setParsedSpec(null);
    try {
      let schema;
      if (importMode === 'url') {
        if (!url) throw new Error('Inserisci un URL');
        schema = await fetchOpenApiSchema(url);
      } else {
        if (!pastedSchema) throw new Error('Incolla lo schema OpenAPI');
        schema = JSON.parse(pastedSchema);
      }
      const parsed = parseOpenAPISchema(schema);
      if (importMode === 'url') {
        try {
          const origin = new URL(url).origin;
          if (!parsed.baseUrl) parsed.baseUrl = origin;
          else if (!parsed.baseUrl.startsWith('http')) parsed.baseUrl = `${origin}${parsed.baseUrl}`;
        } catch(_) {}
      }
      setParsedSpec(parsed); setSelectedEndpoints([]);
      autoDetectAuth(parsed.securitySchemes||{}, parsed.baseUrl);
    } catch(e) { setError(e.message); }
    finally { setLoading(false); }
  };

  const handleFileUpload = async (e) => {
    const file = e.target.files[0]; if (!file) return;
    setLoading(true); setError(null);
    try {
      const schema = JSON.parse(await file.text());
      const parsed = parseOpenAPISchema(schema);
      setParsedSpec(parsed); setSelectedEndpoints([]);
    } catch(e) { setError(`Errore lettura file: ${e.message}`); }
    finally { setLoading(false); }
  };

  const toggleEndpoint = (id) => {
    setSelectedEndpoints(prev => {
      const next = prev.includes(id) ? prev.filter(x => x !== id) : [...prev, id];
      if (!prev.includes(id)) {
        const ep = parsedSpec?.endpoints.find(e => e.id === id);
        if (ep && !selectedFields[id]) setSelectedFields(sf => ({ ...sf, [id]: new Set(ep.outputFields.map(f => f.path)) }));
      }
      return next;
    });
  };
  const toggleField = (eid, path) => setSelectedFields(prev => { const s = new Set(prev[eid]||[]); s.has(path)?s.delete(path):s.add(path); return {...prev,[eid]:s}; });
  const selectAllEndpoints = () => selectedEndpoints.length===parsedSpec.endpoints.length ? setSelectedEndpoints([]) : setSelectedEndpoints(parsedSpec.endpoints.map(e=>e.id));

  const handleImport = () => {
    if (!parsedSpec || !selectedEndpoints.length) return;
    onImport(parsedSpec.endpoints.filter(ep => selectedEndpoints.includes(ep.id)).map(ep => {
      const chosen = selectedFields[ep.id] || new Set(ep.outputFields.map(f=>f.path));
      return { name:ep.summary||`${ep.method} ${ep.path}`, description:ep.description||`Endpoint da ${parsedSpec.apiName}`, api_type:'enricher', category:'custom', connection_type:'openapi', url:`${parsedSpec.baseUrl||manualBaseUrl}${ep.path}`, method:ep.method, auth:authConfig, parameters:ep.parameters.map(p=>({name:p.name,type:p.type==='integer'?'number':p.type,required:p.required,mode:'mapped',location:p.location})), static_parameters:{}, schema_fields:ep.outputFields.map(f=>({path:f.path,name:f.name,type:f.type})), output_mapping:ep.outputFields.filter(f=>chosen.has(f.path)).map(f=>({path:f.path,column:f.name,type:f.type})), options:{rate_limit:5,timeout:30,retry_on_error:true,retry_max:3} };
    }));
  };

  const METHOD_COLOR = { GET:'oai__m--get', POST:'oai__m--post', PUT:'oai__m--put', DELETE:'oai__m--del' };

  return (
    <ModalWrapper
      isOpen={true}
      onClose={onClose}
      title="Import da OpenAPI Schema"
      subtitle="Importa endpoint da schema OpenAPI 3.0 / Swagger"
      icon="📋"
      size="lg"
      footer={
        <>
          {parsedSpec && (
            <span style={{ marginRight: 'auto', fontSize: 13, color: 'var(--plen-text-sub)' }}>
              {selectedEndpoints.length} endpoint selezionati
            </span>
          )}
          <ModalButton variant="secondary" onClick={onClose}>Annulla</ModalButton>
          {parsedSpec && (
            <ModalButton variant="primary" onClick={handleImport} disabled={!selectedEndpoints.length}>
              📥 Importa {selectedEndpoints.length} endpoint
            </ModalButton>
          )}
        </>
      }
    >
        <div className="oai__body">

          {/* ── Step A: Input ── */}
          {!parsedSpec ? (
            <div className="oai__section">
              <div className="oai__tabs">
                {[['url','🌐','Da URL'],['file','📁','Da File'],['paste','📋','Incolla JSON']].map(([id,icon,label])=>(
                  <button key={id} type="button" className={`oai__tab${importMode===id?' oai__tab--active':''}`} onClick={()=>setImportMode(id)}>
                    {icon} {label}
                  </button>
                ))}
              </div>

              {importMode === 'url' && (
                <div className="oai__field-group">
                  <div className="oai__label">URL Schema OpenAPI</div>
                  <input className="oai__input oai__input--mono" value={url} onChange={e=>setUrl(e.target.value)} placeholder="https://api.example.com/openapi.json" autoComplete="off"/>
                  <div className="oai__hint">Es: <code>http://analisi.re-val.it:5001/openapi.json</code></div>
                </div>
              )}
              {importMode === 'file' && (
                <div className="oai__field-group">
                  <div className="oai__label">Carica file OpenAPI (JSON/YAML)</div>
                  <label className="oai__file-drop">
                    <input type="file" accept=".json,.yaml,.yml" onChange={handleFileUpload} style={{display:'none'}}/>
                    <span className="oai__file-icon">📂</span>
                    <span>Clicca per selezionare o trascina il file</span>
                    <span className="oai__file-hint">.json / .yaml / .yml</span>
                  </label>
                </div>
              )}
              {importMode === 'paste' && (
                <div className="oai__field-group">
                  <div className="oai__label">Incolla schema OpenAPI</div>
                  <textarea className="oai__textarea oai__textarea--tall" value={pastedSchema} onChange={e=>setPastedSchema(e.target.value)} placeholder={'{\n  "openapi": "3.0.0",\n  ...\n}'} rows={10}/>
                </div>
              )}

              {error && <div className="oai__error">❌ {error}</div>}

              {importMode !== 'file' && (
                <button type="button" className="oai__fetch-btn" onClick={handleFetchSchema}
                  disabled={loading || (importMode==='url'&&!url) || (importMode==='paste'&&!pastedSchema)}>
                  {loading ? '⏳ Analisi in corso…' : '🔍 Analizza Schema'}
                </button>
              )}
            </div>

          ) : (
            // ── Step B: Endpoint selection ──
            <div className="oai__section">

              {/* Spec info */}
              <div className="oai__spec-card">
                <div className="oai__spec-name">{parsedSpec.apiName}</div>
                {parsedSpec.apiDescription && <div className="oai__spec-desc">{parsedSpec.apiDescription}</div>}
                {parsedSpec.baseUrl ? (
                  <div className="oai__spec-url"><span className="oai__spec-url-label">Base URL</span><code>{parsedSpec.baseUrl}</code></div>
                ) : (
                  <div className="oai__field-group" style={{marginTop:10}}>
                    <div className="oai__label">⚠ Base URL non rilevata — inserisci manualmente</div>
                    <input className="oai__input oai__input--mono" value={manualBaseUrl} onChange={e=>setManualBaseUrl(e.target.value)} placeholder="https://api.example.com"/>
                  </div>
                )}
              </div>

              {/* Auth */}
              <div className="oai__auth-box">
                <div className="oai__section-title">🔐 Autenticazione</div>
                <div className="oai__auth-opts">
                  {[['none','🔓','Nessuna'],['api_key','🔑','API Key'],['basic_auth','👤','Username/Pass'],['oauth2_password','🔐','OAuth2 Pwd'],['oauth2_client_credentials','🏢','OAuth2 CC']].map(([v,icon,label])=>(
                    <label key={v} className={`oai__auth-opt${authConfig.type===v?' oai__auth-opt--on':''}`}>
                      <input type="radio" name="oai-auth" checked={authConfig.type===v} onChange={()=>setAuthConfig({...authConfig,type:v})}/>
                      <span>{icon}</span> {label}
                    </label>
                  ))}
                </div>
                {authConfig.type==='api_key' && (
                  <div className="oai__auth-fields">
                    <input className="oai__input" value={authConfig.key_name} onChange={e=>setAuthConfig({...authConfig,key_name:e.target.value})} placeholder="Nome header"/>
                    <input type="password" className="oai__input" value={authConfig.key_value} onChange={e=>setAuthConfig({...authConfig,key_value:e.target.value})} placeholder="API Key"/>
                  </div>
                )}
                {authConfig.type==='basic_auth' && (
                  <div className="oai__auth-fields">
                    <input className="oai__input" value={authConfig.username||''} onChange={e=>setAuthConfig({...authConfig,username:e.target.value})} placeholder="Username"/>
                    <input type="password" className="oai__input" value={authConfig.password||''} onChange={e=>setAuthConfig({...authConfig,password:e.target.value})} placeholder="Password"/>
                  </div>
                )}
                {authConfig.type==='oauth2_password' && (
                  <div className="oai__auth-fields">
                    <input className="oai__input oai__input--mono" value={authConfig.token_url||''} onChange={e=>setAuthConfig({...authConfig,token_url:e.target.value})} placeholder="Token URL"/>
                    <input className="oai__input" value={authConfig.username||''} onChange={e=>setAuthConfig({...authConfig,username:e.target.value})} placeholder="Username"/>
                    <input type="password" className="oai__input" value={authConfig.password||''} onChange={e=>setAuthConfig({...authConfig,password:e.target.value})} placeholder="Password"/>
                  </div>
                )}
                {authConfig.type==='oauth2_client_credentials' && (
                  <div className="oai__auth-fields">
                    <input className="oai__input oai__input--mono" value={authConfig.token_url||''} onChange={e=>setAuthConfig({...authConfig,token_url:e.target.value})} placeholder="Token URL"/>
                    <input className="oai__input" value={authConfig.client_id||''} onChange={e=>setAuthConfig({...authConfig,client_id:e.target.value})} placeholder="Client ID"/>
                    <input type="password" className="oai__input" value={authConfig.client_secret||''} onChange={e=>setAuthConfig({...authConfig,client_secret:e.target.value})} placeholder="Client Secret"/>
                  </div>
                )}
              </div>

              {/* Endpoint list */}
              <div className="oai__endpoints-header">
                <div className="oai__section-title">📡 Endpoint ({parsedSpec.endpoints.length})</div>
                <button type="button" className="oai__sel-all-btn" onClick={selectAllEndpoints}>
                  {selectedEndpoints.length===parsedSpec.endpoints.length ? 'Deseleziona tutti' : 'Seleziona tutti'}
                </button>
              </div>

              <div className="oai__endpoints-list">
                {parsedSpec.endpoints.map(ep => {
                  const isSel = selectedEndpoints.includes(ep.id);
                  const isExp = expandedEndpoint === ep.id;
                  const fieldSet = selectedFields[ep.id] || new Set(ep.outputFields.map(f=>f.path));
                  return (
                    <div key={ep.id} className={`oai__endpoint${isSel?' oai__endpoint--on':''}`}>
                      <div className="oai__endpoint-row" role="button" tabIndex={0}
                        onClick={()=>toggleEndpoint(ep.id)}
                        onKeyDown={e=>(e.key==='Enter'||e.key===' ')&&toggleEndpoint(ep.id)}>
                        <input type="checkbox" checked={isSel} onChange={()=>{}} onClick={e=>e.stopPropagation()}/>
                        <span className={`oai__method ${METHOD_COLOR[ep.method]||''}`}>{ep.method}</span>
                        <span className="oai__ep-path">{ep.path}</span>
                        <span className="oai__ep-summary">{ep.summary}</span>
                        <div className="oai__ep-meta">
                          <span className="oai__ep-count">{ep.parameters.length}p</span>
                          {isSel && ep.outputFields.length > 0 && (
                            <button type="button" className="oai__fields-toggle"
                              onClick={e=>{e.stopPropagation(); setExpandedEndpoint(isExp?null:ep.id);}}>
                              {fieldSet.size}/{ep.outputFields.length} campi {isExp?'▲':'▼'}
                            </button>
                          )}
                          {!isSel && <span className="oai__ep-count">{ep.outputFields.length}f</span>}
                        </div>
                      </div>
                      {isSel && isExp && ep.outputFields.length > 0 && (
                        <div className="oai__fields-panel">
                          <div className="oai__fields-panel-header">
                            <span>Campi da estrarre</span>
                            <button type="button" className="oai__fields-action" onClick={()=>setSelectedFields(sf=>({...sf,[ep.id]:new Set(ep.outputFields.map(f=>f.path))}))}>Tutti</button>
                            <button type="button" className="oai__fields-action" onClick={()=>setSelectedFields(sf=>({...sf,[ep.id]:new Set()}))}>Nessuno</button>
                          </div>
                          <div className="oai__fields-grid">
                            {ep.outputFields.map(f => (
                              <label key={f.path} className="oai__field-item">
                                <input type="checkbox" checked={fieldSet.has(f.path)} onChange={()=>toggleField(ep.id,f.path)}/>
                                <span className="oai__field-name">{f.name}</span>
                                <span className={`oai__field-type oai__ft--${f.type}`}>{f.type}</span>
                              </label>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>

              <button type="button" className="oai__back-btn" onClick={()=>{ setParsedSpec(null); setSelectedEndpoints([]); setSelectedFields({}); setManualBaseUrl(''); setAuthConfig({type:'none',key_name:'X-API-Key',key_value:'',location:'header'}); }}>
                ← Torna indietro
              </button>
            </div>
          )}
        </div>
    </ModalWrapper>
  );
}

export default OpenAPIImportModal;
