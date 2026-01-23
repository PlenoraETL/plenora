import React, { useState } from 'react';

function OpenAPIImportModal({ onClose, onImport }) {
  const [importMode, setImportMode] = useState('url');
  const [url, setUrl] = useState('');
  const [pastedSchema, setPastedSchema] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [parsedSpec, setParsedSpec] = useState(null);
  const [selectedEndpoints, setSelectedEndpoints] = useState([]);
  const [authConfig, setAuthConfig] = useState({
    type: 'none',
    key_name: 'X-API-Key',
    key_value: '',
    location: 'header'
  });

  const parseOpenAPISchema = (schema) => {
    try {
      const spec = typeof schema === 'string' ? JSON.parse(schema) : schema;
      const baseUrl = spec.servers?.[0]?.url || '';
      const apiName = spec.info?.title || 'API Importata';
      const apiDescription = spec.info?.description || '';
      const endpoints = [];

      Object.entries(spec.paths || {}).forEach(([path, methods]) => {
        Object.entries(methods).forEach(([method, details]) => {
          if (['get', 'post', 'put', 'delete'].includes(method.toLowerCase())) {
            const parameters = [];
            (details.parameters || []).forEach(param => {
              parameters.push({
                name: param.name,
                type: param.schema?.type || 'string',
                required: param.required || false,
                location: param.in,
                description: param.description || ''
              });
            });

            if (details.requestBody?.content?.['application/json']?.schema) {
              const bodySchema = details.requestBody.content['application/json'].schema;
              if (bodySchema.properties) {
                Object.entries(bodySchema.properties).forEach(([propName, propSchema]) => {
                  parameters.push({
                    name: propName,
                    type: propSchema.type || 'string',
                    required: (bodySchema.required || []).includes(propName),
                    location: 'body',
                    description: propSchema.description || ''
                  });
                });
              }
            }

            const outputFields = [];
            const responseSchema = details.responses?.['200']?.content?.['application/json']?.schema;
            if (responseSchema) {
              extractOutputFields(responseSchema, '', outputFields);
            }

            endpoints.push({
              id: `${method.toUpperCase()}_${path}`,
              path,
              method: method.toUpperCase(),
              summary: details.summary || `${method.toUpperCase()} ${path}`,
              description: details.description || '',
              operationId: details.operationId || '',
              parameters,
              outputFields,
              selected: false
            });
          }
        });
      });

      return { baseUrl, apiName, apiDescription, endpoints, securitySchemes: spec.components?.securitySchemes || {} };
    } catch (e) {
      throw new Error(`Errore parsing OpenAPI: ${e.message}`);
    }
  };

  const extractOutputFields = (schema, prefix, fields, depth = 0) => {
    if (depth > 5) return;
    if (schema.type === 'object' && schema.properties) {
      Object.entries(schema.properties).forEach(([name, prop]) => {
        const path = prefix ? `${prefix}.${name}` : name;
        if (prop.type === 'object') {
          extractOutputFields(prop, path, fields, depth + 1);
        } else if (prop.type === 'array' && prop.items) {
          const arrayPath = `${path}[*]`;
          if (prop.items.type === 'object') {
            extractOutputFields(prop.items, arrayPath, fields, depth + 1);
          } else {
            fields.push({ path: arrayPath, name, type: prop.items.type || 'string' });
          }
        } else {
          fields.push({ path, name, type: prop.type || 'string' });
        }
      });
    } else if (schema.type === 'array' && schema.items) {
      extractOutputFields(schema.items, `${prefix}[*]`, fields, depth + 1);
    }
  };

  const handleFetchSchema = async () => {
    setLoading(true);
    setError(null);
    setParsedSpec(null);
    try {
      let schema;
      if (importMode === 'url') {
        if (!url) throw new Error('Inserisci un URL');
        const response = await fetch(url);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        schema = await response.json();
      } else if (importMode === 'paste') {
        if (!pastedSchema) throw new Error('Incolla lo schema OpenAPI');
        schema = JSON.parse(pastedSchema);
      }
      const parsed = parseOpenAPISchema(schema);
      setParsedSpec(parsed);
      setSelectedEndpoints([]);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  const handleFileUpload = async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    setLoading(true);
    setError(null);
    try {
      const text = await file.text();
      const schema = JSON.parse(text);
      const parsed = parseOpenAPISchema(schema);
      setParsedSpec(parsed);
      setSelectedEndpoints([]);
    } catch (e) {
      setError(`Errore lettura file: ${e.message}`);
    } finally {
      setLoading(false);
    }
  };

  const toggleEndpoint = (endpointId) => {
    setSelectedEndpoints(prev =>
      prev.includes(endpointId) ? prev.filter(id => id !== endpointId) : [...prev, endpointId]
    );
  };

  const selectAllEndpoints = () => {
    if (selectedEndpoints.length === parsedSpec.endpoints.length) {
      setSelectedEndpoints([]);
    } else {
      setSelectedEndpoints(parsedSpec.endpoints.map(e => e.id));
    }
  };

  const handleImport = () => {
    if (!parsedSpec || selectedEndpoints.length === 0) return;
    const templates = parsedSpec.endpoints
      .filter(ep => selectedEndpoints.includes(ep.id))
      .map(ep => ({
        name: ep.summary || `${ep.method} ${ep.path}`,
        description: ep.description || `Endpoint importato da ${parsedSpec.apiName}`,
        api_type: 'enricher',
        category: 'custom',
        url: `${parsedSpec.baseUrl}${ep.path}`,
        method: ep.method,
        auth: authConfig,
        parameters: ep.parameters.map(p => ({
          name: p.name,
          type: p.type === 'integer' ? 'number' : p.type,
          required: p.required,
          mode: 'mapped',
          location: p.location
        })),
        static_parameters: {},
        output_mapping: ep.outputFields.slice(0, 10).map(f => ({ path: f.path, column: f.name, type: f.type })),
        options: { rate_limit: 5, timeout: 30, retry_on_error: true, retry_max: 3 }
      }));
    onImport(templates);
  };

  return (
    <div className="openapi-modal__overlay" onClick={onClose}>
      <div className="openapi-modal" onClick={e => e.stopPropagation()}>
        <div className="openapi-modal__header">
          <h2 className="openapi-modal__title">📄 Import da OpenAPI Schema</h2>
          <button onClick={onClose} className="openapi-modal__close">×</button>
        </div>

        <div className="openapi-modal__content">
          {!parsedSpec ? (
            <div className="openapi-modal__step">
              <p className="openapi-modal__subtitle">Importa endpoint API da uno schema OpenAPI 3.0 (Swagger).</p>
              <div className="openapi-modal__tabs">
                <button onClick={() => setImportMode('url')} className={`openapi-modal__tab ${importMode === 'url' ? 'openapi-modal__tab--active' : ''}`}>🌐 Da URL</button>
                <button onClick={() => setImportMode('file')} className={`openapi-modal__tab ${importMode === 'file' ? 'openapi-modal__tab--active' : ''}`}>📁 Da File</button>
                <button onClick={() => setImportMode('paste')} className={`openapi-modal__tab ${importMode === 'paste' ? 'openapi-modal__tab--active' : ''}`}>📋 Incolla JSON</button>
              </div>

              {importMode === 'url' && (
                <div className="openapi-modal__input-group">
                  <label>URL Schema OpenAPI:</label>
                  <input type="text" value={url} onChange={e => setUrl(e.target.value)} placeholder="https://api.example.com/openapi.json" className="openapi-modal__input" />
                  <p className="openapi-modal__hint">Es: <code>http://analisi.re-val.it:5001/openapi.json</code></p>
                </div>
              )}

              {importMode === 'file' && (
                <div className="openapi-modal__input-group">
                  <label>Carica file OpenAPI (JSON/YAML):</label>
                  <input type="file" accept=".json,.yaml,.yml" onChange={handleFileUpload} className="openapi-modal__file-input" />
                </div>
              )}

              {importMode === 'paste' && (
                <div className="openapi-modal__input-group">
                  <label>Incolla schema OpenAPI:</label>
                  <textarea value={pastedSchema} onChange={e => setPastedSchema(e.target.value)} placeholder='{"openapi": "3.0.0", ...}' className="openapi-modal__textarea" rows={10} />
                </div>
              )}

              {error && <div className="openapi-modal__error">❌ {error}</div>}

              {importMode !== 'file' && (
                <button onClick={handleFetchSchema} disabled={loading || (importMode === 'url' && !url) || (importMode === 'paste' && !pastedSchema)} className="openapi-modal__fetch-btn">
                  {loading ? '⏳ Caricamento...' : '🔍 Analizza Schema'}
                </button>
              )}
            </div>
          ) : (
            <div className="openapi-modal__step">
              <div className="openapi-modal__spec-info">
                <h3>{parsedSpec.apiName}</h3>
                <p>{parsedSpec.apiDescription}</p>
                <p className="openapi-modal__base-url"><strong>Base URL:</strong> <code>{parsedSpec.baseUrl}</code></p>
              </div>

              <div className="openapi-modal__auth-section">
                <h4>🔐 Autenticazione</h4>
                <div className="openapi-modal__auth-options">
                  <label className={authConfig.type === 'none' ? 'selected' : ''}>
                    <input type="radio" name="auth" checked={authConfig.type === 'none'} onChange={() => setAuthConfig({ ...authConfig, type: 'none' })} /> Nessuna
                  </label>
                  <label className={authConfig.type === 'api_key' ? 'selected' : ''}>
                    <input type="radio" name="auth" checked={authConfig.type === 'api_key'} onChange={() => setAuthConfig({ ...authConfig, type: 'api_key' })} /> API Key
                  </label>
                </div>
                {authConfig.type === 'api_key' && (
                  <div className="openapi-modal__auth-fields">
                    <input type="text" value={authConfig.key_name} onChange={e => setAuthConfig({ ...authConfig, key_name: e.target.value })} placeholder="Nome header" className="openapi-modal__input openapi-modal__input--small" />
                    <input type="password" value={authConfig.key_value} onChange={e => setAuthConfig({ ...authConfig, key_value: e.target.value })} placeholder="API Key" className="openapi-modal__input openapi-modal__input--small" />
                  </div>
                )}
              </div>

              <div className="openapi-modal__endpoints-section">
                <div className="openapi-modal__endpoints-header">
                  <h4>📡 Endpoint disponibili ({parsedSpec.endpoints.length})</h4>
                  <button onClick={selectAllEndpoints} className="openapi-modal__select-all">
                    {selectedEndpoints.length === parsedSpec.endpoints.length ? 'Deseleziona tutti' : 'Seleziona tutti'}
                  </button>
                </div>
                <div className="openapi-modal__endpoints-list">
                  {parsedSpec.endpoints.map(endpoint => (
                    <div key={endpoint.id} className={`openapi-modal__endpoint ${selectedEndpoints.includes(endpoint.id) ? 'openapi-modal__endpoint--selected' : ''}`} onClick={() => toggleEndpoint(endpoint.id)}>
                      <input type="checkbox" checked={selectedEndpoints.includes(endpoint.id)} onChange={() => {}} />
                      <span className={`openapi-modal__method openapi-modal__method--${endpoint.method.toLowerCase()}`}>{endpoint.method}</span>
                      <span className="openapi-modal__path">{endpoint.path}</span>
                      <span className="openapi-modal__summary">{endpoint.summary}</span>
                      <div className="openapi-modal__endpoint-meta">
                        <span className="openapi-modal__param-count">{endpoint.parameters.length} params</span>
                        <span className="openapi-modal__output-count">{endpoint.outputFields.length} output</span>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
              <button onClick={() => setParsedSpec(null)} className="openapi-modal__back-btn">← Torna indietro</button>
            </div>
          )}
        </div>

        <div className="openapi-modal__footer">
          {parsedSpec && <div className="openapi-modal__footer-info">{selectedEndpoints.length} endpoint selezionati</div>}
          <div className="openapi-modal__footer-actions">
            <button onClick={onClose} className="openapi-modal__cancel-btn">Annulla</button>
            {parsedSpec && (
              <button onClick={handleImport} disabled={selectedEndpoints.length === 0} className="openapi-modal__import-btn">
                📥 Importa {selectedEndpoints.length} endpoint
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

export default OpenAPIImportModal;
