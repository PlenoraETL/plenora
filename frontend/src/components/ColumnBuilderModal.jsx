import React, { useState, useEffect, useMemo } from 'react';
import { getColumnTypeIcon, getColumnTypeLabel } from '../utils/columnUtils';
import '../styles/ColumnBuilderModal.css';

/**
 * ColumnBuilderModal - Modal per costruire espressioni di colonne calcolate
 * 
 * Props:
 * - isOpen: boolean - se il modal è visibile
 * - onClose: () => void - chiude il modal
 * - onSave: (columnDef) => void - salva la colonna calcolata
 * - columns: array - colonne disponibili dalla tabella
 * - dbType: string - tipo database per preview SQL (postgres, mssql, etc.)
 * - existingColumn: object - se editing, la colonna esistente
 */
function ColumnBuilderModal({ 
  isOpen, 
  onClose, 
  onSave, 
  columns = [], 
  dbType = 'postgres',
  existingColumn = null 
}) {
  // State
  const [catalog, setCatalog] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  
  const [columnName, setColumnName] = useState('');
  const [selectedCategory, setSelectedCategory] = useState('geometric');
  const [selectedFunction, setSelectedFunction] = useState('');
  const [params, setParams] = useState({});
  const [nestedExpression, setNestedExpression] = useState(null);
  const [useNested, setUseNested] = useState(false);
  
  const [sqlPreview, setSqlPreview] = useState('');
  const [isManualEdit, setIsManualEdit] = useState(false);
  const [manualSql, setManualSql] = useState('');

  // Carica catalogo funzioni
  useEffect(() => {
    if (isOpen) {
      loadCatalog();
    }
  }, [isOpen]);

  // Reset quando si apre
  useEffect(() => {
    if (isOpen) {
      if (existingColumn) {
        // Editing esistente
        setColumnName(existingColumn.name || '');
        // TODO: parse existingColumn.expression per popolare il builder
      } else {
        // Nuova colonna
        setColumnName('');
        setSelectedCategory('geometric');
        setSelectedFunction('');
        setParams({});
        setNestedExpression(null);
        setUseNested(false);
        setIsManualEdit(false);
        setManualSql('');
        setSqlPreview('');
      }
    }
  }, [isOpen, existingColumn]);

  // Aggiorna preview SQL quando cambia l'espressione
  useEffect(() => {
    if (!isManualEdit && selectedFunction && columnName) {
      updateSqlPreview();
    }
  }, [selectedFunction, params, nestedExpression, columnName, dbType, isManualEdit]);

  const loadCatalog = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch('http://localhost:8000/api/functions/catalog');
      if (!res.ok) throw new Error('Errore caricamento catalogo');
      const data = await res.json();
      setCatalog(data);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  const updateSqlPreview = async () => {
    if (!selectedFunction || !columnName) {
      setSqlPreview('');
      return;
    }

    const expression = buildExpression();
    
    try {
      const res = await fetch('http://localhost:8000/api/functions/translate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          db_type: dbType,
          column_name: columnName,
          expression: expression
        })
      });
      
      if (res.ok) {
        const data = await res.json();
        setSqlPreview(data.sql);
      }
    } catch (err) {
      console.error('Errore preview SQL:', err);
    }
  };

  const buildExpression = () => {
    const funcDef = catalog?.functions?.[selectedFunction];
    if (!funcDef) return null;

    const exprParams = {};
    
    funcDef.params.forEach(param => {
      if (param.name === 'column' && useNested && nestedExpression) {
        // Usa espressione annidata
        exprParams[param.name] = nestedExpression;
      } else {
        exprParams[param.name] = params[param.name] ?? param.default;
      }
    });

    return {
      function: selectedFunction,
      params: exprParams
    };
  };

  // Funzioni disponibili per categoria selezionata
  const availableFunctions = useMemo(() => {
    if (!catalog?.by_category) return [];
    return catalog.by_category[selectedCategory] || [];
  }, [catalog, selectedCategory]);

  // Definizione funzione selezionata
  const currentFunctionDef = useMemo(() => {
    if (!catalog?.functions || !selectedFunction) return null;
    return catalog.functions[selectedFunction];
  }, [catalog, selectedFunction]);

  // Colonne filtrate per tipo (per parametri colonna)
  const getColumnsForParam = (param) => {
    if (!param || param.type !== 'column') return columns;
    
    const funcDef = currentFunctionDef;
    if (!funcDef?.input_type) return columns;

    // Filtra per tipo compatibile
    const inputType = funcDef.input_type;
    
    if (inputType.startsWith('geometry')) {
      return columns.filter(c => {
        const colType = c.type || c.dtype || '';
        return colType.startsWith('geometry');
      });
    }
    
    if (inputType === 'real' || inputType === 'int') {
      return columns.filter(c => {
        const colType = c.type || c.dtype || '';
        return ['real', 'int', 'float', 'integer', 'numeric', 'number'].includes(colType);
      });
    }
    
    if (inputType === 'str') {
      return columns.filter(c => {
        const colType = c.type || c.dtype || '';
        return ['str', 'string', 'text', 'varchar'].includes(colType);
      });
    }
    
    return columns;
  };

  // Handlers
  const handleCategoryChange = (cat) => {
    setSelectedCategory(cat);
    setSelectedFunction('');
    setParams({});
  };

  const handleFunctionChange = (funcName) => {
    setSelectedFunction(funcName);
    setParams({});
    setUseNested(false);
    setNestedExpression(null);
  };

  const handleParamChange = (paramName, value) => {
    setParams(prev => ({ ...prev, [paramName]: value }));
  };

  const handleNestedFunctionChange = (funcName) => {
    if (!funcName) {
      setNestedExpression(null);
      return;
    }
    
    const nestedFunc = catalog?.functions?.[funcName];
    if (nestedFunc) {
      // Crea espressione nested di default
      const nestedParams = {};
      nestedFunc.params.forEach(p => {
        if (p.name === 'column' && columns.length > 0) {
          // Trova prima colonna geometrica compatibile
          const geomCols = columns.filter(c => (c.type || '').startsWith('geometry'));
          nestedParams[p.name] = geomCols[0]?.name || columns[0]?.name;
        } else {
          nestedParams[p.name] = p.default;
        }
      });
      
      setNestedExpression({
        function: funcName,
        params: nestedParams
      });
    }
  };

  const handleNestedParamChange = (paramName, value) => {
    setNestedExpression(prev => ({
      ...prev,
      params: { ...prev.params, [paramName]: value }
    }));
  };

  const handleManualEditToggle = () => {
    if (!isManualEdit) {
      setManualSql(sqlPreview);
    }
    setIsManualEdit(!isManualEdit);
  };

  const handleSave = () => {
    if (!columnName.trim()) {
      alert('Inserisci un nome per la colonna');
      return;
    }

    const expression = buildExpression();
    if (!expression && !isManualEdit) {
      alert('Configura l\'espressione');
      return;
    }

    // Determina output_type con SRID
    let outputType = currentFunctionDef?.output_type || 'str';
    let determinedSrid = null;
    
    // Se è una geometria, cerca di determinare l'SRID
    if (outputType.startsWith('geometry')) {
      
      // CASO 1: La funzione corrente è TRANSFORM - usa direttamente il suo SRID
      if (selectedFunction === 'TRANSFORM' && params.srid) {
        determinedSrid = params.srid;
      }
      // CASO 2: C'è un'espressione annidata che è TRANSFORM
      else if (useNested && nestedExpression?.function === 'TRANSFORM' && nestedExpression?.params?.srid) {
        determinedSrid = nestedExpression.params.srid;
      }
      // CASO 3: Eredita SRID dalla colonna sorgente
      else {
        // Cerca la colonna geometry usata come input
        const geomParamName = currentFunctionDef?.params?.find(p => p.type === 'column')?.name;
        let inputColumnName = params[geomParamName];
        
        // Se il parametro colonna è un'espressione annidata, cerca nell'espressione annidata
        if (!inputColumnName && useNested && nestedExpression?.params) {
          const nestedGeomParam = catalog?.functions?.[nestedExpression.function]?.params?.find(p => p.type === 'column')?.name;
          inputColumnName = nestedExpression.params[nestedGeomParam];
        }
        
        if (inputColumnName) {
          const inputColumn = columns.find(c => c.name === inputColumnName);
          if (inputColumn && inputColumn.type) {
            // Estrai SRID dalla colonna sorgente (formato: geometry:subtype:srid)
            const sourceParts = inputColumn.type.split(':');
            if (sourceParts.length >= 3) {
              determinedSrid = sourceParts[2];
            }
          }
        }
      }
      
      // Aggiungi SRID all'output_type se determinato
      if (determinedSrid) {
        const outputParts = outputType.split(':');
        if (outputParts.length < 3) {
          outputType = `${outputType}:${determinedSrid}`;
        }
      }
    }

    const columnDef = {
      name: columnName.trim(),
      expression: expression,
      sql: isManualEdit ? manualSql : sqlPreview,
      output_type: outputType,
      is_manual: isManualEdit
    };

    onSave(columnDef);
    onClose();
  };

  // Render
  if (!isOpen) return null;

  return (
    <div className="column-builder-overlay" onClick={onClose}>
      <div className="column-builder-modal" onClick={e => e.stopPropagation()}>
        {/* Header */}
        <div className="column-builder-header">
          <h3>✨ {existingColumn ? 'Modifica' : 'Nuova'} colonna calcolata</h3>
          <button className="column-builder-close" onClick={onClose}>×</button>
        </div>

        {/* Body */}
        <div className="column-builder-body">
          {loading ? (
            <div className="column-builder-loading">Caricamento funzioni...</div>
          ) : error ? (
            <div className="column-builder-error">❌ {error}</div>
          ) : (
            <>
              {/* Nome colonna */}
              <div className="column-builder-section">
                <label className="column-builder-label">Nome colonna risultato:</label>
                <input
                  type="text"
                  value={columnName}
                  onChange={e => setColumnName(e.target.value)}
                  placeholder="es: buffer_100m"
                  className="column-builder-input"
                />
              </div>

              <div className="column-builder-divider" />

              {/* Builder */}
              <div className="column-builder-section">
                <div className="column-builder-section-title">🔧 BUILDER</div>

                {/* Categoria */}
                <div className="column-builder-field">
                  <label className="column-builder-label">Categoria:</label>
                  <div className="column-builder-categories">
                    {catalog?.categories && Object.entries(catalog.categories).map(([key, cat]) => (
                      <button
                        key={key}
                        className={`column-builder-category-btn ${selectedCategory === key ? 'active' : ''}`}
                        style={{ 
                          '--cat-color': cat.color,
                          borderColor: selectedCategory === key ? cat.color : 'transparent'
                        }}
                        onClick={() => handleCategoryChange(key)}
                      >
                        {cat.label}
                      </button>
                    ))}
                  </div>
                </div>

                {/* Funzione */}
                <div className="column-builder-field">
                  <label className="column-builder-label">Funzione:</label>
                  <select
                    value={selectedFunction}
                    onChange={e => handleFunctionChange(e.target.value)}
                    className="column-builder-select"
                  >
                    <option value="">-- Seleziona funzione --</option>
                    {availableFunctions.map(func => (
                      <option key={func.name} value={func.name}>
                        {func.label}
                      </option>
                    ))}
                  </select>
                  {currentFunctionDef && (
                    <div className="column-builder-func-desc">
                      {currentFunctionDef.description}
                    </div>
                  )}
                </div>

                {/* Parametri */}
                {currentFunctionDef && currentFunctionDef.params.length > 0 && (
                  <div className="column-builder-params">
                    <label className="column-builder-label">Parametri:</label>
                    <div className="column-builder-params-box">
                      {currentFunctionDef.params.map(param => (
                        <div key={param.name} className="column-builder-param">
                          {param.type === 'column' ? (
                            <>
                              <label>{param.description || param.name}:</label>
                              
                              {/* Toggle colonna/nested - SOLO per categoria geometrica */}
                              {selectedCategory === 'geometric' && (
                                <div className="column-builder-param-toggle">
                                  <label className="column-builder-radio">
                                    <input
                                      type="radio"
                                      checked={!useNested}
                                      onChange={() => {
                                        setUseNested(false);
                                        setNestedExpression(null);
                                      }}
                                    />
                                    Colonna
                                  </label>
                                  <label className="column-builder-radio">
                                    <input
                                      type="radio"
                                      checked={useNested}
                                      onChange={() => setUseNested(true)}
                                    />
                                    Funzione
                                  </label>
                                </div>
                              )}

                              {(!useNested || selectedCategory !== 'geometric') ? (
                                <select
                                  value={params[param.name] || ''}
                                  onChange={e => handleParamChange(param.name, e.target.value)}
                                  className="column-builder-select"
                                >
                                  <option value="">-- Seleziona colonna --</option>
                                  {getColumnsForParam(param).map(col => (
                                    <option key={col.name} value={col.name}>
                                      {getColumnTypeIcon(col.type || col.dtype)} {col.name}
                                    </option>
                                  ))}
                                </select>
                              ) : (
                                <div className="column-builder-nested">
                                  <select
                                    value={nestedExpression?.function || ''}
                                    onChange={e => handleNestedFunctionChange(e.target.value)}
                                    className="column-builder-select"
                                  >
                                    <option value="">-- Funzione --</option>
                                    {availableFunctions
                                      .filter(f => f.output_type?.startsWith('geometry'))
                                      .map(func => (
                                        <option key={func.name} value={func.name}>
                                          {func.label}
                                        </option>
                                      ))}
                                  </select>
                                  
                                  {nestedExpression && catalog?.functions?.[nestedExpression.function] && (
                                    <div className="column-builder-nested-params">
                                      {catalog.functions[nestedExpression.function].params.map(np => (
                                        <div key={np.name} className="column-builder-nested-param">
                                          <label>{np.description || np.name}:</label>
                                          {np.type === 'column' ? (
                                            <select
                                              value={nestedExpression.params?.[np.name] || ''}
                                              onChange={e => handleNestedParamChange(np.name, e.target.value)}
                                              className="column-builder-select column-builder-select--small"
                                            >
                                              <option value="">--</option>
                                              {columns.filter(c => (c.type || '').startsWith('geometry')).map(col => (
                                                <option key={col.name} value={col.name}>
                                                  {col.name}
                                                </option>
                                              ))}
                                            </select>
                                          ) : (
                                            <input
                                              type={np.type === 'number' ? 'number' : 'text'}
                                              value={nestedExpression.params?.[np.name] || ''}
                                              onChange={e => handleNestedParamChange(np.name, e.target.value)}
                                              className="column-builder-input column-builder-input--small"
                                            />
                                          )}
                                        </div>
                                      ))}
                                    </div>
                                  )}
                                </div>
                              )}
                            </>
                          ) : param.type === 'column_list' ? (
                            /* ✅ NUOVO: Supporto per lista colonne (CONCAT) */
                            <>
                              <label>{param.description || param.name}:</label>
                              <div className="column-builder-column-list">
                                {/* Colonne selezionate */}
                                <div className="column-builder-selected-columns">
                                  {(params[param.name] || []).map((colName, idx) => (
                                    <div key={idx} className="column-builder-selected-column">
                                      <span>{colName}</span>
                                      <button
                                        type="button"
                                        onClick={() => {
                                          const newList = [...(params[param.name] || [])];
                                          newList.splice(idx, 1);
                                          handleParamChange(param.name, newList);
                                        }}
                                        className="column-builder-remove-column"
                                      >
                                        ×
                                      </button>
                                    </div>
                                  ))}
                                </div>
                                {/* Dropdown per aggiungere colonna */}
                                <select
                                  value=""
                                  onChange={e => {
                                    if (e.target.value) {
                                      const currentList = params[param.name] || [];
                                      handleParamChange(param.name, [...currentList, e.target.value]);
                                    }
                                  }}
                                  className="column-builder-select"
                                >
                                  <option value="">+ Aggiungi colonna...</option>
                                  {columns
                                    .filter(col => !(params[param.name] || []).includes(col.name))
                                    .map(col => (
                                      <option key={col.name} value={col.name}>
                                        {getColumnTypeIcon(col.type || col.dtype)} {col.name}
                                      </option>
                                    ))}
                                </select>
                              </div>
                            </>
                          ) : param.type === 'number' ? (
                            <>
                              <label>{param.description || param.name}:</label>
                              <input
                                type="number"
                                value={params[param.name] ?? param.default ?? ''}
                                onChange={e => handleParamChange(param.name, parseFloat(e.target.value))}
                                placeholder={param.default?.toString() || ''}
                                className="column-builder-input"
                              />
                            </>
                          ) : param.type === 'srid' ? (
                            <>
                              <label>{param.description || param.name}:</label>
                              <select
                                value={params[param.name] || ''}
                                onChange={e => handleParamChange(param.name, parseInt(e.target.value))}
                                className="column-builder-select"
                              >
                                <option value="">-- SRID --</option>
                                <option value="4326">WGS 84 (4326)</option>
                                <option value="3857">Web Mercator (3857)</option>
                                <option value="32632">UTM 32N (32632)</option>
                                <option value="32633">UTM 33N (32633)</option>
                                <option value="6706">RDN2008 (6706)</option>
                              </select>
                            </>
                          ) : (
                            <>
                              <label>{param.description || param.name}:</label>
                              <input
                                type="text"
                                value={params[param.name] ?? param.default ?? ''}
                                onChange={e => handleParamChange(param.name, e.target.value)}
                                className="column-builder-input"
                                placeholder={param.type === 'string' ? 'es: , oppure -' : ''}
                              />
                            </>
                          )}
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>

              <div className="column-builder-divider" />

              {/* SQL Preview */}
              <div className="column-builder-section">
                <div className="column-builder-preview-header">
                  <span className="column-builder-section-title">📝 SQL PREVIEW</span>
                  <button 
                    className="column-builder-edit-btn"
                    onClick={handleManualEditToggle}
                  >
                    {isManualEdit ? '🔄 Usa Builder' : '✏️ Modifica'}
                  </button>
                </div>

                {isManualEdit ? (
                  <>
                    <textarea
                      value={manualSql}
                      onChange={e => setManualSql(e.target.value)}
                      className="column-builder-sql-edit"
                      rows={3}
                    />
                    <div className="column-builder-warning">
                      ⚠️ Modifica manuale - il builder è disabilitato
                    </div>
                  </>
                ) : (
                  <div className="column-builder-sql-preview">
                    {sqlPreview || <span className="placeholder">Configura l'espressione...</span>}
                  </div>
                )}

                {currentFunctionDef && (
                  <div className="column-builder-result-type">
                    Tipo risultato: {getColumnTypeIcon(currentFunctionDef.output_type)} {getColumnTypeLabel(currentFunctionDef.output_type)}
                  </div>
                )}
              </div>
            </>
          )}
        </div>

        {/* Footer */}
        <div className="column-builder-footer">
          <button className="column-builder-btn column-builder-btn--cancel" onClick={onClose}>
            Annulla
          </button>
          <button 
            className="column-builder-btn column-builder-btn--save" 
            onClick={handleSave}
            disabled={!columnName.trim() || (!sqlPreview && !manualSql)}
          >
            ✓ {existingColumn ? 'Salva modifiche' : 'Aggiungi colonna'}
          </button>
        </div>
      </div>
    </div>
  );
}

export default ColumnBuilderModal;