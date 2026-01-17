import React, { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import '../styles/ApiConnectionManager.css';

// API client functions
const API_BASE = 'http://localhost:8000/api';

const listApiConnections = async () => {
  const res = await fetch(`${API_BASE}/api-connections`);
  return res.json();
};

const createApiConnection = async (data) => {
  const res = await fetch(`${API_BASE}/api-connections`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data)
  });
  return res.json();
};

const updateApiConnection = async (id, data) => {
  const res = await fetch(`${API_BASE}/api-connections/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data)
  });
  return res.json();
};

const deleteApiConnection = async (id) => {
  const res = await fetch(`${API_BASE}/api-connections/${id}`, { method: 'DELETE' });
  return res.json();
};

const testApiConnection = async (data) => {
  const res = await fetch(`${API_BASE}/api-connections/test`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data)
  });
  return res.json();
};

function ApiConnectionManager() {
  const navigate = useNavigate();
  const [apiConnections, setApiConnections] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showWizard, setShowWizard] = useState(false);
  const [wizardStep, setWizardStep] = useState(0);
  const [editingApi, setEditingApi] = useState(null);
  
  const [formData, setFormData] = useState({
    name: '',
    description: '',
    api_type: 'enricher',
    url: '',
    method: 'GET',
    auth: { type: 'none' },
    parameters: [],
    static_parameters: {},
    output_mapping: []
  });
  
  const [testParams, setTestParams] = useState({});
  const [testResult, setTestResult] = useState(null);
  const [testing, setTesting] = useState(false);

  useEffect(() => {
    loadApiConnections();
  }, []);

  const loadApiConnections = async () => {
    try {
      setLoading(true);
      const data = await listApiConnections();
      setApiConnections(data.api_connections || []);
    } catch (error) {
      console.error('Errore caricamento API:', error);
    } finally {
      setLoading(false);
    }
  };

  const openWizard = (api = null) => {
    if (api) {
      setEditingApi(api);
      setFormData({
        name: api.name || '',
        description: api.description || '',
        api_type: api.api_type || 'enricher',
        url: api.endpoint?.url || '',
        method: api.endpoint?.method || 'GET',
        auth: api.auth || { type: 'none' },
        parameters: api.parameters || [],
        static_parameters: api.static_parameters || {},
        output_mapping: api.output_mapping || []
      });
      if (api.parameters?.length > 0 || Object.keys(api.static_parameters || {}).length > 0) {
        setWizardStep(4);
      }
    } else {
      setEditingApi(null);
      setFormData({
        name: '', description: '', api_type: 'enricher', url: '', method: 'GET',
        auth: { type: 'none' }, parameters: [], static_parameters: {}, output_mapping: []
      });
    }
    setWizardStep(0);
    setTestResult(null);
    setTestParams({});
    setShowWizard(true);
  };

  const closeWizard = () => {
    setShowWizard(false);
    setEditingApi(null);
    setWizardStep(1);
    setTestResult(null);
  };

  const handleDelete = async (apiId, apiName) => {
    if (!confirm(`Eliminare l'API "${apiName}"?`)) return;
    try {
      await deleteApiConnection(apiId);
      loadApiConnections();
    } catch (error) {
      alert('Errore eliminazione');
    }
  };

  const handleSave = async () => {
    try {
      const data = {
        name: formData.name, description: formData.description, api_type: formData.api_type,
        url: formData.url, method: formData.method, auth: formData.auth,
        parameters: formData.parameters, static_parameters: formData.static_parameters,
        output_mapping: formData.output_mapping
      };
      if (editingApi) {
        await updateApiConnection(editingApi.id, data);
      } else {
        await createApiConnection(data);
      }
      closeWizard();
      loadApiConnections();
    } catch (error) {
      alert('Errore salvataggio');
    }
  };

  const handleTest = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      const result = await testApiConnection({
        url: formData.url, method: formData.method, auth: formData.auth, test_params: testParams
      });
      setTestResult(result);
      if (result.success && result.available_fields) {
        const autoMapping = result.available_fields
          .filter(f => f.type === 'geojson' || f.name === 'id')
          .map(f => ({ path: f.path, column: f.name === 'geometry' ? 'geometry' : f.name, type: f.type, selected: true }));
        setFormData(prev => ({ ...prev, output_mapping: autoMapping }));
      }
    } catch (error) {
      setTestResult({ success: false, error: error.message });
    } finally {
      setTesting(false);
    }
  };

  const addParameter = () => {
    setFormData(prev => ({
      ...prev,
      parameters: [...prev.parameters, { name: '', type: 'string', required: true }]
    }));
  };

  const updateParameter = (index, field, value) => {
    setFormData(prev => ({
      ...prev,
      parameters: prev.parameters.map((p, i) => i === index ? { ...p, [field]: value } : p)
    }));
  };

  const removeParameter = (index) => {
    setFormData(prev => ({
      ...prev,
      parameters: prev.parameters.filter((_, i) => i !== index)
    }));
  };

  const toggleOutputField = (field) => {
    setFormData(prev => {
      const existing = prev.output_mapping.find(m => m.path === field.path);
      if (existing) {
        return { ...prev, output_mapping: prev.output_mapping.filter(m => m.path !== field.path) };
      } else {
        return { ...prev, output_mapping: [...prev.output_mapping, { path: field.path, column: field.name, type: field.type, selected: true }] };
      }
    });
  };

  const updateOutputColumn = (path, newColumn) => {
    setFormData(prev => ({
      ...prev,
      output_mapping: prev.output_mapping.map(m => m.path === path ? { ...m, column: newColumn } : m)
    }));
  };

  if (loading) {
    return <div className="api-manager__loading">Caricamento...</div>;
  }

  return (
    <div className="api-manager">
      {/* Header */}
      <header className="api-manager__header">
        <div className="api-manager__header-content">
          <div className="api-manager__header-left">
            <button onClick={() => navigate(-1)} className="api-manager__back-btn">← Indietro</button>
            <div>
              <h1 className="api-manager__title">🔗 API Esterne</h1>
              <p className="api-manager__subtitle">Configura le API REST per arricchire i tuoi dati</p>
            </div>
          </div>
          <button onClick={() => openWizard()} className="api-manager__new-btn">+ Nuova API</button>
        </div>
      </header>

      {/* Content */}
      <main className="api-manager__main">
        {apiConnections.length === 0 ? (
          <div className="api-manager__empty">
            <div className="api-manager__empty-icon">🔗</div>
            <h3 className="api-manager__empty-title">Nessuna API configurata</h3>
            <p className="api-manager__empty-text">Configura la tua prima API per arricchire i dati con informazioni esterne</p>
            <button onClick={() => openWizard()} className="api-manager__empty-btn">+ Configura prima API</button>
          </div>
        ) : (
          <div className="api-manager__grid">
            {apiConnections.map(api => (
              <div key={api.id} className="api-card">
                <div className="api-card__header">
                  <span className="api-card__icon">{api.api_type === 'generator' ? '📥' : '🔄'}</span>
                  <div>
                    <h3 className="api-card__name">{api.name}</h3>
                    <span className={`api-card__type-badge ${api.api_type === 'generator' ? 'api-card__type-badge--generator' : ''}`}>
                      {api.api_type === 'generator' ? 'Generatore' : 'Arricchitore'}
                    </span>
                  </div>
                </div>
                <div className="api-card__body">
                  <p className="api-card__description">{api.description || 'Nessuna descrizione'}</p>
                  <div className="api-card__details">
                    <div className="api-card__detail-row">
                      <span className="api-card__detail-label">URL:</span>
                      <span className="api-card__detail-value">{api.endpoint?.url || 'N/A'}</span>
                    </div>
                    <div className="api-card__detail-row">
                      <span className="api-card__detail-label">Metodo:</span>
                      <span className="api-card__detail-value">{api.endpoint?.method || 'GET'}</span>
                    </div>
                  </div>
                  <div className="api-card__outputs">
                    <div className="api-card__outputs-label">Output: {api.output_mapping?.length || 0} colonne</div>
                  </div>
                </div>
                <div className="api-card__footer">
                  <button onClick={() => openWizard(api)} className="api-card__btn api-card__btn--edit">✏️ Modifica</button>
                  <button onClick={() => handleDelete(api.id, api.name)} className="api-card__btn api-card__btn--delete">🗑️ Elimina</button>
                </div>
              </div>
            ))}
          </div>
        )}
      </main>

      {/* Wizard Modal */}
      {showWizard && (
        <div className="api-manager__wizard-overlay" onClick={closeWizard}>
          <div className="api-manager__wizard" onClick={e => e.stopPropagation()}>
            <div className="api-manager__wizard-header">
              <h3 className="api-manager__wizard-title">{editingApi ? 'Modifica API' : 'Nuova API'} - Step {wizardStep + 1}/5</h3>
              <button onClick={closeWizard} className="api-manager__wizard-close">×</button>
            </div>

            <div className="api-manager__wizard-progress">
              {['Tipo', 'Info', 'Parametri', 'Auth', 'Test'].map((label, idx) => (
                <div key={idx} className={`api-manager__step ${idx === wizardStep ? 'api-manager__step--active' : ''} ${idx < wizardStep ? 'api-manager__step--completed' : ''}`}>
                  <span className="api-manager__step-number">{idx + 1}</span>
                  {label}
                </div>
              ))}
            </div>

            <div className="api-manager__wizard-body">
              {/* Step 0: Tipo */}
              {wizardStep === 0 && (
                <div>
                  <p className="api-manager__section-subtitle">Seleziona il tipo di API che vuoi configurare.</p>
                  <div className="api-manager__type-cards">
                    <div onClick={() => setFormData({ ...formData, api_type: 'enricher' })} className={`api-manager__type-card ${formData.api_type === 'enricher' ? 'api-manager__type-card--selected' : ''}`}>
                      <div className="api-manager__type-card-icon">🔄</div>
                      <div className="api-manager__type-card-title">ARRICCHITORE</div>
                      <p className="api-manager__type-card-desc">Chiama l'API <strong>per ogni riga</strong> del dataset. I parametri vengono mappati alle colonne.</p>
                    </div>
                    <div onClick={() => setFormData({ ...formData, api_type: 'generator' })} className={`api-manager__type-card ${formData.api_type === 'generator' ? 'api-manager__type-card--selected' : ''}`}>
                      <div className="api-manager__type-card-icon">📥</div>
                      <div className="api-manager__type-card-title">GENERATORE</div>
                      <p className="api-manager__type-card-desc">Chiama l'API <strong>una sola volta</strong> e genera un nuovo dataset dalla risposta.</p>
                    </div>
                  </div>
                </div>
              )}

              {/* Step 1: Info */}
              {wizardStep === 1 && (
                <div>
                  <div className="api-manager__form-group">
                    <label className="api-manager__form-label">Nome API *</label>
                    <input type="text" value={formData.name} onChange={e => setFormData({ ...formData, name: e.target.value })} placeholder="Es. Catasto Fabbricati" className="api-manager__form-input" />
                  </div>
                  <div className="api-manager__form-group">
                    <label className="api-manager__form-label">Descrizione</label>
                    <textarea value={formData.description} onChange={e => setFormData({ ...formData, description: e.target.value })} placeholder="Descrizione opzionale..." rows={2} className="api-manager__form-textarea" />
                  </div>
                  <div className="api-manager__form-group">
                    <label className="api-manager__form-label">URL Endpoint *</label>
                    <input type="text" value={formData.url} onChange={e => setFormData({ ...formData, url: e.target.value })} placeholder="http://localhost:8001/api/endpoint" className="api-manager__form-input" style={{ fontFamily: 'monospace' }} />
                  </div>
                  <div className="api-manager__form-group">
                    <label className="api-manager__form-label">Metodo HTTP</label>
                    <div className="api-manager__form-row api-manager__form-row--2">
                      {['GET', 'POST'].map(method => (
                        <label key={method} className={`api-manager__auth-type ${formData.method === method ? 'api-manager__auth-type--selected' : ''}`}>
                          <input type="radio" name="method" value={method} checked={formData.method === method} onChange={e => setFormData({ ...formData, method: e.target.value })} style={{ marginRight: '8px' }} />
                          {method}
                        </label>
                      ))}
                    </div>
                  </div>
                </div>
              )}

              {/* Step 2: Parametri */}
              {wizardStep === 2 && (
                <div>
                  <p className="api-manager__section-subtitle">
                    {formData.api_type === 'enricher' 
                      ? 'Definisci i parametri che l\'API richiede. Questi verranno mappati alle colonne del dataset.'
                      : 'Definisci i parametri con i loro valori fissi. L\'API verrà chiamata una sola volta.'}
                  </p>
                  <div className="api-manager__params-list">
                    {formData.parameters.map((param, idx) => (
                      <div key={idx} className="api-manager__param-row">
                        <input type="text" value={param.name} onChange={e => updateParameter(idx, 'name', e.target.value)} placeholder="Nome parametro" className="api-manager__param-input" />
                        <select value={param.type} onChange={e => updateParameter(idx, 'type', e.target.value)} className="api-manager__param-select">
                          <option value="string">string</option>
                          <option value="number">number</option>
                        </select>
                        {formData.api_type === 'enricher' ? (
                          <label style={{ display: 'flex', alignItems: 'center', gap: '4px', fontSize: '12px' }}>
                            <input type="checkbox" checked={param.required} onChange={e => updateParameter(idx, 'required', e.target.checked)} className="api-manager__param-checkbox" /> Obblig.
                          </label>
                        ) : (
                          <input type="text" value={formData.static_parameters[param.name] || ''} onChange={e => setFormData({ ...formData, static_parameters: { ...formData.static_parameters, [param.name]: e.target.value } })} placeholder="Valore fisso" className="api-manager__param-input" />
                        )}
                        <button onClick={() => removeParameter(idx)} className="api-manager__param-delete">🗑️</button>
                      </div>
                    ))}
                    <button onClick={addParameter} className="api-manager__add-param-btn">+ Aggiungi parametro</button>
                  </div>
                  {formData.api_type === 'generator' && (
                    <div className="api-manager__info-box api-manager__info-box--info" style={{ marginTop: '20px' }}>
                      ℹ️ L'API verrà chiamata una sola volta con questi parametri fissi e genererà un nuovo dataset dalla risposta.
                    </div>
                  )}
                </div>
              )}

              {/* Step 3: Auth */}
              {wizardStep === 3 && (
                <div>
                  <p className="api-manager__section-subtitle">Configura l'autenticazione se richiesta dall'API.</p>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '10px', marginBottom: '20px' }}>
                    {[{ value: 'none', label: 'Nessuna autenticazione' }, { value: 'api_key', label: 'API Key' }, { value: 'oauth2_password', label: 'OAuth2 Password Flow' }].map(opt => (
                      <label key={opt.value} className={`api-manager__auth-type ${formData.auth.type === opt.value ? 'api-manager__auth-type--selected' : ''}`} style={{ padding: '12px 15px', justifyContent: 'flex-start' }}>
                        <input type="radio" name="authType" value={opt.value} checked={formData.auth.type === opt.value} onChange={e => setFormData({ ...formData, auth: { type: e.target.value } })} style={{ marginRight: '10px' }} />
                        {opt.label}
                      </label>
                    ))}
                  </div>
                  {formData.auth.type === 'api_key' && (
                    <div className="api-manager__test-section">
                      <div className="api-manager__form-row api-manager__form-row--2">
                        <div className="api-manager__form-group">
                          <label className="api-manager__form-label">Nome chiave *</label>
                          <input type="text" value={formData.auth.key_name || ''} onChange={e => setFormData({ ...formData, auth: { ...formData.auth, key_name: e.target.value } })} placeholder="X-API-Key" className="api-manager__form-input" />
                        </div>
                        <div className="api-manager__form-group">
                          <label className="api-manager__form-label">Valore *</label>
                          <input type="password" value={formData.auth.key_value || ''} onChange={e => setFormData({ ...formData, auth: { ...formData.auth, key_value: e.target.value } })} placeholder="La tua API key" className="api-manager__form-input" />
                        </div>
                      </div>
                      <div className="api-manager__form-group">
                        <label className="api-manager__form-label">Posizione</label>
                        <div className="api-manager__form-row api-manager__form-row--2">
                          {['header', 'query'].map(loc => (
                            <label key={loc} className={`api-manager__auth-type ${(formData.auth.location || 'header') === loc ? 'api-manager__auth-type--selected' : ''}`}>
                              <input type="radio" name="keyLocation" value={loc} checked={(formData.auth.location || 'header') === loc} onChange={e => setFormData({ ...formData, auth: { ...formData.auth, location: e.target.value } })} style={{ marginRight: '6px' }} />
                              {loc === 'header' ? 'Header' : 'Query param'}
                            </label>
                          ))}
                        </div>
                      </div>
                    </div>
                  )}
                  {formData.auth.type === 'oauth2_password' && (
                    <div className="api-manager__test-section">
                      <div className="api-manager__form-group">
                        <label className="api-manager__form-label">Token URL *</label>
                        <input type="text" value={formData.auth.token_url || ''} onChange={e => setFormData({ ...formData, auth: { ...formData.auth, token_url: e.target.value } })} placeholder="http://api.example.com/token" className="api-manager__form-input" style={{ fontFamily: 'monospace' }} />
                      </div>
                      <div className="api-manager__form-row api-manager__form-row--2">
                        <div className="api-manager__form-group">
                          <label className="api-manager__form-label">Username *</label>
                          <input type="text" value={formData.auth.username || ''} onChange={e => setFormData({ ...formData, auth: { ...formData.auth, username: e.target.value } })} placeholder="Username" className="api-manager__form-input" />
                        </div>
                        <div className="api-manager__form-group">
                          <label className="api-manager__form-label">Password *</label>
                          <input type="password" value={formData.auth.password || ''} onChange={e => setFormData({ ...formData, auth: { ...formData.auth, password: e.target.value } })} placeholder="••••••••" className="api-manager__form-input" />
                        </div>
                      </div>
                      <div className="api-manager__info-box api-manager__info-box--info">ℹ️ Il sistema farà login automatico prima di ogni chiamata API</div>
                    </div>
                  )}
                </div>
              )}

              {/* Step 4: Test */}
              {wizardStep === 4 && (
                <div>
                  <p className="api-manager__section-subtitle">Testa l'API con dati reali e seleziona le colonne da importare.</p>
                  <div className="api-manager__test-section">
                    <h4 className="api-manager__test-title">📝 Dati di test:</h4>
                    {formData.parameters.map((param, idx) => (
                      <div key={idx} className="api-manager__test-param-row">
                        <label className="api-manager__test-param-label">{param.name} {param.required && <span style={{ color: '#f44336' }}>*</span>}</label>
                        <input type={param.type === 'number' ? 'number' : 'text'} value={testParams[param.name] || ''} onChange={e => setTestParams({ ...testParams, [param.name]: e.target.value })} placeholder={`Inserisci ${param.name}`} className="api-manager__test-param-input" />
                      </div>
                    ))}
                    <button onClick={handleTest} disabled={testing} className="api-manager__test-btn">{testing ? '⏳ Test in corso...' : '🚀 Esegui Test'}</button>
                  </div>
                  {testResult && (
                    <div className={`api-manager__test-result ${testResult.success ? 'api-manager__test-result--success' : 'api-manager__test-result--error'}`}>
                      <h4 className={`api-manager__test-result-title ${testResult.success ? 'api-manager__test-result-title--success' : 'api-manager__test-result-title--error'}`}>
                        {testResult.success ? '✅ Test riuscito!' : '❌ Test fallito'}
                      </h4>
                      {testResult.success ? (
                        <>
                          <p className="api-manager__test-result-message">Seleziona i campi da importare come colonne:</p>
                          <div className="api-manager__fields-grid">
                            {testResult.available_fields?.map((field, idx) => {
                              const isSelected = formData.output_mapping.find(m => m.path === field.path);
                              return (
                                <div key={idx} onClick={() => toggleOutputField(field)} className={`api-manager__field-item ${isSelected ? 'api-manager__field-item--selected' : ''}`}>
                                  <input type="checkbox" checked={!!isSelected} onChange={() => {}} className="api-manager__field-checkbox" />
                                  <span className="api-manager__field-name">{field.name}</span>
                                  <span className={`api-manager__field-type ${field.type === 'geojson' ? 'api-manager__field-type--geojson' : ''}`}>{field.type}</span>
                                </div>
                              );
                            })}
                          </div>
                          {formData.output_mapping.length > 0 && (
                            <div className="api-manager__selected-fields">
                              <h5 className="api-manager__selected-title">Campi selezionati:</h5>
                              <div className="api-manager__selected-list">
                                {formData.output_mapping.map((m, idx) => (
                                  <div key={idx} className="api-manager__selected-item">
                                    <span className="api-manager__selected-path">{m.path}</span>
                                    <span className="api-manager__selected-arrow">→</span>
                                    <input type="text" value={m.column} onChange={e => updateOutputColumn(m.path, e.target.value)} className="api-manager__selected-input" />
                                  </div>
                                ))}
                              </div>
                            </div>
                          )}
                        </>
                      ) : (
                        <div className="api-manager__test-result-message">{testResult.error}</div>
                      )}
                    </div>
                  )}
                </div>
              )}
            </div>

            <div className="api-manager__wizard-footer">
              <button onClick={wizardStep === 0 ? closeWizard : () => setWizardStep(s => s - 1)} className="api-manager__wizard-btn api-manager__wizard-btn--secondary">
                {wizardStep === 0 ? 'Annulla' : '← Indietro'}
              </button>
              {wizardStep < 4 ? (
                <button onClick={() => setWizardStep(s => s + 1)} disabled={(wizardStep === 1 && (!formData.name || !formData.url)) || (wizardStep === 2 && formData.parameters.length === 0)} className="api-manager__wizard-btn api-manager__wizard-btn--primary" style={{ opacity: ((wizardStep === 1 && (!formData.name || !formData.url)) || (wizardStep === 2 && formData.parameters.length === 0)) ? 0.5 : 1 }}>
                  Avanti →
                </button>
              ) : (
                <button onClick={handleSave} disabled={formData.output_mapping.length === 0} className="api-manager__wizard-btn api-manager__wizard-btn--success" style={{ opacity: formData.output_mapping.length === 0 ? 0.5 : 1 }}>
                  💾 Salva API
                </button>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default ApiConnectionManager;