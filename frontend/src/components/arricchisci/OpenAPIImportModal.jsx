/**
 * OpenAPIImportModal.jsx — Importazione schema OpenAPI/Swagger
 * Redesign Plenora design system
 *
 * Lettura e normalizzazione della specifica (OpenAPI 3.x e Swagger 2.0, JSON
 * o YAML, da URL, file o testo): solo `utils/openapiImport` (Fase 5, P10).
 * Questo componente sceglie endpoint, campi e autenticazione.
 */
import React, { useState } from 'react';
import { fetchOpenApiSchema } from '../../api/apiConnections';
import ModalWrapper, { ModalButton } from '../common/ModalWrapper';
import { humanizeError } from '../../utils/errorMessages';
import { normalizeOpenApi, buildConnectionPayloads, needsBaseUrl, IMPORT_AUTH_OPTIONS } from '../../utils/openapiImport';
import './OpenAPIImportModal.css';

const NO_AUTH = { type: 'none' };

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
  const [authConfig, setAuthConfig] = useState(NO_AUTH);

  // Stessa strada per URL, file e testo: la specifica normalizzata, con la
  // sua proposta di autenticazione.
  const applySpec = (spec) => {
    setParsedSpec(spec);
    setSelectedEndpoints([]);
    setSelectedFields({});
    setAuthConfig(spec.auth);
  };

  const handleFetchSchema = async () => {
    setLoading(true); setError(null); setParsedSpec(null);
    try {
      if (importMode === 'url') {
        if (!url) throw new Error('Inserisci un URL');
        applySpec(normalizeOpenApi(await fetchOpenApiSchema(url), { sourceUrl: url }));
      } else {
        if (!pastedSchema) throw new Error('Incolla lo schema OpenAPI');
        applySpec(normalizeOpenApi(pastedSchema));
      }
    } catch (e) { setError(humanizeError(e).message); }
    finally { setLoading(false); }
  };

  const handleFileUpload = async (e) => {
    const file = e.target.files[0]; if (!file) return;
    setLoading(true); setError(null);
    try {
      applySpec(normalizeOpenApi(await file.text()));
    } catch (err) { setError(`File «${file.name}» non valido: ${humanizeError(err).message}`); }
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

  const chooseAuth = (option) => setAuthConfig(
    parsedSpec?.auth.type === option.type ? parsedSpec.auth : { type: option.type, ...option.blank },
  );
  const setAuth = (patch) => setAuthConfig(prev => ({ ...prev, ...patch }));

  const baseUrlMissing = !!parsedSpec && parsedSpec.endpoints
    .some(ep => selectedEndpoints.includes(ep.id) && needsBaseUrl(ep)) && !manualBaseUrl.trim();
  const showManualBase = !!parsedSpec && parsedSpec.endpoints.some(needsBaseUrl);
  const proposedFrom = parsedSpec?.authCandidates.find(c => c.auth)?.name;

  const handleImport = () => {
    if (!parsedSpec || !selectedEndpoints.length || baseUrlMissing) return;
    onImport(buildConnectionPayloads(parsedSpec, {
      endpointIds: selectedEndpoints,
      fieldsByEndpoint: selectedFields,
      auth: authConfig,
      manualBaseUrl: manualBaseUrl.trim(),
    }));
  };

  const METHOD_COLOR = { GET:'oai__m--get', POST:'oai__m--post', PUT:'oai__m--put', DELETE:'oai__m--del' };

  return (
    <ModalWrapper
      isOpen={true}
      onClose={onClose}
      title="Import da OpenAPI Schema"
      subtitle="Importa endpoint da OpenAPI 3.x o Swagger 2.0 (JSON o YAML)"
      icon="📋"
      size="lg"
      footer={
        <>
          {parsedSpec && (
            <span className="oai__footer-count">
              {selectedEndpoints.length} endpoint selezionati
            </span>
          )}
          <ModalButton variant="secondary" onClick={onClose}>Annulla</ModalButton>
          {parsedSpec && (
            <ModalButton variant="primary" onClick={handleImport} disabled={!selectedEndpoints.length || baseUrlMissing}>
              📥 Importa {selectedEndpoints.length} endpoint
            </ModalButton>
          )}
        </>
      }
    >
        <div className="oai__body">
{!parsedSpec ? (
            <div className="oai__section">
              <div className="oai__tabs">
                {[['url','🌐','Da URL'],['file','📁','Da File'],['paste','📋','Incolla JSON/YAML']].map(([id,icon,label])=>(
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
                    <input type="file" accept=".json,.yaml,.yml" onChange={handleFileUpload} className="oai__file-input--hidden"/>
                    <span className="oai__file-icon">📂</span>
                    <span>Clicca per selezionare o trascina il file</span>
                    <span className="oai__file-hint">.json / .yaml / .yml</span>
                  </label>
                </div>
              )}
              {importMode === 'paste' && (
                <div className="oai__field-group">
                  <div className="oai__label">Incolla schema OpenAPI</div>
                  <textarea className="oai__textarea oai__textarea--tall" value={pastedSchema} onChange={e=>setPastedSchema(e.target.value)} placeholder={'openapi: 3.0.0\ninfo:\n  title: …\npaths: …'} rows={10}/>
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
<div className="oai__section">

              {/* Spec info */}
              <div className="oai__spec-card">
                <div className="oai__spec-name">{parsedSpec.title}</div>
                {parsedSpec.description && <div className="oai__spec-desc">{parsedSpec.description}</div>}
                {parsedSpec.baseUrl && !needsBaseUrl({ server: parsedSpec.baseUrl }) && (
                  <div className="oai__spec-url"><span className="oai__spec-url-label">Base URL</span><code>{parsedSpec.baseUrl}</code></div>
                )}
                {showManualBase && (
                  <div className="oai__field-group oai__field-group--mt-md">
                    <label className="oai__label" htmlFor="oai-manual-base">⚠ Base URL non rilevata — inserisci manualmente</label>
                    <input id="oai-manual-base" className="oai__input oai__input--mono" value={manualBaseUrl} onChange={e=>setManualBaseUrl(e.target.value)} placeholder="https://api.example.com"/>
                    {parsedSpec.baseUrl && <div className="oai__hint">Il server della specifica è relativo: <code>{parsedSpec.baseUrl}</code></div>}
                  </div>
                )}
              </div>

              {parsedSpec.warnings.length > 0 && (
                <ul className="oai__warnings" aria-label="Avvisi sulla specifica">
                  {parsedSpec.warnings.map(w => <li key={w}>{w}</li>)}
                </ul>
              )}

              {/* Auth */}
              <div className="oai__auth-box">
                <div className="oai__section-title">🔐 Autenticazione</div>
                {proposedFrom && authConfig.type === parsedSpec.auth.type && <div className="oai__hint">Proposta dallo schema di sicurezza «{proposedFrom}» della specifica.</div>}
                <div className="oai__auth-opts">
                  {IMPORT_AUTH_OPTIONS.map(opt=>(
                    <label key={opt.type} className={`oai__auth-opt${authConfig.type===opt.type?' oai__auth-opt--on':''}`}>
                      <input type="radio" name="oai-auth" checked={authConfig.type===opt.type} onChange={()=>chooseAuth(opt)}/>
                      <span>{opt.icon}</span> {opt.label}
                    </label>
                  ))}
                </div>
                {authConfig.type==='api_key' && (
                  <div className="oai__auth-fields">
                    <input className="oai__input" value={authConfig.key_name||''} onChange={e=>setAuth({key_name:e.target.value})} placeholder="Nome chiave" aria-label="Nome chiave"/>
                    <input type="password" className="oai__input" value={authConfig.key_value||''} onChange={e=>setAuth({key_value:e.target.value})} placeholder="API Key" aria-label="API Key"/>
                    <div className="oai__auth-opts" role="radiogroup" aria-label="Posizione della chiave">
                      {[['header','Header'],['query','Query param']].map(([loc,label])=>(
                        <label key={loc} className={`oai__auth-opt${(authConfig.location||'header')===loc?' oai__auth-opt--on':''}`}>
                          <input type="radio" name="oai-key-location" checked={(authConfig.location||'header')===loc} onChange={()=>setAuth({location:loc})}/> {label}
                        </label>
                      ))}
                    </div>
                  </div>
                )}
                {authConfig.type==='bearer' && (
                  <div className="oai__auth-fields">
                    <input type="password" className="oai__input" value={authConfig.token||''} onChange={e=>setAuth({token:e.target.value})} placeholder="Bearer token" aria-label="Bearer token"/>
                  </div>
                )}
                {authConfig.type==='basic_auth' && (
                  <div className="oai__auth-fields">
                    <input className="oai__input" value={authConfig.username||''} onChange={e=>setAuth({username:e.target.value})} placeholder="Username" aria-label="Username"/>
                    <input type="password" className="oai__input" value={authConfig.password||''} onChange={e=>setAuth({password:e.target.value})} placeholder="Password" aria-label="Password"/>
                  </div>
                )}
                {authConfig.type==='oauth2_password' && (
                  <div className="oai__auth-fields">
                    <input className="oai__input oai__input--mono" value={authConfig.token_url||''} onChange={e=>setAuth({token_url:e.target.value})} placeholder="Token URL" aria-label="Token URL"/>
                    <input className="oai__input" value={authConfig.username||''} onChange={e=>setAuth({username:e.target.value})} placeholder="Username" aria-label="Username"/>
                    <input type="password" className="oai__input" value={authConfig.password||''} onChange={e=>setAuth({password:e.target.value})} placeholder="Password" aria-label="Password"/>
                  </div>
                )}
                {authConfig.type==='oauth2_client_credentials' && (
                  <div className="oai__auth-fields">
                    <input className="oai__input oai__input--mono" value={authConfig.token_url||''} onChange={e=>setAuth({token_url:e.target.value})} placeholder="Token URL" aria-label="Token URL"/>
                    <input className="oai__input" value={authConfig.client_id||''} onChange={e=>setAuth({client_id:e.target.value})} placeholder="Client ID" aria-label="Client ID"/>
                    <input type="password" className="oai__input" value={authConfig.client_secret||''} onChange={e=>setAuth({client_secret:e.target.value})} placeholder="Client Secret" aria-label="Client Secret"/>
                    <input className="oai__input" value={authConfig.scope||''} onChange={e=>setAuth({scope:e.target.value})} placeholder="Scope (opzionale)" aria-label="Scope"/>
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
                        <input type="checkbox" checked={isSel} aria-label={`Seleziona ${ep.method} ${ep.path}`} onChange={()=>toggleEndpoint(ep.id)} onClick={e=>e.stopPropagation()}/>
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
                              <label key={f.path} className="oai__field-item" title={f.path}>
                                <input type="checkbox" checked={fieldSet.has(f.path)} onChange={()=>toggleField(ep.id,f.path)}/>
                                <span className="oai__field-name">{f.column}</span>
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

              <button type="button" className="oai__back-btn" onClick={()=>{ setParsedSpec(null); setSelectedEndpoints([]); setSelectedFields({}); setManualBaseUrl(''); setAuthConfig(NO_AUTH); }}>
                ← Torna indietro
              </button>
            </div>
          )}
        </div>
    </ModalWrapper>
  );
}

export default OpenAPIImportModal;
