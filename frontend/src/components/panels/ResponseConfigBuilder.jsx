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
import JsonTreeNode from './response-config/JsonTreeNode';
import MappingsPanel from './response-config/MappingsPanel';
import TransformsPanel from './response-config/TransformsPanel';
import PreviewPanel from './response-config/PreviewPanel';
import {
  analyzeJsonStructure,
  collectPaths,
  collectScalarFields,
  extractRowsFromConfig,
} from './response-config/responseConfigEngine';

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

// `TRANSFORM_OPERATIONS` (lista UI delle 12 operazioni di transform)
// vive co-locata in `response-config/TransformsPanel.jsx`
// Il dispatch effettivo dei case e' in `responseConfigEngine.applyTransforms`.

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
            <MappingsPanel
              iterateOn={iterateOn}
              mappings={mappings}
              updateIterateAlias={updateIterateAlias}
              toggleIterate={toggleIterate}
              updateMappingColumn={updateMappingColumn}
              toggleMapping={toggleMapping}
            />
          )}

          {/* Tab: Trasformazioni */}
          {activeTab === 'transforms' && (
            <TransformsPanel
              transforms={transforms}
              mappings={mappings}
              updateTransform={updateTransform}
              removeTransform={removeTransform}
              addTransform={addTransform}
            />
          )}

          {/* Tab: Preview */}
          {activeTab === 'preview' && (
            <PreviewPanel
              previewData={previewData}
              previewError={previewError}
              generatedConfig={generatedConfig}
              generatePreview={generatePreview}
            />
          )}
        </>
      )}
    </div>
  );
}

export default ResponseConfigBuilder;
