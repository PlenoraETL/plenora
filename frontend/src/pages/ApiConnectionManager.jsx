import React, { useState, useEffect, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import TemplateLibraryModal from '../components/arricchisci/TemplateLibraryModal';
import OpenAPIImportModal from '../components/arricchisci/OpenAPIImportModal';
import ExecutableConfigModal from '../components/arricchisci/ExecutableConfigModal';
import DataSourceConfigModal from '../components/arricchisci/DataSourceConfigModal';
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

// Categorie per i filtri
const CATEGORIES = [
  { id: 'all', label: 'Tutte', icon: '🔗' },
  { id: 'geocoding', label: 'Geocoding', icon: '🌍' },
  { id: 'routing', label: 'Routing', icon: '🚗' },
  { id: 'ml_model', label: 'Modelli ML', icon: '🧠' },
  { id: 'catasto', label: 'Catasto', icon: '🏠' },
  { id: 'datasource', label: 'Data Source', icon: '📁' },
  { id: 'custom', label: 'Custom', icon: '⚙️' }
];

// Determina icona API in base a nome/categoria
const getApiIcon = (api) => {
  // Se ha una categoria, usa quella
  if (api.category) {
    const cat = CATEGORIES.find(c => c.id === api.category);
    if (cat) return cat.icon;
  }
  
  // Altrimenti indovina dal nome
  const name = (api.name || '').toLowerCase();
  const url = (api.endpoint?.url || api.url || '').toLowerCase();
  
  if (name.includes('nominatim') || name.includes('geocod') || url.includes('nominatim')) return '🌍';
  if (name.includes('osrm') || name.includes('rout') || name.includes('direction')) return '🚗';
  if (name.includes('weather') || name.includes('meteo')) return '🌤️';
  if (name.includes('catasto') || name.includes('omi') || name.includes('immobil')) return '🏠';
  if (name.includes('model') || name.includes('ml') || name.includes('predict')) return '🧠';
  if (name.includes('sharepoint') || name.includes('ftp') || name.includes('sftp') || name.includes('s3') || name.includes('azure')) return '📁';
  
  return '⚙️';
};

// Helper per status API
const getApiStatus = (api) => {
  const quota = api.usage?.quota_used || 0;
  const quotaLimit = api.usage?.quota_limit || 0;
  const quotaPercent = quotaLimit > 0 ? Math.round((quota / quotaLimit) * 100) : 0;
  const errorRate = api.usage?.calls_total > 0 
    ? ((api.usage?.calls_error || 0) / api.usage.calls_total * 100).toFixed(1)
    : 0;
  
  let status = 'active';
  let statusLabel = '✅ Attiva';
  
  if (quotaPercent >= 90) {
    status = 'critical';
    statusLabel = '🔴 Quota esaurita';
  } else if (quotaPercent >= 80) {
    status = 'warning';
    statusLabel = `⚠️ Quota ${quotaPercent}%`;
  } else if (errorRate > 5) {
    status = 'warning';
    statusLabel = `⚠️ Errori ${errorRate}%`;
  }
  
  return { status, statusLabel, quotaPercent, errorRate };
};

// Formatta tempo relativo
const formatTimeAgo = (date) => {
  if (!date) return 'Mai';
  const seconds = Math.floor((new Date() - new Date(date)) / 1000);
  if (seconds < 60) return 'Ora';
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m fa`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h fa`;
  return `${Math.floor(seconds / 86400)}g fa`;
};

function ApiConnectionManager() {
  const navigate = useNavigate();
  
  // State principale
  const [apiConnections, setApiConnections] = useState([]);
  const [loading, setLoading] = useState(true);
  
  // Filtri e ricerca
  const [searchQuery, setSearchQuery] = useState('');
  const [activeCategory, setActiveCategory] = useState('all');
  
  // Modali
  const [showWizard, setShowWizard] = useState(false);
  const [showTemplateLibrary, setShowTemplateLibrary] = useState(false);
  const [showOpenAPIImport, setShowOpenAPIImport] = useState(false);
  const [showExecutableConfig, setShowExecutableConfig] = useState(false);
  const [showDataSourceConfig, setShowDataSourceConfig] = useState(false);
  const [showNewApiDropdown, setShowNewApiDropdown] = useState(false);
  
  // Wizard state
  const [wizardStep, setWizardStep] = useState(0);
  const [editingApi, setEditingApi] = useState(null);
  
  const [formData, setFormData] = useState({
    name: '',
    description: '',
    api_type: 'enricher',
    category: 'custom',
    url: '',
    method: 'GET',
    auth: { type: 'none' },
    custom_headers: {},
    parameters: [],
    static_parameters: {},
    output_mapping: [],
    options: {
      rate_limit: 5,
      timeout: 30,
      retry_on_error: true,
      retry_max: 3
    }
  });
  
  const [customHeadersList, setCustomHeadersList] = useState([]);
  const [testParams, setTestParams] = useState({});
  const [testResult, setTestResult] = useState(null);
  const [testing, setTesting] = useState(false);

  useEffect(() => {
    loadApiConnections();
  }, []);

  // Click outside per chiudere dropdown
  useEffect(() => {
    const handleClickOutside = (e) => {
      if (showNewApiDropdown && !e.target.closest('.api-manager__new-api-wrapper')) {
        setShowNewApiDropdown(false);
      }
    };
    document.addEventListener('click', handleClickOutside);
    return () => document.removeEventListener('click', handleClickOutside);
  }, [showNewApiDropdown]);

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

  // Filtra API in base a ricerca e categoria
  const filteredApis = useMemo(() => {
    return apiConnections.filter(api => {
      const matchesSearch = !searchQuery || 
        api.name?.toLowerCase().includes(searchQuery.toLowerCase()) ||
        api.description?.toLowerCase().includes(searchQuery.toLowerCase());
      const matchesCategory = activeCategory === 'all' || api.category === activeCategory;
      return matchesSearch && matchesCategory;
    });
  }, [apiConnections, searchQuery, activeCategory]);

  // Calcola statistiche globali
  const globalStats = useMemo(() => {
    const total = apiConnections.length;
    const totalCalls = apiConnections.reduce((sum, api) => sum + (api.usage?.calls_total || 0), 0);
    const totalErrors = apiConnections.reduce((sum, api) => sum + (api.usage?.calls_error || 0), 0);
    const warningApis = apiConnections.filter(api => {
      const { status } = getApiStatus(api);
      return status === 'warning' || status === 'critical';
    }).length;
    
    return { total, totalCalls, totalErrors, warningApis };
  }, [apiConnections]);

  // Apre wizard per nuova API o modifica
  const openWizard = (api = null) => {
    if (api) {
      setEditingApi(api);
      const existingHeaders = api.custom_headers || {};
      setFormData({
        name: api.name || '',
        description: api.description || '',
        api_type: api.api_type || 'enricher',
        category: api.category || 'custom',
        url: api.endpoint?.url || '',
        method: api.endpoint?.method || 'GET',
        auth: api.auth || { type: 'none' },
        custom_headers: existingHeaders,
        parameters: api.parameters || [],
        static_parameters: api.static_parameters || {},
        output_mapping: api.output_mapping || [],
        options: api.options || { rate_limit: 5, timeout: 30, retry_on_error: true, retry_max: 3 }
      });
      setCustomHeadersList(
        Object.entries(existingHeaders).map(([key, value]) => ({ key, value }))
      );
      if (api.parameters?.length > 0 || Object.keys(api.static_parameters || {}).length > 0) {
        setWizardStep(4);
      }
    } else {
      setEditingApi(null);
      setFormData({
        name: '', description: '', api_type: 'enricher', category: 'custom', url: '', method: 'GET',
        auth: { type: 'none' }, custom_headers: {}, parameters: [], static_parameters: {}, output_mapping: [],
        options: { rate_limit: 5, timeout: 30, retry_on_error: true, retry_max: 3 }
      });
      setCustomHeadersList([]);
    }
    setWizardStep(0);
    setTestResult(null);
    setTestParams({});
    setShowWizard(true);
    setShowNewApiDropdown(false);
  };

  // Applica template dalla libreria
  const applyTemplate = (template) => {
    setFormData({
      name: template.display?.name || template.name || '',
      description: template.display?.short_description || '',
      api_type: template.config?.api_type || 'enricher',
      category: template._meta?.category || 'custom',
      url: template.config?.endpoint?.url || '',
      method: template.config?.endpoint?.method || 'GET',
      auth: template.config?.auth || { type: 'none' },
      custom_headers: {},
      parameters: (template.parameters || []).map(p => ({
        name: p.name,
        type: p.type || 'string',
        required: p.required ?? true,
        mode: p.hidden ? 'fixed' : 'mapped',
        label: p.label,
        description: p.description
      })),
      static_parameters: (template.parameters || [])
        .filter(p => p.default !== null && p.default !== undefined)
        .reduce((acc, p) => ({ ...acc, [p.name]: p.default }), {}),
      output_mapping: (template.output_mapping || []).map(o => ({
        path: o.path,
        column: o.column,
        type: o.type
      })),
      options: {
        rate_limit: template.config?.options?.rate_limit || 5,
        timeout: template.config?.options?.timeout || 30,
        retry_on_error: template.config?.options?.retry_on_error ?? true,
        retry_max: template.config?.options?.retry_max || 3
      }
    });
    setCustomHeadersList([]);
    setShowTemplateLibrary(false);
    setWizardStep(1); // Salta allo step Info per revisione
    setShowWizard(true);
  };

  // Applica schema OpenAPI importato
  const applyOpenAPIImport = (importedApis) => {
    // Importa direttamente le API
    importedApis.forEach(async (apiData) => {
      try {
        await createApiConnection(apiData);
      } catch (e) {
        console.error('Errore import API:', e);
      }
    });
    setShowOpenAPIImport(false);
    setTimeout(loadApiConnections, 500);
  };

  const closeWizard = () => {
    setShowWizard(false);
    setEditingApi(null);
    setWizardStep(0);
    setTestResult(null);
    setCustomHeadersList([]);
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

  const getCustomHeadersObject = () => {
    const headers = {};
    customHeadersList.forEach(h => {
      if (h.key && h.key.trim()) {
        headers[h.key.trim()] = h.value || '';
      }
    });
    return headers;
  };

  const handleSave = async () => {
    try {
      const data = {
        name: formData.name, 
        description: formData.description, 
        api_type: formData.api_type,
        category: formData.category,
        url: formData.url, 
        method: formData.method, 
        auth: formData.auth,
        custom_headers: getCustomHeadersObject(),
        parameters: formData.parameters, 
        static_parameters: formData.static_parameters,
        output_mapping: formData.output_mapping,
        options: formData.options
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
      const allParams = { ...formData.static_parameters, ...testParams };
      
      const result = await testApiConnection({
        url: formData.url, 
        method: formData.method, 
        auth: formData.auth,
        custom_headers: getCustomHeadersObject(),
        test_params: allParams
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

  const handleQuickTest = async (api) => {
    try {
      const result = await testApiConnection({
        url: api.endpoint?.url,
        method: api.endpoint?.method || 'GET',
        auth: api.auth,
        custom_headers: api.custom_headers || {},
        test_params: api.test_data || {}
      });
      alert(result.success ? '✅ API funzionante!' : `❌ Errore: ${result.error}`);
    } catch (error) {
      alert(`❌ Errore: ${error.message}`);
    }
  };

  const addParameter = () => {
    setFormData(prev => ({
      ...prev,
      parameters: [...prev.parameters, { name: '', type: 'string', required: true, mode: 'mapped' }]
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

  const addCustomHeader = () => {
    setCustomHeadersList(prev => [...prev, { key: '', value: '' }]);
  };

  const updateCustomHeader = (index, field, value) => {
    setCustomHeadersList(prev => 
      prev.map((h, i) => i === index ? { ...h, [field]: value } : h)
    );
  };

  const removeCustomHeader = (index) => {
    setCustomHeadersList(prev => prev.filter((_, i) => i !== index));
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
              <h1 className="api-manager__title">🔗 Connettori Esterni</h1>
              <p className="api-manager__subtitle">API REST, Eseguibili e Data Sources per arricchire i tuoi dati</p>
            </div>
          </div>
          
          {/* Dropdown Nuovo Connettore */}
          <div className="api-manager__new-api-wrapper">
            <button 
              onClick={() => setShowNewApiDropdown(!showNewApiDropdown)} 
              className="api-manager__new-btn"
            >
              + Nuovo Connettore ▾
            </button>
            {showNewApiDropdown && (
              <div className="api-manager__new-api-dropdown">
                <button onClick={() => { setShowTemplateLibrary(true); setShowNewApiDropdown(false); }}>
                  <span className="dropdown-icon">📦</span>
                  <div>
                    <strong>Da Template</strong>
                    <small>Nominatim, OSRM, RETURN...</small>
                  </div>
                </button>
                <button onClick={() => { setShowOpenAPIImport(true); setShowNewApiDropdown(false); }}>
                  <span className="dropdown-icon">📄</span>
                  <div>
                    <strong>Importa OpenAPI</strong>
                    <small>Da URL o file JSON</small>
                  </div>
                </button>
                <button onClick={() => openWizard()}>
                  <span className="dropdown-icon">✏️</span>
                  <div>
                    <strong>Configurazione Manuale</strong>
                    <small>Wizard passo-passo</small>
                  </div>
                </button>
                <div className="api-manager__dropdown-divider"></div>
                <button onClick={() => { setShowExecutableConfig(true); setShowNewApiDropdown(false); }}>
                  <span className="dropdown-icon">🖥️</span>
                  <div>
                    <strong>Eseguibile Locale</strong>
                    <small>Script Python, exe, modelli ML</small>
                  </div>
                </button>
                <button onClick={() => { setShowDataSourceConfig(true); setShowNewApiDropdown(false); }}>
                  <span className="dropdown-icon">📁</span>
                  <div>
                    <strong>Data Source</strong>
                    <small>SharePoint, FTP, SFTP, S3...</small>
                  </div>
                </button>
              </div>
            )}
          </div>
        </div>
      </header>

      {/* Stats Bar */}
      <div className="api-manager__stats-bar">
        <div className="api-manager__stat">
          <span className="api-manager__stat-value">{globalStats.total}</span>
          <span className="api-manager__stat-label">Connettori</span>
        </div>
        <div className="api-manager__stat">
          <span className="api-manager__stat-value">{globalStats.totalCalls.toLocaleString()}</span>
          <span className="api-manager__stat-label">Chiamate totali</span>
        </div>
        <div className="api-manager__stat">
          <span className="api-manager__stat-value">{globalStats.totalErrors}</span>
          <span className="api-manager__stat-label">Errori</span>
        </div>
        {globalStats.warningApis > 0 && (
          <div className="api-manager__stat api-manager__stat--warning">
            <span className="api-manager__stat-value">⚠️ {globalStats.warningApis}</span>
            <span className="api-manager__stat-label">Richiede attenzione</span>
          </div>
        )}
      </div>

      {/* Toolbar: Search + Category Filters */}
      <div className="api-manager__toolbar">
        <div className="api-manager__search-wrapper">
          <span className="api-manager__search-icon">🔍</span>
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Cerca API..."
            className="api-manager__search-input"
          />
          {searchQuery && (
            <button 
              onClick={() => setSearchQuery('')}
              className="api-manager__search-clear"
            >×</button>
          )}
        </div>
        
        <div className="api-manager__category-filters">
          {CATEGORIES.map(cat => (
            <button
              key={cat.id}
              onClick={() => setActiveCategory(cat.id)}
              className={`api-manager__category-btn ${activeCategory === cat.id ? 'api-manager__category-btn--active' : ''}`}
              title={cat.label}
            >
              <span>{cat.icon}</span>
              <span className="api-manager__category-label">{cat.label}</span>
            </button>
          ))}
        </div>
      </div>

      {/* Content */}
      <main className="api-manager__main">
        {filteredApis.length === 0 ? (
          <div className="api-manager__empty">
            <div className="api-manager__empty-icon">🔗</div>
            <h3 className="api-manager__empty-title">
              {apiConnections.length === 0 
                ? 'Nessuna API configurata' 
                : 'Nessun risultato'}
            </h3>
            <p className="api-manager__empty-text">
              {apiConnections.length === 0 
                ? 'Configura la tua prima API per arricchire i dati con informazioni esterne'
                : 'Prova a modificare i filtri di ricerca'}
            </p>
            {apiConnections.length === 0 && (
              <div className="api-manager__empty-actions">
                <button onClick={() => setShowTemplateLibrary(true)} className="api-manager__empty-btn">
                  📦 Scegli da Template
                </button>
                <button onClick={() => openWizard()} className="api-manager__empty-btn api-manager__empty-btn--secondary">
                  ✏️ Configurazione Manuale
                </button>
              </div>
            )}
          </div>
        ) : (
          <div className="api-manager__grid">
            {filteredApis.map(api => {
              const { status, statusLabel, quotaPercent } = getApiStatus(api);
              const apiIcon = getApiIcon(api);
              
              return (
                <div key={api.id} className={`api-card api-card--${status}`}>
                  <div className="api-card__header">
                    <span className="api-card__icon">{apiIcon}</span>
                    <div className="api-card__header-info">
                      <h3 className="api-card__name">{api.name}</h3>
                      <div className="api-card__badges">
                        <span className={`api-card__type-badge ${api.api_type === 'generator' ? 'api-card__type-badge--generator' : ''}`}>
                          {api.api_type === 'generator' ? 'Generatore' : 'Arricchitore'}
                        </span>
                        <span className={`api-card__status-badge api-card__status-badge--${status}`}>
                          {statusLabel}
                        </span>
                      </div>
                    </div>
                  </div>
                  
                  <div className="api-card__body">
                    <p className="api-card__description">{api.description || 'Nessuna descrizione'}</p>
                    
                    <div className="api-card__stats">
                      <div className="api-card__stat-item">
                        <span className="api-card__stat-value">{(api.usage?.calls_total || 0).toLocaleString()}</span>
                        <span className="api-card__stat-label">chiamate</span>
                      </div>
                      {api.usage?.quota_limit > 0 && (
                        <div className="api-card__stat-item">
                          <span className="api-card__stat-value">{quotaPercent}%</span>
                          <span className="api-card__stat-label">quota</span>
                        </div>
                      )}
                      <div className="api-card__stat-item">
                        <span className="api-card__stat-value">{formatTimeAgo(api.usage?.last_call)}</span>
                        <span className="api-card__stat-label">ultimo</span>
                      </div>
                    </div>
                    
                    {api.usage?.quota_limit > 0 && (
                      <div className="api-card__quota-bar">
                        <div 
                          className={`api-card__quota-fill api-card__quota-fill--${status}`}
                          style={{ width: `${Math.min(quotaPercent, 100)}%` }}
                        />
                      </div>
                    )}
                  </div>
                  
                  <div className="api-card__footer">
                    <button onClick={() => handleQuickTest(api)} className="api-card__btn api-card__btn--test" title="Test rapido">
                      🧪 Test
                    </button>
                    <button onClick={() => openWizard(api)} className="api-card__btn api-card__btn--edit" title="Modifica">
                      ⚙️
                    </button>
                    <button onClick={() => handleDelete(api.id, api.name)} className="api-card__btn api-card__btn--delete" title="Elimina">
                      🗑️
                    </button>
                  </div>
                </div>
              );
            })}
            
            {/* Card per aggiungere nuova API */}
            <div className="api-card api-card--add">
              <div className="api-card__add-content">
                <span className="api-card__add-icon">➕</span>
                <span className="api-card__add-text">Aggiungi Connettore</span>
              </div>
              <div className="api-card__add-actions">
                <button onClick={() => setShowTemplateLibrary(true)} className="api-card__add-btn">
                  📦 Template
                </button>
                <button onClick={() => setShowOpenAPIImport(true)} className="api-card__add-btn">
                  📄 OpenAPI
                </button>
                <button onClick={() => openWizard()} className="api-card__add-btn">
                  ✏️ Manuale
                </button>
                <button onClick={() => setShowExecutableConfig(true)} className="api-card__add-btn api-card__add-btn--alt">
                  🖥️ Eseguibile
                </button>
                <button onClick={() => setShowDataSourceConfig(true)} className="api-card__add-btn api-card__add-btn--datasource">
                  📁 Data Source
                </button>
              </div>
            </div>
          </div>
        )}
      </main>

      {/* Template Library Modal */}
      {showTemplateLibrary && (
        <TemplateLibraryModal
          onClose={() => setShowTemplateLibrary(false)}
          onSelectTemplate={applyTemplate}
        />
      )}

      {/* OpenAPI Import Modal */}
      {showOpenAPIImport && (
        <OpenAPIImportModal
          onClose={() => setShowOpenAPIImport(false)}
          onImport={applyOpenAPIImport}
        />
      )}

      {/* Executable Config Modal */}
      {showExecutableConfig && (
        <ExecutableConfigModal
          onClose={() => setShowExecutableConfig(false)}
          onSave={async (execConfig) => {
            try {
              await createApiConnection({
                ...execConfig,
                api_type: execConfig.exec_type === 'enricher' ? 'enricher' : 'generator',
                category: 'ml_model'
              });
              setShowExecutableConfig(false);
              loadApiConnections();
            } catch (error) {
              console.error('Errore salvataggio eseguibile:', error);
            }
          }}
        />
      )}

      {/* Data Source Config Modal */}
      {showDataSourceConfig && (
        <DataSourceConfigModal
          onClose={() => setShowDataSourceConfig(false)}
          onSave={async (dsConfig) => {
            try {
              await createApiConnection({
                ...dsConfig,
                api_type: 'generator',
                category: 'datasource'
              });
              setShowDataSourceConfig(false);
              loadApiConnections();
            } catch (error) {
              console.error('Errore salvataggio data source:', error);
            }
          }}
        />
      )}

      {/* Wizard Modal */}
      {showWizard && (
        <div className="api-manager__wizard-overlay" onClick={closeWizard}>
          <div className="api-manager__wizard" onClick={e => e.stopPropagation()}>
            <div className="api-manager__wizard-header">
              <h2 className="api-manager__wizard-title">{editingApi ? 'Modifica Connettore' : 'Nuovo Connettore'} - Step {wizardStep + 1}/5</h2>
              <button onClick={closeWizard} className="api-manager__wizard-close">×</button>
            </div>
            
            {/* Steps indicator */}
            <div className="api-manager__wizard-progress">
              {['Tipo', 'Info', 'Parametri', 'Auth', 'Test'].map((step, idx) => (
                <div 
                  key={idx} 
                  className={`api-manager__step ${wizardStep === idx ? 'api-manager__step--active' : ''} ${wizardStep > idx ? 'api-manager__step--completed' : ''}`}
                  onClick={() => setWizardStep(idx)}
                  style={{ cursor: 'pointer' }}
                >
                  <span className="api-manager__step-number">{idx + 1}</span>
                  <span>{step}</span>
                </div>
              ))}
            </div>

            <div className="api-manager__wizard-body">
              {/* Step 0: Type */}
              {wizardStep === 0 && (
                <div>
                  <p className="api-manager__section-subtitle">Seleziona il tipo di API che vuoi configurare.</p>
                  <div className="api-manager__type-cards">
                    <div 
                      onClick={() => setFormData({ ...formData, api_type: 'enricher' })}
                      className={`api-manager__type-card ${formData.api_type === 'enricher' ? 'api-manager__type-card--selected' : ''}`}
                    >
                      <div className="api-manager__type-card-icon">🔄</div>
                      <h3 className="api-manager__type-card-title">Arricchitore</h3>
                      <p className="api-manager__type-card-desc">Chiama l'API per ogni riga del dataset e aggiunge colonne con i risultati</p>
                      <p className="api-manager__type-card-example">Es: Geocoding, lookup dati, validazione</p>
                    </div>
                    <div 
                      onClick={() => setFormData({ ...formData, api_type: 'generator' })}
                      className={`api-manager__type-card ${formData.api_type === 'generator' ? 'api-manager__type-card--selected' : ''}`}
                    >
                      <div className="api-manager__type-card-icon">📥</div>
                      <h3 className="api-manager__type-card-title">Generatore</h3>
                      <p className="api-manager__type-card-desc">Chiama l'API una volta e genera un nuovo dataset dalla risposta</p>
                      <p className="api-manager__type-card-example">Es: Lista POI, dati meteo, cataloghi</p>
                    </div>
                  </div>
                  
                  {/* Categoria */}
                  <div className="api-manager__category-select">
                    <label className="api-manager__form-label">Categoria</label>
                    <div className="api-manager__category-options">
                      {CATEGORIES.filter(c => c.id !== 'all').map(cat => (
                        <button
                          key={cat.id}
                          type="button"
                          onClick={() => setFormData({ ...formData, category: cat.id })}
                          className={`api-manager__category-option ${formData.category === cat.id ? 'api-manager__category-option--selected' : ''}`}
                        >
                          <span>{cat.icon}</span> {cat.label}
                        </button>
                      ))}
                    </div>
                  </div>
                </div>
              )}

              {/* Step 1: Info */}
              {wizardStep === 1 && (
                <div>
                  <p className="api-manager__section-subtitle">Inserisci le informazioni di base dell'API.</p>
                  <div className="api-manager__form-group">
                    <label className="api-manager__form-label">Nome API *</label>
                    <input type="text" value={formData.name} onChange={e => setFormData({ ...formData, name: e.target.value })} placeholder="Es: Nominatim Geocoding" className="api-manager__form-input" />
                  </div>
                  <div className="api-manager__form-group">
                    <label className="api-manager__form-label">Descrizione</label>
                    <textarea value={formData.description} onChange={e => setFormData({ ...formData, description: e.target.value })} placeholder="Descrizione opzionale..." className="api-manager__form-textarea" rows={2} />
                  </div>
                  <div className="api-manager__form-row">
                    <div className="api-manager__form-group" style={{ flex: 3 }}>
                      <label className="api-manager__form-label">URL Endpoint *</label>
                      <input type="text" value={formData.url} onChange={e => setFormData({ ...formData, url: e.target.value })} placeholder="https://api.example.com/endpoint" className="api-manager__form-input api-manager__form-input--mono" />
                    </div>
                    <div className="api-manager__form-group" style={{ flex: 1 }}>
                      <label className="api-manager__form-label">Metodo</label>
                      <select value={formData.method} onChange={e => setFormData({ ...formData, method: e.target.value })} className="api-manager__form-select">
                        <option value="GET">GET</option>
                        <option value="POST">POST</option>
                      </select>
                    </div>
                  </div>
                  
                  {/* Opzioni avanzate */}
                  <details className="api-manager__advanced-options">
                    <summary>⚙️ Opzioni avanzate</summary>
                    <div className="api-manager__form-row api-manager__form-row--4">
                      <div className="api-manager__form-group">
                        <label className="api-manager__form-label">Rate Limit (req/s)</label>
                        <input type="number" value={formData.options.rate_limit} onChange={e => setFormData({ ...formData, options: { ...formData.options, rate_limit: parseInt(e.target.value) || 5 } })} className="api-manager__form-input" min="1" max="100" />
                      </div>
                      <div className="api-manager__form-group">
                        <label className="api-manager__form-label">Timeout (sec)</label>
                        <input type="number" value={formData.options.timeout} onChange={e => setFormData({ ...formData, options: { ...formData.options, timeout: parseInt(e.target.value) || 30 } })} className="api-manager__form-input" min="5" max="300" />
                      </div>
                      <div className="api-manager__form-group">
                        <label className="api-manager__form-label">Max Retry</label>
                        <input type="number" value={formData.options.retry_max} onChange={e => setFormData({ ...formData, options: { ...formData.options, retry_max: parseInt(e.target.value) || 3 } })} className="api-manager__form-input" min="0" max="10" />
                      </div>
                    </div>
                  </details>
                </div>
              )}

              {/* Step 2: Parameters */}
              {wizardStep === 2 && (
                <div>
                  <p className="api-manager__section-subtitle">
                    {formData.api_type === 'enricher' 
                      ? 'Definisci i parametri. "Mappato" = valore da colonna, "Fisso" = valore costante.'
                      : 'Definisci i parametri fissi per la chiamata API.'}
                  </p>
                  <div className="api-manager__params-list">
                    {formData.parameters.map((param, idx) => (
                      <div key={idx} className="api-manager__param-row">
                        <input type="text" value={param.name} onChange={e => updateParameter(idx, 'name', e.target.value)} placeholder="Nome parametro" className="api-manager__param-input" />
                        <select value={param.type} onChange={e => updateParameter(idx, 'type', e.target.value)} className="api-manager__param-select">
                          <option value="string">Testo</option>
                          <option value="number">Numero</option>
                          <option value="boolean">Booleano</option>
                        </select>
                        {formData.api_type === 'enricher' && (
                          <select 
                            value={param.mode || 'mapped'} 
                            onChange={e => updateParameter(idx, 'mode', e.target.value)} 
                            className={`api-manager__param-select api-manager__param-select--${param.mode || 'mapped'}`}
                          >
                            <option value="mapped">📥 Mappato</option>
                            <option value="fixed">📌 Fisso</option>
                          </select>
                        )}
                        {(param.mode === 'fixed' || formData.api_type === 'generator') && (
                          <input 
                            type="text" 
                            value={formData.static_parameters[param.name] || ''} 
                            onChange={e => setFormData({ ...formData, static_parameters: { ...formData.static_parameters, [param.name]: e.target.value } })} 
                            placeholder="Valore fisso" 
                            className="api-manager__param-input api-manager__param-input--fixed"
                          />
                        )}
                        <button onClick={() => removeParameter(idx)} className="api-manager__param-delete">🗑️</button>
                      </div>
                    ))}
                    <button onClick={addParameter} className="api-manager__add-param-btn">+ Aggiungi parametro</button>
                  </div>
                  <div className="api-manager__info-box api-manager__info-box--info">
                    {formData.api_type === 'enricher' 
                      ? <>ℹ️ <strong>Mappato:</strong> valore da colonna dataset &nbsp;|&nbsp; 📌 <strong>Fisso:</strong> valore costante</>
                      : <>ℹ️ L'API verrà chiamata una volta con questi parametri fissi</>
                    }
                  </div>
                </div>
              )}

              {/* Step 3: Auth + Headers */}
              {wizardStep === 3 && (
                <div>
                  <p className="api-manager__section-subtitle">Configura autenticazione e headers HTTP.</p>
                  
                  <h4 className="api-manager__section-title">🔐 Autenticazione</h4>
                  <div className="api-manager__auth-options">
                    {[
                      { value: 'none', label: 'Nessuna', icon: '🔓' }, 
                      { value: 'api_key', label: 'API Key', icon: '🔑' }, 
                      { value: 'oauth2_password', label: 'OAuth2', icon: '🔐' }
                    ].map(opt => (
                      <label key={opt.value} className={`api-manager__auth-type ${formData.auth.type === opt.value ? 'api-manager__auth-type--selected' : ''}`}>
                        <input type="radio" name="authType" value={opt.value} checked={formData.auth.type === opt.value} onChange={e => setFormData({ ...formData, auth: { type: e.target.value } })} />
                        <span>{opt.icon}</span> {opt.label}
                      </label>
                    ))}
                  </div>
                  
                  {formData.auth.type === 'api_key' && (
                    <div className="api-manager__auth-config">
                      <div className="api-manager__form-row">
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
                        <div className="api-manager__auth-position">
                          {['header', 'query'].map(loc => (
                            <label key={loc} className={`api-manager__auth-type ${(formData.auth.location || 'header') === loc ? 'api-manager__auth-type--selected' : ''}`}>
                              <input type="radio" name="keyLocation" value={loc} checked={(formData.auth.location || 'header') === loc} onChange={e => setFormData({ ...formData, auth: { ...formData.auth, location: e.target.value } })} />
                              {loc === 'header' ? 'Header' : 'Query param'}
                            </label>
                          ))}
                        </div>
                      </div>
                    </div>
                  )}
                  
                  {formData.auth.type === 'oauth2_password' && (
                    <div className="api-manager__auth-config">
                      <div className="api-manager__form-group">
                        <label className="api-manager__form-label">Token URL *</label>
                        <input type="text" value={formData.auth.token_url || ''} onChange={e => setFormData({ ...formData, auth: { ...formData.auth, token_url: e.target.value } })} placeholder="https://api.example.com/token" className="api-manager__form-input api-manager__form-input--mono" />
                      </div>
                      <div className="api-manager__form-row">
                        <div className="api-manager__form-group">
                          <label className="api-manager__form-label">Username *</label>
                          <input type="text" value={formData.auth.username || ''} onChange={e => setFormData({ ...formData, auth: { ...formData.auth, username: e.target.value } })} placeholder="Username" className="api-manager__form-input" />
                        </div>
                        <div className="api-manager__form-group">
                          <label className="api-manager__form-label">Password *</label>
                          <input type="password" value={formData.auth.password || ''} onChange={e => setFormData({ ...formData, auth: { ...formData.auth, password: e.target.value } })} placeholder="••••••••" className="api-manager__form-input" />
                        </div>
                      </div>
                    </div>
                  )}

                  <h4 className="api-manager__section-title" style={{ marginTop: '24px' }}>📋 Headers Personalizzati</h4>
                  <div className="api-manager__params-list">
                    {customHeadersList.map((header, idx) => (
                      <div key={idx} className="api-manager__param-row">
                        <input type="text" value={header.key} onChange={e => updateCustomHeader(idx, 'key', e.target.value)} placeholder="Nome header" className="api-manager__param-input" />
                        <input type="text" value={header.value} onChange={e => updateCustomHeader(idx, 'value', e.target.value)} placeholder="Valore" className="api-manager__param-input" />
                        <button onClick={() => removeCustomHeader(idx)} className="api-manager__param-delete">🗑️</button>
                      </div>
                    ))}
                    <button onClick={addCustomHeader} className="api-manager__add-param-btn">+ Aggiungi header</button>
                  </div>
                </div>
              )}

              {/* Step 4: Test */}
              {wizardStep === 4 && (
                <div>
                  <p className="api-manager__section-subtitle">Testa l'API e seleziona i campi da importare.</p>
                  <div className="api-manager__test-section">
                    <h4 className="api-manager__test-title">📝 Dati di test</h4>
                    {formData.parameters.length === 0 ? (
                      <div className="api-manager__info-box api-manager__info-box--warning">
                        ⚠️ Nessun parametro definito. Torna allo Step 3 per aggiungerli.
                      </div>
                    ) : (
                      formData.parameters.map((param, idx) => {
                        const isFixed = param.mode === 'fixed' || formData.api_type === 'generator';
                        return (
                          <div key={idx} className="api-manager__test-param-row">
                            <label className="api-manager__test-param-label">
                              {param.name}
                              <span className={`api-manager__param-badge api-manager__param-badge--${isFixed ? 'fixed' : 'mapped'}`}>
                                {isFixed ? '📌 fisso' : '📥 mappato'}
                              </span>
                            </label>
                            <input 
                              type={param.type === 'number' ? 'number' : 'text'} 
                              value={isFixed ? (formData.static_parameters[param.name] || '') : (testParams[param.name] || '')} 
                              onChange={e => !isFixed && setTestParams({ ...testParams, [param.name]: e.target.value })} 
                              disabled={isFixed}
                              placeholder={isFixed ? '' : `Valore test per ${param.name}`}
                              className={`api-manager__test-param-input ${isFixed ? 'api-manager__test-param-input--disabled' : ''}`}
                            />
                          </div>
                        );
                      })
                    )}
                    <button onClick={handleTest} disabled={testing || formData.parameters.length === 0} className="api-manager__test-btn">
                      {testing ? '⏳ Test in corso...' : '🚀 Esegui Test'}
                    </button>
                  </div>
                  
                  {testResult && (
                    <div className={`api-manager__test-result ${testResult.success ? 'api-manager__test-result--success' : 'api-manager__test-result--error'}`}>
                      <h4 className={`api-manager__test-result-title ${testResult.success ? '' : 'api-manager__test-result-title--error'}`}>
                        {testResult.success ? '✅ Test riuscito!' : '❌ Test fallito'}
                      </h4>
                      {testResult.success && testResult.available_fields?.length > 0 && (
                        <>
                          <p>Seleziona i campi da importare:</p>
                          <div className="api-manager__fields-grid">
                            {testResult.available_fields.map((field, idx) => {
                              const isSelected = formData.output_mapping.find(m => m.path === field.path);
                              return (
                                <div key={idx} onClick={() => toggleOutputField(field)} className={`api-manager__field-item ${isSelected ? 'api-manager__field-item--selected' : ''}`}>
                                  <input type="checkbox" checked={!!isSelected} onChange={() => {}} />
                                  <div className="api-manager__field-info">
                                    <span className="api-manager__field-name">{field.name}</span>
                                    <span className={`api-manager__field-type api-manager__field-type--${field.type}`}>{field.type}</span>
                                    {field.sample_value && <span className="api-manager__field-sample">Es: {field.sample_value}</span>}
                                  </div>
                                </div>
                              );
                            })}
                          </div>
                          {formData.output_mapping.length > 0 && (
                            <div className="api-manager__selected-fields">
                              <h5>Campi selezionati ({formData.output_mapping.length}):</h5>
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
                      )}
                      {!testResult.success && (
                        <p className="api-manager__test-error-msg">{testResult.error}</p>
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
                <button 
                  onClick={() => setWizardStep(s => s + 1)} 
                  disabled={wizardStep === 1 && (!formData.name || !formData.url)}
                  className="api-manager__wizard-btn api-manager__wizard-btn--primary"
                >
                  Avanti →
                </button>
              ) : (
                <button 
                  onClick={handleSave} 
                  disabled={formData.output_mapping.length === 0}
                  className="api-manager__wizard-btn api-manager__wizard-btn--success"
                >
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