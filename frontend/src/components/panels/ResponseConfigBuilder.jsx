/**
 * ResponseConfigBuilder.jsx — Builder visuale per mapping risposta JSON
 *
 * Visualizza la struttura ad albero di una risposta JSON e permette
 * di selezionare i campi da estrarre come colonne output.
 * Supporta path annidati (es. "result.geometry.coordinates[0]").
 *
 * Usato da: ApiEnrichNode per configurare l'output delle API
 */
import React, { useState, useMemo, useCallback } from 'react';
import './ResponseConfigBuilder.css';

/**
 * ResponseConfigBuilder - Componente visuale per configurare l'estrazione
 * di dati da risposte JSON con strutture annidate.
 * 
 * Permette di:
 * 1. Visualizzare l'albero JSON della risposta
 * 2. Marcare array da "esplodere" (iterate_on)
 * 3. Selezionare campi da estrarre (mappings)
 * 4. Configurare trasformazioni (transforms)
 * 5. Preview del risultato
 */

// Operazioni di trasformazione disponibili
const TRANSFORM_OPERATIONS = [
  { id: 'none', label: 'Nessuna', icon: '−' },
  { id: 'kelvin_to_celsius', label: 'Kelvin → Celsius', icon: '🌡️' },
  { id: 'celsius_to_kelvin', label: 'Celsius → Kelvin', icon: '🌡️' },
  { id: 'round', label: 'Arrotonda', icon: '🔢', hasValue: true, valueLabel: 'Decimali' },
  { id: 'multiply', label: 'Moltiplica', icon: '✖️', hasValue: true, valueLabel: 'Fattore' },
  { id: 'divide', label: 'Dividi', icon: '➗', hasValue: true, valueLabel: 'Divisore' },
  { id: 'add', label: 'Aggiungi', icon: '➕', hasValue: true, valueLabel: 'Valore' },
  { id: 'subtract', label: 'Sottrai', icon: '➖', hasValue: true, valueLabel: 'Valore' },
  { id: 'uppercase', label: 'MAIUSCOLO', icon: 'Aa' },
  { id: 'lowercase', label: 'minuscolo', icon: 'aa' },
  { id: 'prefix', label: 'Prefisso', icon: '←', hasValue: true, valueLabel: 'Prefisso' },
  { id: 'suffix', label: 'Suffisso', icon: '→', hasValue: true, valueLabel: 'Suffisso' },
];

// Preset predefiniti
const PRESETS = [
  { id: '', label: '-- Nessun preset --' },
  { id: 'italiameteo_observations', label: '🌡️ ItaliaMeteo Observations' },
  { id: 'geojson_features', label: '🗺️ GeoJSON Features' },
  { id: 'simple_array', label: '📋 Array semplice' },
];

function ResponseConfigBuilder({ 
  sampleData,           // Dati JSON di esempio dalla risposta API
  initialConfig,        // Configurazione iniziale (per editing)
  onChange,             // Callback quando la config cambia
  onPreview             // Callback per preview risultato
}) {
  // State per la configurazione
  const [usePreset, setUsePreset] = useState(initialConfig?.preset || '');
  const [rootPath, setRootPath] = useState(initialConfig?.root_path || '');
  const [iterateOn, setIterateOn] = useState(initialConfig?.iterate_on || []);
  const [mappings, setMappings] = useState(initialConfig?.mappings || []);
  const [transforms, setTransforms] = useState(initialConfig?.transforms || []);
  
  // State UI
  const [expandedPaths, setExpandedPaths] = useState(new Set(['']));
  const [activeTab, setActiveTab] = useState('structure'); // structure | mappings | transforms | preview
  const [previewData, setPreviewData] = useState(null);
  const [previewError, setPreviewError] = useState(null);

  // Analizza la struttura del JSON
  const jsonStructure = useMemo(() => {
    if (!sampleData) return null;
    return analyzeJsonStructure(sampleData, '', 0);
  }, [sampleData]);

  // Genera la configurazione finale
  const generatedConfig = useMemo(() => {
    if (usePreset) {
      return { response_preset: usePreset };
    }
    
    const config = {
      response_config: {
        root_path: rootPath,
        iterate_on: iterateOn,
        mappings: mappings,
        transforms: transforms.filter(t => t.operation !== 'none')
      }
    };
    
    return config;
  }, [usePreset, rootPath, iterateOn, mappings, transforms]);

  // Notifica il parent quando cambia la config
  // onChange viene messo in un ref per evitare loop infiniti
  // (il parent passa una funzione inline che cambia riferimento ad ogni render)
  const onChangeRef = React.useRef(onChange);
  React.useEffect(() => { onChangeRef.current = onChange; });
  React.useEffect(() => {
    onChangeRef.current?.(generatedConfig);
  }, [generatedConfig]);

  // Toggle espansione nodo
  const toggleExpand = useCallback((path) => {
    setExpandedPaths(prev => {
      const next = new Set(prev);
      if (next.has(path)) {
        next.delete(path);
      } else {
        next.add(path);
      }
      return next;
    });
  }, []);

  // Toggle array come "iterate_on"
  const toggleIterate = useCallback((path, alias) => {
    setIterateOn(prev => {
      const existing = prev.findIndex(i => i.path === path);
      if (existing >= 0) {
        return prev.filter((_, i) => i !== existing);
      } else {
        // Calcola l'alias suggerito
        const suggestedAlias = alias || path.split('.').pop() || `level_${prev.length}`;
        return [...prev, { path, as: suggestedAlias }];
      }
    });
  }, []);

  // Toggle campo come mapping
  const toggleMapping = useCallback((fullPath, name, type) => {
    setMappings(prev => {
      const existing = prev.findIndex(m => m.path === fullPath);
      if (existing >= 0) {
        return prev.filter((_, i) => i !== existing);
      } else {
        return [...prev, { 
          column: name, 
          path: fullPath,
          type: type || 'string'
        }];
      }
    });
  }, []);

  // Aggiorna alias iterate
  const updateIterateAlias = useCallback((index, newAlias) => {
    setIterateOn(prev => prev.map((item, i) => 
      i === index ? { ...item, as: newAlias } : item
    ));
  }, []);

  // Aggiorna nome colonna mapping
  const updateMappingColumn = useCallback((index, newColumn) => {
    setMappings(prev => prev.map((item, i) => 
      i === index ? { ...item, column: newColumn } : item
    ));
  }, []);

  // Aggiungi trasformazione
  const addTransform = useCallback((sourceColumn) => {
    setTransforms(prev => [...prev, {
      column: `${sourceColumn}_transformed`,
      source: sourceColumn,
      operation: 'none',
      value: ''
    }]);
  }, []);

  // Aggiorna trasformazione
  const updateTransform = useCallback((index, field, value) => {
    setTransforms(prev => prev.map((t, i) => 
      i === index ? { ...t, [field]: value } : t
    ));
  }, []);

  // Rimuovi trasformazione
  const removeTransform = useCallback((index) => {
    setTransforms(prev => prev.filter((_, i) => i !== index));
  }, []);

  // Genera preview
  const generatePreview = useCallback(async () => {
    if (!sampleData) return;
    
    setPreviewError(null);
    
    try {
      // Simula l'estrazione localmente
      const rows = extractRowsFromConfig(sampleData, {
        root_path: rootPath,
        iterate_on: iterateOn,
        mappings: mappings,
        transforms: transforms
      });
      
      setPreviewData(rows.slice(0, 10)); // Max 10 righe preview
    } catch (err) {
      setPreviewError(err.message);
      setPreviewData(null);
    }
  }, [sampleData, rootPath, iterateOn, mappings, transforms]);

  // Espandi tutti i nodi fino al livello 2
  const expandAll = useCallback(() => {
    if (!jsonStructure) return;
    const paths = new Set(['']);
    collectPaths(jsonStructure, paths, 0, 3);
    setExpandedPaths(paths);
  }, [jsonStructure]);

  // Collapse tutti
  const collapseAll = useCallback(() => {
    setExpandedPaths(new Set(['']));
  }, []);

  // Auto-detect: seleziona automaticamente campi scalari
  const autoDetectMappings = useCallback(() => {
    if (!jsonStructure) return;
    
    const detectedMappings = [];
    collectScalarFields(jsonStructure, '', detectedMappings, iterateOn);
    setMappings(detectedMappings);
  }, [jsonStructure, iterateOn]);

  if (!sampleData) {
    return (
      <div className="response-config-builder response-config-builder--empty">
        <div className="response-config-builder__empty-state">
          <span className="response-config-builder__empty-icon">📭</span>
          <p>Esegui prima il test dell'API per vedere la struttura della risposta</p>
        </div>
      </div>
    );
  }

  return (
    <div className="response-config-builder">
      {/* Header con preset */}
      <div className="response-config-builder__header">
        <div className="response-config-builder__preset-row">
          <label className="response-config-builder__label">
            🎯 Preset:
          </label>
          <select 
            value={usePreset} 
            onChange={e => setUsePreset(e.target.value)}
            className="response-config-builder__select"
          >
            {PRESETS.map(p => (
              <option key={p.id} value={p.id}>{p.label}</option>
            ))}
          </select>
          {usePreset && (
            <span className="response-config-builder__preset-note">
              ✨ Configurazione automatica attiva
            </span>
          )}
        </div>
      </div>

      {/* Se usa preset, mostra solo info */}
      {usePreset ? (
        <div className="response-config-builder__preset-info">
          <div className="response-config-builder__info-box">
            <h4>📋 Preset: {PRESETS.find(p => p.id === usePreset)?.label}</h4>
            <p>La configurazione verrà applicata automaticamente in base al formato della risposta.</p>
            <button type="button" 
              onClick={() => setUsePreset('')}
              className="response-config-builder__btn response-config-builder__btn--secondary"
            >
              ✏️ Configura manualmente
            </button>
          </div>
        </div>
      ) : (
        <>
          {/* Tabs */}
          <div className="response-config-builder__tabs">
            <button type="button" 
              className={`response-config-builder__tab ${activeTab === 'structure' ? 'response-config-builder__tab--active' : ''}`}
              onClick={() => setActiveTab('structure')}
            >
              🌳 Struttura
            </button>
            <button type="button" 
              className={`response-config-builder__tab ${activeTab === 'mappings' ? 'response-config-builder__tab--active' : ''}`}
              onClick={() => setActiveTab('mappings')}
            >
              📋 Mappings ({mappings.length})
            </button>
            <button type="button" 
              className={`response-config-builder__tab ${activeTab === 'transforms' ? 'response-config-builder__tab--active' : ''}`}
              onClick={() => setActiveTab('transforms')}
            >
              ⚙️ Trasformazioni ({transforms.length})
            </button>
            <button type="button" 
              className={`response-config-builder__tab ${activeTab === 'preview' ? 'response-config-builder__tab--active' : ''}`}
              onClick={() => { setActiveTab('preview'); generatePreview(); }}
            >
              👁️ Preview
            </button>
          </div>

          {/* Tab: Struttura */}
          {activeTab === 'structure' && (
            <div className="response-config-builder__panel">
              {/* Toolbar */}
              <div className="response-config-builder__toolbar">
                <div className="response-config-builder__toolbar-left">
                  <label className="response-config-builder__label">Root path:</label>
                  <input 
                    type="text"
                    value={rootPath}
                    onChange={e => setRootPath(e.target.value)}
                    placeholder="es: data, results.items"
                    className="response-config-builder__input response-config-builder__input--small"
                  />
                </div>
                <div className="response-config-builder__toolbar-right">
                  <button type="button" onClick={expandAll} className="response-config-builder__btn response-config-builder__btn--small">
                    ➕ Espandi
                  </button>
                  <button type="button" onClick={collapseAll} className="response-config-builder__btn response-config-builder__btn--small">
                    ➖ Comprimi
                  </button>
                  <button type="button" onClick={autoDetectMappings} className="response-config-builder__btn response-config-builder__btn--small response-config-builder__btn--primary">
                    🔮 Auto-detect
                  </button>
                </div>
              </div>

              {/* Legenda */}
              <div className="response-config-builder__legend">
                <span className="response-config-builder__legend-item">
                  <span className="response-config-builder__badge response-config-builder__badge--array">array</span>
                  Clicca per esplodere
                </span>
                <span className="response-config-builder__legend-item">
                  <span className="response-config-builder__badge response-config-builder__badge--object">object</span>
                  Espandibile
                </span>
                <span className="response-config-builder__legend-item">
                  <input type="checkbox" checked readOnly /> Campo selezionato
                </span>
              </div>

              {/* Tree View */}
              <div className="response-config-builder__tree">
                {jsonStructure && (
                  <JsonTreeNode 
                    node={jsonStructure}
                    path=""
                    depth={0}
                    expandedPaths={expandedPaths}
                    iterateOn={iterateOn}
                    mappings={mappings}
                    onToggleExpand={toggleExpand}
                    onToggleIterate={toggleIterate}
                    onToggleMapping={toggleMapping}
                  />
                )}
              </div>
            </div>
          )}

          {/* Tab: Mappings */}
          {activeTab === 'mappings' && (
            <div className="response-config-builder__panel">
              <div className="response-config-builder__section-header">
                <h4>📋 Campi da estrarre</h4>
                <p>Rinomina le colonne di output se necessario</p>
              </div>

              {/* Iterate On */}
              {iterateOn.length > 0 && (
                <div className="response-config-builder__iterate-section">
                  <h5>🔄 Array da esplodere (iterate_on):</h5>
                  <div className="response-config-builder__iterate-list">
                    {iterateOn.map((item, idx) => (
                      <div key={idx} className="response-config-builder__iterate-item">
                        <span className="response-config-builder__iterate-level">Livello {idx + 1}:</span>
                        <code className="response-config-builder__iterate-path">{item.path || '(root)'}</code>
                        <span className="response-config-builder__iterate-arrow">→</span>
                        <input 
                          type="text"
                          value={item.as}
                          onChange={e => updateIterateAlias(idx, e.target.value)}
                          className="response-config-builder__input response-config-builder__input--small"
                          placeholder="alias"
                        />
                        <button type="button" 
                          onClick={() => toggleIterate(item.path)}
                          className="response-config-builder__btn response-config-builder__btn--danger response-config-builder__btn--icon"
                        >
                          🗑️
                        </button>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Mappings */}
              {mappings.length === 0 ? (
                <div className="response-config-builder__empty-section">
                  <p>Nessun campo selezionato. Vai alla tab "Struttura" per selezionare i campi.</p>
                </div>
              ) : (
                <div className="response-config-builder__mappings-list">
                  <div className="response-config-builder__mapping-header">
                    <span>Path sorgente</span>
                    <span></span>
                    <span>Nome colonna</span>
                    <span>Tipo</span>
                    <span></span>
                  </div>
                  {mappings.map((m, idx) => (
                    <div key={idx} className="response-config-builder__mapping-item">
                      <code className="response-config-builder__mapping-path">{m.path}</code>
                      <span className="response-config-builder__mapping-arrow">→</span>
                      <input 
                        type="text"
                        value={m.column}
                        onChange={e => updateMappingColumn(idx, e.target.value)}
                        className="response-config-builder__input"
                      />
                      <span className={`response-config-builder__type-badge response-config-builder__type-badge--${m.type}`}>
                        {m.type}
                      </span>
                      <button type="button" 
                        onClick={() => toggleMapping(m.path, m.column, m.type)}
                        className="response-config-builder__btn response-config-builder__btn--danger response-config-builder__btn--icon"
                      >
                        🗑️
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* Tab: Trasformazioni */}
          {activeTab === 'transforms' && (
            <div className="response-config-builder__panel">
              <div className="response-config-builder__section-header">
                <h4>⚙️ Trasformazioni</h4>
                <p>Crea nuove colonne con valori calcolati</p>
              </div>

              {/* Lista trasformazioni */}
              {transforms.length === 0 ? (
                <div className="response-config-builder__empty-section">
                  <p>Nessuna trasformazione definita.</p>
                </div>
              ) : (
                <div className="response-config-builder__transforms-list">
                  {transforms.map((t, idx) => {
                    const op = TRANSFORM_OPERATIONS.find(o => o.id === t.operation);
                    return (
                      <div key={idx} className="response-config-builder__transform-item">
                        <div className="response-config-builder__transform-row">
                          <label>Colonna output:</label>
                          <input 
                            type="text"
                            value={t.column}
                            onChange={e => updateTransform(idx, 'column', e.target.value)}
                            className="response-config-builder__input"
                          />
                        </div>
                        <div className="response-config-builder__transform-row">
                          <label>Colonna sorgente:</label>
                          <select 
                            value={t.source}
                            onChange={e => updateTransform(idx, 'source', e.target.value)}
                            className="response-config-builder__select"
                          >
                            <option value="">-- seleziona --</option>
                            {mappings.map(m => (
                              <option key={m.column} value={m.column}>{m.column}</option>
                            ))}
                          </select>
                        </div>
                        <div className="response-config-builder__transform-row">
                          <label>Operazione:</label>
                          <select 
                            value={t.operation}
                            onChange={e => updateTransform(idx, 'operation', e.target.value)}
                            className="response-config-builder__select"
                          >
                            {TRANSFORM_OPERATIONS.map(o => (
                              <option key={o.id} value={o.id}>{o.icon} {o.label}</option>
                            ))}
                          </select>
                        </div>
                        {op?.hasValue && (
                          <div className="response-config-builder__transform-row">
                            <label>{op.valueLabel}:</label>
                            <input 
                              type="text"
                              value={t.value || ''}
                              onChange={e => updateTransform(idx, 'value', e.target.value)}
                              className="response-config-builder__input response-config-builder__input--small"
                            />
                          </div>
                        )}
                        <button type="button" 
                          onClick={() => removeTransform(idx)}
                          className="response-config-builder__btn response-config-builder__btn--danger"
                        >
                          🗑️ Rimuovi
                        </button>
                      </div>
                    );
                  })}
                </div>
              )}

              {/* Aggiungi trasformazione */}
              {mappings.length > 0 && (
                <div className="response-config-builder__add-transform">
                  <select 
                    id="add-transform-source"
                    className="response-config-builder__select"
                    defaultValue=""
                  >
                    <option value="">-- Scegli colonna sorgente --</option>
                    {mappings.map(m => (
                      <option key={m.column} value={m.column}>{m.column}</option>
                    ))}
                  </select>
                  <button type="button" 
                    onClick={() => {
                      const select = document.getElementById('add-transform-source');
                      if (select.value) {
                        addTransform(select.value);
                        select.value = '';
                      }
                    }}
                    className="response-config-builder__btn response-config-builder__btn--primary"
                  >
                    + Aggiungi trasformazione
                  </button>
                </div>
              )}
            </div>
          )}

          {/* Tab: Preview */}
          {activeTab === 'preview' && (
            <div className="response-config-builder__panel">
              <div className="response-config-builder__section-header">
                <h4>👁️ Preview risultato</h4>
                <button type="button" 
                  onClick={generatePreview}
                  className="response-config-builder__btn response-config-builder__btn--primary"
                >
                  🔄 Aggiorna preview
                </button>
              </div>

              {previewError && (
                <div className="response-config-builder__error">
                  ❌ {previewError}
                </div>
              )}

              {previewData && previewData.length > 0 ? (
                <div className="response-config-builder__preview-table-wrapper">
                  <table className="response-config-builder__preview-table">
                    <thead>
                      <tr>
                        {Object.keys(previewData[0]).map(col => (
                          <th key={col}>{col}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {previewData.map((row, idx) => (
                        <tr key={idx}>
                          {Object.values(row).map((val, vIdx) => (
                            <td key={vIdx}>
                              {val === null ? <span className="response-config-builder__null">null</span> : String(val).substring(0, 50)}
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {previewData.length >= 10 && (
                    <p className="response-config-builder__preview-note">
                      Mostrando le prime 10 righe...
                    </p>
                  )}
                </div>
              ) : (
                <div className="response-config-builder__empty-section">
                  <p>Nessun dato da mostrare. Configura i mappings e clicca "Aggiorna preview".</p>
                </div>
              )}

              {/* Config JSON */}
              <div className="response-config-builder__config-preview">
                <h5>📝 Configurazione generata:</h5>
                <pre className="response-config-builder__config-json">
                  {JSON.stringify(generatedConfig, null, 2)}
                </pre>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}

// ============================================================================
// Componente TreeNode per visualizzare la struttura JSON
// ============================================================================

function JsonTreeNode({ 
  node, 
  path, 
  depth, 
  expandedPaths, 
  iterateOn, 
  mappings,
  onToggleExpand,
  onToggleIterate,
  onToggleMapping
}) {
  const isExpanded = expandedPaths.has(path);
  const isArray = node.type === 'array';
  const isObject = node.type === 'object';
  const isExpandable = isArray || isObject;
  
  const isIterated = iterateOn.some(i => i.path === (path || ''));
  const isSelected = mappings.some(m => m.path === node.fullPath);

  const indent = depth * 20;

  return (
    <div className="json-tree-node">
      <div 
        className={`json-tree-node__row ${isIterated ? 'json-tree-node__row--iterated' : ''} ${isSelected ? 'json-tree-node__row--selected' : ''}`}
        style={{ paddingLeft: `${indent}px` }}
      >
        {/* Expand/Collapse */}
        {isExpandable ? (
          <button type="button" 
            className="json-tree-node__toggle"
            onClick={() => onToggleExpand(path)}
          >
            {isExpanded ? '▼' : '▶'}
          </button>
        ) : (
          <span className="json-tree-node__toggle json-tree-node__toggle--empty"></span>
        )}

        {/* Nome campo */}
        <span className="json-tree-node__name">{node.name || '(root)'}</span>

        {/* Badge tipo */}
        <span className={`json-tree-node__badge json-tree-node__badge--${node.type}`}>
          {node.type}
          {isArray && node.count !== undefined && ` [${node.count}]`}
        </span>

        {/* Azioni */}
        <div className="json-tree-node__actions">
          {/* Toggle iterate (solo per array) */}
          {isArray && (
            <button type="button" 
              className={`json-tree-node__action ${isIterated ? 'json-tree-node__action--active' : ''}`}
              onClick={() => onToggleIterate(path || '', node.name)}
              title={isIterated ? 'Non esplodere questo array' : 'Esplodi questo array'}
            >
              {isIterated ? '🔄 Esplodi' : '💫 Esplodi'}
            </button>
          )}

          {/* Toggle mapping (solo per scalari) */}
          {!isExpandable && (
            <label className="json-tree-node__checkbox">
              <input 
                type="checkbox"
                checked={isSelected}
                onChange={() => onToggleMapping(node.fullPath, node.name, node.type)}
              />
              <span>Estrai</span>
            </label>
          )}
        </div>

        {/* Sample value per scalari */}
        {!isExpandable && node.sample !== undefined && (
          <span className="json-tree-node__sample">
            Es: {formatSampleValue(node.sample)}
          </span>
        )}
      </div>

      {/* Children */}
      {isExpanded && node.children && (
        <div className="json-tree-node__children">
          {node.children.map((child, idx) => (
            <JsonTreeNode 
              key={child.name || idx}
              node={child}
              path={path ? `${path}.${child.name}` : child.name}
              depth={depth + 1}
              expandedPaths={expandedPaths}
              iterateOn={iterateOn}
              mappings={mappings}
              onToggleExpand={onToggleExpand}
              onToggleIterate={onToggleIterate}
              onToggleMapping={onToggleMapping}
            />
          ))}
        </div>
      )}
    </div>
  );
}

// ============================================================================
// Utility Functions
// ============================================================================

/**
 * Analizza la struttura di un oggetto JSON
 */
function analyzeJsonStructure(data, name, depth, parentPath = '') {
  const fullPath = parentPath ? `${parentPath}.${name}` : name;
  
  if (data === null || data === undefined) {
    return { name, type: 'null', fullPath, sample: null };
  }
  
  if (Array.isArray(data)) {
    const sample = data[0];
    
    // Se l'array contiene oggetti, mostra i campi del primo elemento (es. features[])
    if (sample && typeof sample === 'object' && !Array.isArray(sample)) {
      return {
        name,
        type: 'array',
        fullPath,
        count: data.length,
        children: Object.entries(sample).map(([key, value]) => 
          analyzeJsonStructure(value, key, depth + 1, `${fullPath}[*]`)
        )
      };
    }
    
    // Array di primitivi (es. latlng: [42.83, 12.83]) — mostra tutti gli indici
    // Creiamo i nodi direttamente per evitare il punto spurio prima di [idx]
    // (la ricorsione produrrebbe "latlng.[0]" invece di "latlng[0]")
    const children = data.slice(0, 10).map((item, idx) => {
      let t = typeof item;
      if (t === 'number') t = Number.isInteger(item) ? 'integer' : 'number';
      return { name: `[${idx}]`, type: t, fullPath: `${fullPath}[${idx}]`, sample: item };
    });
    
    return { name, type: 'array', fullPath, count: data.length, children };
  }
  
  if (typeof data === 'object') {
    return {
      name,
      type: 'object',
      fullPath,
      children: Object.entries(data).map(([key, value]) => 
        analyzeJsonStructure(value, key, depth + 1, fullPath)
      )
    };
  }
  
  // Scalare
  let type = typeof data;
  if (type === 'number') {
    type = Number.isInteger(data) ? 'integer' : 'number';
  }
  
  return { name, type, fullPath, sample: data };
}

/**
 * Raccoglie tutti i path per espandere
 */
function collectPaths(node, paths, depth, maxDepth) {
  if (depth >= maxDepth) return;
  
  if (node.children) {
    node.children.forEach(child => {
      const childPath = node.fullPath ? `${node.fullPath}.${child.name}` : child.name;
      paths.add(childPath);
      collectPaths(child, paths, depth + 1, maxDepth);
    });
  }
}

/**
 * Raccoglie campi scalari per auto-detect
 */
function collectScalarFields(node, contextPath, mappings, iterateOn) {
  // Costruisci il path considerando gli alias iterate_on
  let fullPath = contextPath ? `${contextPath}.${node.name}` : node.name;
  
  // Se è un iterato, usa l'alias
  const iterateMatch = iterateOn.find(i => i.path === node.fullPath);
  if (iterateMatch) {
    fullPath = iterateMatch.as;
  }
  
  if (node.type !== 'array' && node.type !== 'object') {
    // È uno scalare
    mappings.push({
      column: node.name,
      path: fullPath || node.name,
      type: node.type
    });
  }
  
  if (node.children) {
    node.children.forEach(child => {
      collectScalarFields(child, fullPath, mappings, iterateOn);
    });
  }
}

/**
 * Estrae righe dalla configurazione (simulazione locale)
 */
function extractRowsFromConfig(data, config) {
  const { root_path, iterate_on, mappings, transforms } = config;
  
  // Naviga al root path
  let rootData = data;
  if (root_path) {
    for (const part of root_path.split('.')) {
      if (rootData && typeof rootData === 'object') {
        rootData = rootData[part];
      }
    }
  }
  
  if (!rootData) return [];
  
  // Se non ci sono iterate, estrai direttamente
  if (!iterate_on || iterate_on.length === 0) {
    const items = Array.isArray(rootData) ? rootData : [rootData];
    return items.slice(0, 100).map(item => extractRow(item, mappings, {}));
  }
  
  // Itera ricorsivamente
  const rows = [];
  recursiveIterate(rootData, iterate_on, 0, {}, rows, mappings);
  
  // Applica trasformazioni
  applyTransforms(rows, transforms);
  
  return rows;
}

function recursiveIterate(data, iterateOn, level, context, rows, mappings) {
  if (level >= iterateOn.length) {
    rows.push(extractRow(null, mappings, context));
    return;
  }
  
  const { path, as } = iterateOn[level];
  let items = path ? navigatePath(data, path) : data;
  
  if (!Array.isArray(items)) {
    items = items ? [items] : [];
  }
  
  for (const item of items) {
    const newContext = { ...context, [as]: item };
    recursiveIterate(item, iterateOn, level + 1, newContext, rows, mappings);
  }
}

function extractRow(_, mappings, context) {
  const row = {};
  
  for (const mapping of mappings) {
    const { column, path } = mapping;
    
    // Prova a estrarre dal context
    const parts = path.split('.');
    let value = context[parts[0]];
    
    for (let i = 1; i < parts.length && value !== undefined; i++) {
      value = value?.[parts[i]];
    }
    
    row[column] = value ?? null;
  }
  
  return row;
}

function navigatePath(data, path) {
  if (!path) return data;
  
  let current = data;
  for (const part of path.split('.')) {
    if (current === null || current === undefined) return null;
    current = current[part];
  }
  return current;
}

function applyTransforms(rows, transforms) {
  if (!transforms || transforms.length === 0) return;
  
  for (const row of rows) {
    for (const t of transforms) {
      if (t.operation === 'none') continue;
      
      const sourceValue = row[t.source];
      if (sourceValue === null || sourceValue === undefined) {
        row[t.column] = null;
        continue;
      }
      
      switch (t.operation) {
        case 'kelvin_to_celsius':
          row[t.column] = Math.round((parseFloat(sourceValue) - 273.15) * 100) / 100;
          break;
        case 'celsius_to_kelvin':
          row[t.column] = parseFloat(sourceValue) + 273.15;
          break;
        case 'round':
          row[t.column] = Math.round(parseFloat(sourceValue) * Math.pow(10, parseInt(t.value) || 0)) / Math.pow(10, parseInt(t.value) || 0);
          break;
        case 'multiply':
          row[t.column] = parseFloat(sourceValue) * parseFloat(t.value);
          break;
        case 'divide':
          row[t.column] = parseFloat(sourceValue) / parseFloat(t.value);
          break;
        case 'add':
          row[t.column] = parseFloat(sourceValue) + parseFloat(t.value);
          break;
        case 'subtract':
          row[t.column] = parseFloat(sourceValue) - parseFloat(t.value);
          break;
        case 'uppercase':
          row[t.column] = String(sourceValue).toUpperCase();
          break;
        case 'lowercase':
          row[t.column] = String(sourceValue).toLowerCase();
          break;
        case 'prefix':
          row[t.column] = `${t.value}${sourceValue}`;
          break;
        case 'suffix':
          row[t.column] = `${sourceValue}${t.value}`;
          break;
        default:
          row[t.column] = sourceValue;
      }
    }
  }
}

function formatSampleValue(value) {
  if (value === null) return 'null';
  if (typeof value === 'string') {
    return value.length > 30 ? `"${value.substring(0, 30)}..."` : `"${value}"`;
  }
  return String(value);
}

export default ResponseConfigBuilder;
