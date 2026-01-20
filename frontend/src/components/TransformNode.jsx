import React, { memo, useRef, useEffect, useState } from 'react';
import { Handle, Position, useReactFlow } from 'reactflow';
import { getColumnTypeIcon } from '../utils/columnUtils';
import '../styles/TransformNode.css';

/**
 * TransformNode - Nodo per trasformazioni dati (MANIPOLA)
 * Supporta: Join, Formula, Filter, Aggregate, Concat, Sort, Distinct + operazioni spaziali
 */
const TransformNode = memo(({ data, id }) => {
  const { getEdges, getNodes } = useReactFlow();
  const formulaRef = useRef(null);
  
  const transform_op = data.transform_op || 'join';
  const config = data.config || {};

  const operations = [
    // Base
    { value: 'join', label: 'Join (Merge)', color: '#2196F3', category: 'base' },
    { value: 'formula', label: 'Formula (Calcolo)', color: '#4CAF50', category: 'base' },
    { value: 'aggregate', label: 'Aggregazione (GroupBy)', color: '#FF9800', category: 'base' },
    { value: 'filter', label: 'Filter (Filtra)', color: '#9C27B0', category: 'base' },
    { value: 'concat', label: 'Concat (Unisci)', color: '#F44336', category: 'base' },
    { value: 'sort', label: 'Sort (Ordina)', color: '#607D8B', category: 'base' },
    { value: 'distinct', label: 'Distinct (Univoci)', color: '#795548', category: 'base' },
    // Nuove Base
    { value: 'rename', label: 'Rinomina Colonne', color: '#00BCD4', category: 'base' },
    { value: 'drop_columns', label: 'Elimina Colonne', color: '#FF5722', category: 'base' },
    { value: 'fill_na', label: 'Riempi Nulli', color: '#8BC34A', category: 'base' },
    { value: 'replace', label: 'Sostituisci Valori', color: '#673AB7', category: 'base' },
    { value: 'split_column', label: 'Dividi Colonna', color: '#009688', category: 'base' },
    // Spaziali
    { value: 'sjoin', label: 'Spatial Join', color: '#E91E63', category: 'spatial' },
    { value: 'geo_buffer', label: 'Buffer', color: '#E91E63', category: 'spatial' },
    { value: 'geo_centroid', label: 'Centroide', color: '#E91E63', category: 'spatial' },
    { value: 'geo_area', label: 'Area', color: '#E91E63', category: 'spatial' },
    { value: 'geo_clip', label: 'Clip', color: '#E91E63', category: 'spatial' },
    // Nuove Spaziali
    { value: 'geo_dissolve', label: 'Dissolve', color: '#E91E63', category: 'spatial' },
    { value: 'geo_intersection', label: 'Intersezione', color: '#E91E63', category: 'spatial' },
    { value: 'geo_union', label: 'Unione Geom.', color: '#E91E63', category: 'spatial' },
    { value: 'geo_difference', label: 'Differenza', color: '#E91E63', category: 'spatial' },
    { value: 'geo_simplify', label: 'Semplifica', color: '#E91E63', category: 'spatial' },
    { value: 'geo_convex_hull', label: 'Convex Hull', color: '#E91E63', category: 'spatial' },
    { value: 'geo_reproject', label: 'Riproietta', color: '#E91E63', category: 'spatial' }
  ];

  // Determina la categoria iniziale in base all'operazione corrente
  const getCurrentCategory = () => {
    const op = operations.find(o => o.value === transform_op);
    return op?.category || 'base';
  };
  
  const [activeTab, setActiveTab] = useState(getCurrentCategory);
  
  // Aggiorna la tab quando cambia l'operazione dall'esterno
  useEffect(() => {
    setActiveTab(getCurrentCategory());
  }, [transform_op]);

  const currentOp = operations.find(op => op.value === transform_op) || operations[0];
  
  // Filtra operazioni per tab attiva
  const filteredOperations = operations.filter(op => op.category === activeTab);

  // === HANDLERS ===
  const updateConfig = (field, value) => {
    if (data.onChange) {
      data.onChange({
        ...data,
        config: { ...config, [field]: value }
      });
    }
  };

  const onOpChange = (evt) => {
    if (data.onChange) {
      data.onChange({ 
        ...data, 
        transform_op: evt.target.value, 
        config: {} 
      });
    }
  };

  const handleDelete = () => {
    if (data.onDelete && confirm('Eliminare questo nodo?')) {
      data.onDelete(id);
    }
  };

  // === CALCOLO COLONNE E VALIDAZIONE ===
  const incomingEdges = getEdges()
    .filter(e => e.target === id)
    .sort((a, b) => a.id.localeCompare(b.id));

  const nodes = getNodes();

  const getNodeLabel = (node) => {
    if (!node) return 'Unknown';
    if (node.type === 'project_source') {
      const projectId = node.data?.source_project_id;
      return projectId ? `Progetto ${projectId.substring(0, 8)}...` : 'Progetto non selezionato';
    }
    if (node.type === 'transform') {
      return `Transform (${node.data?.transform_op || 'unknown'})`;
    }
    return node.data?.label || node.id;
  };

  // === VALIDAZIONE JOIN ===
  let joinValidation = { valid: true, message: '' };
  let leftNode = null;
  let rightNode = null;
  let leftColumns = [];
  let rightColumns = [];

  if (transform_op === 'join') {
    if (incomingEdges.length < 2) {
      joinValidation = {
        valid: false,
        type: 'warning',
        message: 'Collega 2 sorgenti dati per configurare il join'
      };
    } else if (incomingEdges.length > 2) {
      joinValidation = {
        valid: false,
        type: 'error',
        message: `Join supporta solo 2 input! Hai ${incomingEdges.length} connessioni.`
      };
    } else {
      leftNode = nodes.find(n => n.id === incomingEdges[0].source);
      rightNode = nodes.find(n => n.id === incomingEdges[1].source);

      if (leftNode?.data?.columns) {
        leftColumns = leftNode.data.columns.filter(c => c.visible);
      }
      if (rightNode?.data?.columns) {
        rightColumns = rightNode.data.columns.filter(c => c.visible);
      }

      if (leftColumns.length === 0 || rightColumns.length === 0) {
        joinValidation = {
          valid: false,
          type: 'warning',
          message: 'Uno o più input non hanno colonne disponibili.'
        };
      }
    }
  }

  // === VALIDAZIONE FORMULA/FILTER ===
  let formulaValidation = { valid: true, message: '' };
  let inputNode = null;
  let inputColumns = [];

  if (transform_op === 'formula' || transform_op === 'filter') {
    if (incomingEdges.length === 0) {
      formulaValidation = {
        valid: false,
        type: 'warning',
        message: `Collega 1 sorgente dati per configurare ${transform_op === 'formula' ? 'la formula' : 'il filtro'}`
      };
    } else if (incomingEdges.length > 1) {
      formulaValidation = {
        valid: false,
        type: 'error',
        message: `${transform_op === 'formula' ? 'Formula' : 'Filter'} supporta solo 1 input!`
      };
    } else {
      inputNode = nodes.find(n => n.id === incomingEdges[0].source);

      if (inputNode?.data?.columns && inputNode.data.columns.length > 0) {
        inputColumns = inputNode.data.columns.filter(c => c.visible !== false);
      }

      if (inputColumns.length === 0) {
        formulaValidation = {
          valid: false,
          type: 'warning',
          message: 'Input non ha colonne disponibili.'
        };
      }
    }
  }

  // === VALIDAZIONE SORT/DISTINCT (singolo input) ===
  let singleInputValidation = { valid: true, message: '' };
  let singleInputColumns = [];

  if (transform_op === 'sort' || transform_op === 'distinct') {
    if (incomingEdges.length === 0) {
      singleInputValidation = {
        valid: false,
        type: 'warning',
        message: `Collega 1 sorgente dati per configurare ${transform_op === 'sort' ? "l'ordinamento" : 'la rimozione duplicati'}`
      };
    } else if (incomingEdges.length > 1) {
      singleInputValidation = {
        valid: false,
        type: 'error',
        message: `${transform_op === 'sort' ? 'Sort' : 'Distinct'} supporta solo 1 input!`
      };
    } else {
      const singleInputNode = nodes.find(n => n.id === incomingEdges[0].source);
      
      if (singleInputNode?.data?.columns && singleInputNode.data.columns.length > 0) {
        singleInputColumns = singleInputNode.data.columns.filter(c => c.visible !== false);
      }
      
      if (singleInputColumns.length === 0) {
        singleInputValidation = {
          valid: false,
          type: 'warning',
          message: 'Input non ha colonne disponibili.'
        };
      }
    }
  }

  // === VALIDAZIONE OPERAZIONI SPAZIALI SINGOLO INPUT ===
  let spatialValidation = { valid: true, message: '' };
  let spatialInputColumns = [];
  let geometryColumns = [];

  const singleInputSpatialOps = ['geo_buffer', 'geo_centroid', 'geo_area'];
  
  if (singleInputSpatialOps.includes(transform_op)) {
    if (incomingEdges.length === 0) {
      spatialValidation = {
        valid: false,
        type: 'warning',
        message: 'Collega una sorgente con colonna geometria'
      };
    } else {
      const spatialInputNode = nodes.find(n => n.id === incomingEdges[0].source);
      
      if (spatialInputNode?.data?.columns) {
        spatialInputColumns = spatialInputNode.data.columns.filter(c => c.visible !== false);
        // Filtra solo colonne geometriche
        geometryColumns = spatialInputColumns.filter(c => {
          const colType = (c.type || c.dtype || '').toLowerCase();
          return colType.startsWith('geometry');
        });
      }
      
      if (geometryColumns.length === 0) {
        spatialValidation = {
          valid: false,
          type: 'warning',
          message: 'Nessuna colonna geometrica trovata nell\'input'
        };
      }
    }
  }

  // === VALIDAZIONE SJOIN / CLIP / INTERSECTION / UNION / DIFFERENCE (2 input) ===
  let sjoinValidation = { valid: true, message: '' };
  let leftGeomColumns = [];
  let rightGeomColumns = [];

  const twoInputSpatialOps = ['sjoin', 'geo_clip', 'geo_intersection', 'geo_union', 'geo_difference'];
  
  if (twoInputSpatialOps.includes(transform_op)) {
    const opLabels = {
      'sjoin': 'Spatial Join',
      'geo_clip': 'Clip',
      'geo_intersection': 'Intersezione',
      'geo_union': 'Unione Geometrica',
      'geo_difference': 'Differenza'
    };
    
    if (incomingEdges.length < 2) {
      sjoinValidation = {
        valid: false,
        type: 'warning',
        message: `${opLabels[transform_op] || transform_op} richiede 2 input con geometrie`
      };
    } else {
      const leftSpatialNode = nodes.find(n => n.id === incomingEdges[0].source);
      const rightSpatialNode = nodes.find(n => n.id === incomingEdges[1].source);
      
      if (leftSpatialNode?.data?.columns) {
        leftGeomColumns = leftSpatialNode.data.columns.filter(c => {
          const colType = (c.type || c.dtype || '').toLowerCase();
          return colType.startsWith('geometry') && c.visible !== false;
        });
      }
      
      if (rightSpatialNode?.data?.columns) {
        rightGeomColumns = rightSpatialNode.data.columns.filter(c => {
          const colType = (c.type || c.dtype || '').toLowerCase();
          return colType.startsWith('geometry') && c.visible !== false;
        });
      }
      
      if (leftGeomColumns.length === 0 || rightGeomColumns.length === 0) {
        sjoinValidation = {
          valid: false,
          type: 'warning',
          message: 'Entrambi gli input devono avere colonne geometriche'
        };
      }
    }
  }

  // === CALCOLO COLONNE OUTPUT (con propagazione tipi) ===
  const calculateOutputColumns = () => {
    let outputColumns = [];

    // Helper per normalizzare il tipo (supporta sia 'type' che 'dtype')
    const getColType = (col) => col.type || col.dtype || 'str';

    if (transform_op === 'join') {
      if (leftColumns.length > 0 && rightColumns.length > 0) {
        const leftOn = config.left_on;
        const rightOn = config.right_on;
        
        // Colonne da sinistra con tipo
        outputColumns = leftColumns.map(c => ({
          name: c.name === leftOn ? c.name : c.name + '_L',
          type: getColType(c),
          visible: true
        }));
        
        const outputColNames = outputColumns.map(c => c.name);
        
        // Colonne da destra con tipo
        rightColumns.forEach(rc => {
          const rightName = rc.name === rightOn ? rc.name : rc.name + '_R';
          if (!outputColNames.includes(rightName)) {
            outputColumns.push({ 
              name: rightName, 
              type: getColType(rc),
              visible: true 
            });
          }
        });
      }
    } else if (transform_op === 'formula') {
      if (inputColumns.length > 0) {
        // Copia colonne esistenti con tipo
        outputColumns = inputColumns.map(c => ({ 
          name: c.name,
          type: getColType(c),
          visible: true 
        }));
        // Aggiungi nuova colonna calcolata
        if (config.new_column && !outputColumns.find(c => c.name === config.new_column)) {
          // Inferisci tipo dalla formula (default 'real' per calcoli numerici)
          const inferredType = inferFormulaType(config.formula);
          outputColumns.push({ 
            name: config.new_column, 
            type: inferredType,
            visible: true 
          });
        }
      }
    } else if (transform_op === 'filter' || transform_op === 'sort' || transform_op === 'distinct') {
      // Queste operazioni mantengono tutte le colonne con i loro tipi
      const cols = transform_op === 'filter' ? inputColumns : singleInputColumns;
      if (cols.length > 0) {
        outputColumns = cols.map(c => ({ 
          name: c.name,
          type: getColType(c),
          visible: true 
        }));
      }
    } else if (transform_op === 'concat') {
      if (incomingEdges.length > 0) {
        const allInputs = incomingEdges.map(edge => {
          const node = nodes.find(n => n.id === edge.source);
          return node?.data?.columns?.filter(c => c.visible !== false) || [];
        });
        
        if (allInputs.length > 0 && allInputs[0].length > 0) {
          const joinMode = config.join || 'outer';
          
          if (joinMode === 'inner') {
            const firstInputCols = allInputs[0].map(c => c.name);
            const commonCols = firstInputCols.filter(colName =>
              allInputs.every(inputCols => inputCols.find(c => c.name === colName))
            );
            outputColumns = commonCols.map(name => {
              const sourceCol = allInputs[0].find(c => c.name === name);
              return {
                name,
                type: getColType(sourceCol),
                visible: true
              };
            });
          } else {
            // Outer join: tutte le colonne
            const allColNames = new Set();
            const colTypes = {};
            allInputs.forEach(inputCols => {
              inputCols.forEach(col => {
                allColNames.add(col.name);
                // Prendi il primo tipo trovato per ogni colonna
                if (!colTypes[col.name]) {
                  colTypes[col.name] = getColType(col);
                }
              });
            });
            outputColumns = Array.from(allColNames).map(name => ({
              name,
              type: colTypes[name] || 'str',
              visible: true
            }));
          }
        }
      }
    }

    return outputColumns;
  };

  /**
   * Inferisce il tipo di una colonna calcolata dalla formula
   * Default 'real' per operazioni matematiche, 'str' per operazioni stringa
   */
  const inferFormulaType = (formula) => {
    if (!formula) return 'str';
    
    const formulaLower = formula.toLowerCase();
    
    // Operazioni stringa
    if (formulaLower.includes('.str.') || 
        formulaLower.includes('+ "') || 
        formulaLower.includes("+ '")) {
      return 'str';
    }
    
    // Operazioni booleane
    if (formulaLower.includes(' == ') || 
        formulaLower.includes(' != ') ||
        formulaLower.includes(' > ') ||
        formulaLower.includes(' < ') ||
        formulaLower.includes(' and ') ||
        formulaLower.includes(' or ')) {
      return 'bool';
    }
    
    // Operazioni matematiche -> default real
    if (formula.includes('+') || 
        formula.includes('-') || 
        formula.includes('*') || 
        formula.includes('/')) {
      return 'real';
    }
    
    return 'str';
  };

  // Effect: Ricalcola colonne quando cambia config
  useEffect(() => {
    const outputColumns = calculateOutputColumns();
    if (data.onUpdateColumns && outputColumns.length > 0) {
      data.onUpdateColumns(id, outputColumns);
    }
  }, [transform_op, config, incomingEdges.length, leftColumns.length, rightColumns.length, inputColumns.length, singleInputColumns.length]);

  // Insert column name in formula
  const insertColumn = (columnName) => {
    if (!formulaRef.current) return;
    const textarea = formulaRef.current;
    const start = textarea.selectionStart;
    const end = textarea.selectionEnd;
    const currentValue = config.formula || '';
    const newValue = currentValue.substring(0, start) + columnName + currentValue.substring(end);
    updateConfig('formula', newValue);
    setTimeout(() => {
      textarea.focus();
      const newCursorPos = start + columnName.length;
      textarea.setSelectionRange(newCursorPos, newCursorPos);
    }, 0);
  };

  // === RENDER ===
  return (
    <div className={`transform-node transform-node--${transform_op}`}>
      <Handle
        type="target"
        position={Position.Left}
        className="transform-node__handle"
      />

      {/* Header */}
      <div className="transform-node__header">
        <div className="transform-node__title">
          <span>⚙️ Trasformazione</span>
        </div>
        <button
          onClick={handleDelete}
          className="transform-node__delete-btn"
          title="Elimina nodo"
        >
          ×
        </button>
      </div>

      {/* Body */}
      <div className="transform-node__body">
        {/* Tabs per categoria */}
        <div className="transform-node__tabs">
          <button
            className={`transform-node__tab ${activeTab === 'base' ? 'transform-node__tab--active' : ''}`}
            onClick={() => setActiveTab('base')}
          >
            📊 Base
          </button>
          <button
            className={`transform-node__tab ${activeTab === 'spatial' ? 'transform-node__tab--active' : ''}`}
            onClick={() => setActiveTab('spatial')}
          >
            🌍 Spaziale
          </button>
        </div>

        {/* Selezione tipo operazione */}
        <div className="transform-node__section">
          <label className="transform-node__label">Tipo operazione:</label>
          <select 
            value={transform_op} 
            onChange={onOpChange}
            className="transform-node__select"
          >
            {filteredOperations.map(op => (
              <option key={op.value} value={op.value}>
                {op.label}
              </option>
            ))}
          </select>
        </div>

        {/* === CONFIG JOIN === */}
        {transform_op === 'join' && (
          <div className="transform-node__config">
            {!joinValidation.valid ? (
              <div className={`transform-node__message transform-node__message--${joinValidation.type}`}>
                {joinValidation.type === 'warning' ? '⚠️' : '❌'} {joinValidation.message}
              </div>
            ) : (
              <>
                {/* Info nodi collegati */}
                <div className="transform-node__info-box">
                  <div className="transform-node__info-row">
                    <span className="transform-node__info-label">LEFT:</span> {getNodeLabel(leftNode)}
                    <span className="transform-node__info-count">({leftColumns.length} col)</span>
                  </div>
                  <div className="transform-node__info-row">
                    <span className="transform-node__info-label">RIGHT:</span> {getNodeLabel(rightNode)}
                    <span className="transform-node__info-count">({rightColumns.length} col)</span>
                  </div>
                </div>

                {/* Tipo JOIN */}
                <div className="transform-node__config-group">
                  <label className="transform-node__label">Tipo JOIN:</label>
                  <div className="transform-node__join-types">
                    {['left', 'inner', 'right', 'outer'].map(jt => (
                      <label 
                        key={jt}
                        className={`transform-node__join-type ${config.how === jt ? 'transform-node__join-type--selected' : ''}`}
                      >
                        <input
                          type="radio"
                          name={`join-type-${id}`}
                          value={jt}
                          checked={config.how === jt}
                          onChange={(e) => updateConfig('how', e.target.value)}
                        />
                        {jt.toUpperCase()}
                      </label>
                    ))}
                  </div>
                </div>

                {/* Colonna LEFT */}
                <div className="transform-node__config-group">
                  <label className="transform-node__label">Colonna LEFT:</label>
                  <select
                    value={config.left_on || ''}
                    onChange={(e) => updateConfig('left_on', e.target.value)}
                    className="transform-node__config-select"
                  >
                    <option value="">-- Seleziona --</option>
                    {leftColumns.map((col, idx) => (
                      <option key={idx} value={col.name}>
                        {getColumnTypeIcon(col.type || col.dtype)} {col.name}
                      </option>
                    ))}
                  </select>
                </div>

                {/* Colonna RIGHT */}
                <div className="transform-node__config-group">
                  <label className="transform-node__label">Colonna RIGHT:</label>
                  <select
                    value={config.right_on || ''}
                    onChange={(e) => updateConfig('right_on', e.target.value)}
                    className="transform-node__config-select"
                  >
                    <option value="">-- Seleziona --</option>
                    {rightColumns.map((col, idx) => (
                      <option key={idx} value={col.name}>
                        {getColumnTypeIcon(col.type || col.dtype)} {col.name}
                      </option>
                    ))}
                  </select>
                </div>

                {/* Confirmation */}
                {config.left_on && config.right_on && (
                  <div className="transform-node__confirm-box">
                    <div className="transform-node__confirm-title">✓ Configurazione:</div>
                    <div className="transform-node__confirm-content">
                      {config.how?.toUpperCase() || 'LEFT'} JOIN ON {config.left_on} = {config.right_on}
                    </div>
                  </div>
                )}
              </>
            )}
          </div>
        )}

        {/* === CONFIG FORMULA === */}
        {transform_op === 'formula' && (
          <div className="transform-node__config">
            {!formulaValidation.valid ? (
              <div className={`transform-node__message transform-node__message--${formulaValidation.type}`}>
                {formulaValidation.type === 'warning' ? '⚠️' : '❌'} {formulaValidation.message}
              </div>
            ) : (
              <>
                {/* Nome nuova colonna */}
                <div className="transform-node__config-group">
                  <label className="transform-node__label">Nome nuova colonna:</label>
                  <input
                    type="text"
                    value={config.new_column || ''}
                    onChange={(e) => updateConfig('new_column', e.target.value)}
                    placeholder="es: totale_calcolato"
                    className="transform-node__input"
                  />
                </div>

                {/* Formula */}
                <div className="transform-node__config-group">
                  <label className="transform-node__label">Formula:</label>
                  <textarea
                    ref={formulaRef}
                    value={config.formula || ''}
                    onChange={(e) => updateConfig('formula', e.target.value)}
                    placeholder="es: col1 + col2 * 100"
                    className="transform-node__textarea"
                    rows={3}
                  />
                </div>

                {/* Colonne cliccabili con icone tipo */}
                <div className="transform-node__config-group">
                  <div className="transform-node__columns-hint">Click per inserire:</div>
                  <div className="transform-node__columns-list">
                    {inputColumns.map((col, idx) => (
                      <span
                        key={idx}
                        className="transform-node__column-chip"
                        onClick={() => insertColumn(col.name)}
                        title={col.type || col.dtype || 'str'}
                      >
                        {getColumnTypeIcon(col.type || col.dtype)} {col.name}
                      </span>
                    ))}
                  </div>
                </div>

                {/* Help */}
                <div className="transform-node__help-box transform-node__help-box--formula">
                  <div className="transform-node__help-title">ℹ️ Operatori supportati:</div>
                  <div className="transform-node__help-code">+ - * / // % ** &amp; |</div>
                  <div className="transform-node__help-hint">Stringhe: col.str.upper(), col.str.contains("x")</div>
                </div>

                {/* Confirmation */}
                {config.new_column && config.formula && (
                  <div className="transform-node__confirm-box">
                    <div className="transform-node__confirm-title">✓ Configurazione:</div>
                    <div className="transform-node__confirm-content">
                      {config.new_column} = {config.formula}
                    </div>
                    <div className="transform-node__confirm-hint">
                      Output: {inputColumns.length + 1} colonne
                    </div>
                  </div>
                )}
              </>
            )}
          </div>
        )}

        {/* === CONFIG FILTER === */}
        {transform_op === 'filter' && (
          <div className="transform-node__config">
            {!formulaValidation.valid ? (
              <div className={`transform-node__message transform-node__message--${formulaValidation.type}`}>
                {formulaValidation.type === 'warning' ? '⚠️' : '❌'} {formulaValidation.message}
              </div>
            ) : (
              <>
                {/* Condizione */}
                <div className="transform-node__config-group">
                  <label className="transform-node__label">Condizione filtro:</label>
                  <textarea
                    value={config.condition || ''}
                    onChange={(e) => updateConfig('condition', e.target.value)}
                    placeholder='es: colonna > 100 & colonna2 == "valore"'
                    className="transform-node__textarea"
                    rows={3}
                  />
                </div>

                {/* Colonne cliccabili con icone tipo */}
                <div className="transform-node__config-group">
                  <div className="transform-node__columns-hint">Click per inserire:</div>
                  <div className="transform-node__columns-list">
                    {inputColumns.map((col, idx) => (
                      <span
                        key={idx}
                        className="transform-node__column-chip"
                        onClick={() => {
                          const textarea = document.querySelector(`#filter-condition-${id}`);
                          if (textarea) {
                            const start = textarea.selectionStart;
                            const val = config.condition || '';
                            updateConfig('condition', val.substring(0, start) + col.name + val.substring(start));
                          } else {
                            updateConfig('condition', (config.condition || '') + col.name);
                          }
                        }}
                        title={col.type || col.dtype || 'str'}
                      >
                        {getColumnTypeIcon(col.type || col.dtype)} {col.name}
                      </span>
                    ))}
                  </div>
                </div>

                {/* Help */}
                <div className="transform-node__help-box">
                  <div className="transform-node__help-title">ℹ️ Operatori supportati:</div>
                  <div className="transform-node__help-code">== != &gt; &lt; &gt;= &lt;= &amp; (AND) | (OR)</div>
                  <div className="transform-node__help-hint">Stringhe tra virgolette: colonna == "valore"</div>
                </div>

                {/* Confirmation */}
                {config.condition && (
                  <div className="transform-node__confirm-box">
                    <div className="transform-node__confirm-title">✓ Configurazione:</div>
                    <div className="transform-node__confirm-content">WHERE {config.condition}</div>
                    <div className="transform-node__confirm-hint">
                      Output: {inputColumns.length} colonne (righe filtrate)
                    </div>
                  </div>
                )}
              </>
            )}
          </div>
        )}

        {/* === CONFIG CONCAT === */}
        {transform_op === 'concat' && (
          <div className="transform-node__config">
            {incomingEdges.length < 2 ? (
              <div className="transform-node__message transform-node__message--warning">
                ⚠️ Concat richiede almeno 2 input! Hai {incomingEdges.length} connessione/i.
                <div className="transform-node__message-hint">
                  Collega più nodi sorgente o trasformazioni.
                </div>
              </div>
            ) : (
              <>
                {/* Info */}
                <div className="transform-node__info-box">
                  <strong>Input collegati:</strong> {incomingEdges.length} dataset
                  <div className="transform-node__message-hint">
                    I dati verranno uniti verticalmente (append)
                  </div>
                </div>

                {/* Modalità colonne */}
                <div className="transform-node__config-group">
                  <label className="transform-node__label">Gestione colonne:</label>
                  <div className="transform-node__options-group">
                    <label className="transform-node__option-label">
                      <input
                        type="radio"
                        name={`concat-join-${id}`}
                        value="inner"
                        checked={config.join === 'inner'}
                        onChange={(e) => updateConfig('join', e.target.value)}
                      />
                      <div className="transform-node__option-content">
                        <div className="transform-node__option-title">Solo colonne comuni</div>
                        <div className="transform-node__option-desc">
                          Mantiene solo colonne presenti in tutti gli input
                        </div>
                      </div>
                    </label>
                    <label className="transform-node__option-label">
                      <input
                        type="radio"
                        name={`concat-join-${id}`}
                        value="outer"
                        checked={config.join !== 'inner'}
                        onChange={(e) => updateConfig('join', e.target.value)}
                      />
                      <div className="transform-node__option-content">
                        <div className="transform-node__option-title">Tutte le colonne</div>
                        <div className="transform-node__option-desc">
                          Mantiene tutte le colonne (NULL dove mancanti)
                        </div>
                      </div>
                    </label>
                  </div>
                </div>

                {/* Reset indice */}
                <label className="transform-node__option-label" style={{ padding: '8px', background: '#fafafa', borderRadius: '4px', border: '1px solid #e0e0e0' }}>
                  <input
                    type="checkbox"
                    checked={config.ignore_index !== false}
                    onChange={(e) => updateConfig('ignore_index', e.target.checked)}
                  />
                  <div className="transform-node__option-content">
                    <div className="transform-node__option-title">Reset indice righe</div>
                    <div className="transform-node__option-desc">
                      Rinumera le righe da 0 (consigliato)
                    </div>
                  </div>
                </label>

                {/* Confirmation */}
                <div className="transform-node__confirm-box">
                  <div className="transform-node__confirm-title">✓ Configurazione Concat:</div>
                  <div className="transform-node__confirm-content">
                    • {incomingEdges.length} dataset in input<br/>
                    • Colonne: {config.join === 'inner' ? 'solo comuni' : 'tutte (outer)'}<br/>
                    • Reset indice: {config.ignore_index !== false ? 'sì' : 'no'}
                  </div>
                </div>
              </>
            )}
          </div>
        )}

        {/* === CONFIG AGGREGATE === */}
        {transform_op === 'aggregate' && (
          <div className="transform-node__config">
            <div className="transform-node__message transform-node__message--info">
              ℹ️ Aggregazione (GroupBy) - Configurazione avanzata in sviluppo
            </div>
          </div>
        )}

        {/* === CONFIG SORT === */}
        {transform_op === 'sort' && (
          <div className="transform-node__config">
            {!singleInputValidation.valid ? (
              <div className={`transform-node__message transform-node__message--${singleInputValidation.type}`}>
                ⚠️ {singleInputValidation.message}
              </div>
            ) : (
              <>
                {/* Colonne ordinamento */}
                <div className="transform-node__config-group">
                  <label className="transform-node__label">Colonne ordinamento:</label>
                  
                  {/* Lista colonne configurate */}
                  {(config.sort_columns || []).map((sortCol, idx) => (
                    <div key={idx} style={{
                      display: 'flex',
                      gap: '6px',
                      marginBottom: '6px',
                      alignItems: 'center'
                    }}>
                      <select
                        value={sortCol.column || ''}
                        onChange={(e) => {
                          const newSortCols = [...(config.sort_columns || [])];
                          newSortCols[idx] = { ...newSortCols[idx], column: e.target.value };
                          updateConfig('sort_columns', newSortCols);
                        }}
                        className="transform-node__config-select"
                        style={{ flex: 1 }}
                      >
                        <option value="">-- Seleziona --</option>
                        {singleInputColumns.map((col, cidx) => (
                          <option key={cidx} value={col.name}>
                            {col.name}
                          </option>
                        ))}
                      </select>
                      
                      <select
                        value={sortCol.ascending ? 'asc' : 'desc'}
                        onChange={(e) => {
                          const newSortCols = [...(config.sort_columns || [])];
                          newSortCols[idx] = { ...newSortCols[idx], ascending: e.target.value === 'asc' };
                          updateConfig('sort_columns', newSortCols);
                        }}
                        className="transform-node__config-select"
                        style={{ width: '80px' }}
                      >
                        <option value="asc">↑ ASC</option>
                        <option value="desc">↓ DESC</option>
                      </select>
                      
                      <button
                        onClick={() => {
                          const newSortCols = (config.sort_columns || []).filter((_, i) => i !== idx);
                          updateConfig('sort_columns', newSortCols);
                        }}
                        style={{
                          background: '#f44336',
                          color: 'white',
                          border: 'none',
                          borderRadius: '3px',
                          padding: '4px 8px',
                          cursor: 'pointer',
                          fontSize: '12px'
                        }}
                      >
                        ✕
                      </button>
                    </div>
                  ))}
                  
                  {/* Bottone aggiungi colonna */}
                  <button
                    onClick={() => {
                      const newSortCols = [...(config.sort_columns || []), { column: '', ascending: true }];
                      updateConfig('sort_columns', newSortCols);
                    }}
                    style={{
                      background: '#607D8B',
                      color: 'white',
                      border: 'none',
                      borderRadius: '4px',
                      padding: '6px 12px',
                      cursor: 'pointer',
                      fontSize: '11px',
                      marginTop: '4px'
                    }}
                  >
                    + Aggiungi colonna
                  </button>
                </div>

                {/* Posizione NULL */}
                <div className="transform-node__config-group">
                  <label className="transform-node__label">Posizione valori NULL:</label>
                  <select
                    value={config.na_position || 'last'}
                    onChange={(e) => updateConfig('na_position', e.target.value)}
                    className="transform-node__config-select"
                  >
                    <option value="last">Alla fine</option>
                    <option value="first">All'inizio</option>
                  </select>
                </div>

                {/* Riepilogo configurazione */}
                {(config.sort_columns || []).length > 0 && (
                  <div className="transform-node__confirm-box">
                    <div className="transform-node__confirm-title">↕️ Configurazione Sort:</div>
                    <div className="transform-node__confirm-content">
                      {(config.sort_columns || []).map((sc, i) => (
                        <div key={i}>
                          {i + 1}. {sc.column || '?'} ({sc.ascending ? 'ASC ↑' : 'DESC ↓'})
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </>
            )}
          </div>
        )}

        {/* === CONFIG DISTINCT === */}
        {transform_op === 'distinct' && (
          <div className="transform-node__config">
            {!singleInputValidation.valid ? (
              <div className={`transform-node__message transform-node__message--${singleInputValidation.type}`}>
                ⚠️ {singleInputValidation.message}
              </div>
            ) : (
              <>
                {/* Modalità: tutte le colonne o subset */}
                <div className="transform-node__config-group">
                  <label className="transform-node__label">Considera duplicati su:</label>
                  <select
                    value={config.mode || 'all'}
                    onChange={(e) => {
                      updateConfig('mode', e.target.value);
                      if (e.target.value === 'all') {
                        updateConfig('subset', null);
                      }
                    }}
                    className="transform-node__config-select"
                  >
                    <option value="all">Tutte le colonne</option>
                    <option value="subset">Solo alcune colonne</option>
                  </select>
                </div>

                {/* Selezione colonne subset */}
                {config.mode === 'subset' && (
                  <div className="transform-node__config-group">
                    <label className="transform-node__label">Colonne da considerare:</label>
                    <div style={{
                      maxHeight: '120px',
                      overflowY: 'auto',
                      border: '1px solid #e0e0e0',
                      borderRadius: '4px',
                      padding: '6px'
                    }}>
                      {singleInputColumns.map((col, idx) => (
                        <label key={idx} style={{
                          display: 'flex',
                          alignItems: 'center',
                          gap: '6px',
                          padding: '3px 0',
                          cursor: 'pointer',
                          fontSize: '11px'
                        }}>
                          <input
                            type="checkbox"
                            checked={(config.subset || []).includes(col.name)}
                            onChange={(e) => {
                              const currentSubset = config.subset || [];
                              let newSubset;
                              if (e.target.checked) {
                                newSubset = [...currentSubset, col.name];
                              } else {
                                newSubset = currentSubset.filter(c => c !== col.name);
                              }
                              updateConfig('subset', newSubset);
                            }}
                          />
                          <span style={{ fontFamily: 'monospace' }}>{col.name}</span>
                        </label>
                      ))}
                    </div>
                  </div>
                )}

                {/* Keep mode */}
                <div className="transform-node__config-group">
                  <label className="transform-node__label">In caso di duplicati, mantieni:</label>
                  <select
                    value={config.keep || 'first'}
                    onChange={(e) => updateConfig('keep', e.target.value)}
                    className="transform-node__config-select"
                  >
                    <option value="first">Prima occorrenza</option>
                    <option value="last">Ultima occorrenza</option>
                    <option value="false">Nessuna (rimuovi tutti)</option>
                  </select>
                </div>

                {/* Riepilogo */}
                <div className="transform-node__confirm-box">
                  <div className="transform-node__confirm-title">🔹 Configurazione Distinct:</div>
                  <div className="transform-node__confirm-content">
                    Duplicati su: {config.mode === 'subset' ? (config.subset || []).join(', ') || 'nessuna colonna' : 'tutte le colonne'}<br/>
                    Mantieni: {config.keep === 'first' ? 'prima' : config.keep === 'last' ? 'ultima' : 'nessuna'}
                  </div>
                </div>
              </>
            )}
          </div>
        )}

        {/* === CONFIG SPATIAL JOIN === */}
        {transform_op === 'sjoin' && (
          <div className="transform-node__config">
            {!sjoinValidation.valid ? (
              <div className={`transform-node__message transform-node__message--${sjoinValidation.type}`}>
                ⚠️ {sjoinValidation.message}
                <div className="transform-node__message-hint">
                  Collega 2 sorgenti dati con colonne geometriche.
                </div>
              </div>
            ) : (
              <>
                {/* Colonna geometria sinistra */}
                <div className="transform-node__config-group">
                  <label className="transform-node__label">Geometria sinistra (1° input):</label>
                  <select
                    value={config.left_geom || leftGeomColumns[0]?.name || ''}
                    onChange={(e) => updateConfig('left_geom', e.target.value)}
                    className="transform-node__select"
                  >
                    {leftGeomColumns.map((col, idx) => (
                      <option key={idx} value={col.name}>
                        🌐 {col.name}
                      </option>
                    ))}
                  </select>
                </div>

                {/* Colonna geometria destra */}
                <div className="transform-node__config-group">
                  <label className="transform-node__label">Geometria destra (2° input):</label>
                  <select
                    value={config.right_geom || rightGeomColumns[0]?.name || ''}
                    onChange={(e) => updateConfig('right_geom', e.target.value)}
                    className="transform-node__select"
                  >
                    {rightGeomColumns.map((col, idx) => (
                      <option key={idx} value={col.name}>
                        🌐 {col.name}
                      </option>
                    ))}
                  </select>
                </div>

                {/* Predicato spaziale */}
                <div className="transform-node__config-group">
                  <label className="transform-node__label">Predicato spaziale:</label>
                  <select
                    value={config.predicate || 'intersects'}
                    onChange={(e) => updateConfig('predicate', e.target.value)}
                    className="transform-node__select"
                  >
                    <option value="intersects">Intersects (si toccano/sovrappongono)</option>
                    <option value="within">Within (A dentro B)</option>
                    <option value="contains">Contains (A contiene B)</option>
                    <option value="touches">Touches (si toccano al bordo)</option>
                    <option value="crosses">Crosses (si incrociano)</option>
                    <option value="overlaps">Overlaps (si sovrappongono parzialmente)</option>
                  </select>
                </div>

                {/* Tipo join */}
                <div className="transform-node__config-group">
                  <label className="transform-node__label">Tipo join:</label>
                  <select
                    value={config.how || 'inner'}
                    onChange={(e) => updateConfig('how', e.target.value)}
                    className="transform-node__select"
                  >
                    <option value="inner">Inner (solo match)</option>
                    <option value="left">Left (tutti da sinistra)</option>
                    <option value="right">Right (tutti da destra)</option>
                  </select>
                </div>

                {/* Confirmation */}
                <div className="transform-node__confirm-box transform-node__confirm-box--spatial">
                  <div className="transform-node__confirm-title">🌐 Configurazione Spatial Join:</div>
                  <div className="transform-node__confirm-content">
                    • Predicato: {config.predicate || 'intersects'}<br/>
                    • Tipo: {config.how || 'inner'} join
                  </div>
                </div>
              </>
            )}
          </div>
        )}

        {/* === CONFIG GEO BUFFER === */}
        {transform_op === 'geo_buffer' && (
          <div className="transform-node__config">
            {!spatialValidation.valid ? (
              <div className={`transform-node__message transform-node__message--${spatialValidation.type}`}>
                ⚠️ {spatialValidation.message}
              </div>
            ) : (
              <>
                {/* Colonna geometria */}
                <div className="transform-node__config-group">
                  <label className="transform-node__label">Colonna geometria:</label>
                  <select
                    value={config.geom_column || geometryColumns[0]?.name || ''}
                    onChange={(e) => updateConfig('geom_column', e.target.value)}
                    className="transform-node__select"
                  >
                    {geometryColumns.map((col, idx) => (
                      <option key={idx} value={col.name}>
                        🌐 {col.name}
                      </option>
                    ))}
                  </select>
                </div>

                {/* Distanza */}
                <div className="transform-node__config-group">
                  <label className="transform-node__label">Distanza buffer (metri):</label>
                  <input
                    type="number"
                    value={config.distance || 100}
                    onChange={(e) => updateConfig('distance', parseFloat(e.target.value))}
                    min="0"
                    step="10"
                    className="transform-node__input"
                  />
                </div>

                {/* Nome colonna output */}
                <div className="transform-node__config-group">
                  <label className="transform-node__label">Nome colonna output:</label>
                  <input
                    type="text"
                    value={config.output_column || 'buffer_geom'}
                    onChange={(e) => updateConfig('output_column', e.target.value)}
                    placeholder="buffer_geom"
                    className="transform-node__input"
                  />
                </div>

                {/* Stile */}
                <div className="transform-node__config-group">
                  <label className="transform-node__label">Stile bordo:</label>
                  <select
                    value={config.cap_style || 'round'}
                    onChange={(e) => updateConfig('cap_style', e.target.value)}
                    className="transform-node__select"
                  >
                    <option value="round">Arrotondato</option>
                    <option value="flat">Piatto</option>
                    <option value="square">Quadrato</option>
                  </select>
                </div>

                {/* Confirmation */}
                <div className="transform-node__confirm-box transform-node__confirm-box--spatial">
                  <div className="transform-node__confirm-title">🔵 Configurazione Buffer:</div>
                  <div className="transform-node__confirm-content">
                    • Distanza: {config.distance || 100}m<br/>
                    • Output: {config.output_column || 'buffer_geom'}
                  </div>
                </div>
              </>
            )}
          </div>
        )}

        {/* === CONFIG GEO CENTROID === */}
        {transform_op === 'geo_centroid' && (
          <div className="transform-node__config">
            {!spatialValidation.valid ? (
              <div className={`transform-node__message transform-node__message--${spatialValidation.type}`}>
                ⚠️ {spatialValidation.message}
              </div>
            ) : (
              <>
                {/* Colonna geometria */}
                <div className="transform-node__config-group">
                  <label className="transform-node__label">Colonna geometria:</label>
                  <select
                    value={config.geom_column || geometryColumns[0]?.name || ''}
                    onChange={(e) => updateConfig('geom_column', e.target.value)}
                    className="transform-node__select"
                  >
                    {geometryColumns.map((col, idx) => (
                      <option key={idx} value={col.name}>
                        🌐 {col.name}
                      </option>
                    ))}
                  </select>
                </div>

                {/* Nome colonna output */}
                <div className="transform-node__config-group">
                  <label className="transform-node__label">Nome colonna output:</label>
                  <input
                    type="text"
                    value={config.output_column || 'centroid'}
                    onChange={(e) => updateConfig('output_column', e.target.value)}
                    placeholder="centroid"
                    className="transform-node__input"
                  />
                </div>

                {/* Confirmation */}
                <div className="transform-node__confirm-box transform-node__confirm-box--spatial">
                  <div className="transform-node__confirm-title">📍 Configurazione Centroide:</div>
                  <div className="transform-node__confirm-content">
                    Colonna: {config.geom_column || geometryColumns[0]?.name || 'auto'}<br/>
                    Output: {config.output_column || 'centroid'}
                  </div>
                </div>
              </>
            )}
          </div>
        )}

        {/* === CONFIG GEO AREA === */}
        {transform_op === 'geo_area' && (
          <div className="transform-node__config">
            {!spatialValidation.valid ? (
              <div className={`transform-node__message transform-node__message--${spatialValidation.type}`}>
                ⚠️ {spatialValidation.message}
              </div>
            ) : (
              <>
                {/* Colonna geometria */}
                <div className="transform-node__config-group">
                  <label className="transform-node__label">Colonna geometria:</label>
                  <select
                    value={config.geom_column || geometryColumns[0]?.name || ''}
                    onChange={(e) => updateConfig('geom_column', e.target.value)}
                    className="transform-node__select"
                  >
                    {geometryColumns.map((col, idx) => (
                      <option key={idx} value={col.name}>
                        🌐 {col.name}
                      </option>
                    ))}
                  </select>
                </div>

                {/* Nome colonna output */}
                <div className="transform-node__config-group">
                  <label className="transform-node__label">Nome colonna output:</label>
                  <input
                    type="text"
                    value={config.output_column || 'area_m2'}
                    onChange={(e) => updateConfig('output_column', e.target.value)}
                    placeholder="area_m2"
                    className="transform-node__input"
                  />
                </div>

                {/* Confirmation */}
                <div className="transform-node__confirm-box transform-node__confirm-box--spatial">
                  <div className="transform-node__confirm-title">📐 Configurazione Area:</div>
                  <div className="transform-node__confirm-content">
                    Colonna: {config.geom_column || geometryColumns[0]?.name || 'auto'}<br/>
                    Output: {config.output_column || 'area_m2'} (m²)
                  </div>
                </div>
              </>
            )}
          </div>
        )}

        {/* === CONFIG GEO CLIP === */}
        {transform_op === 'geo_clip' && (
          <div className="transform-node__config">
            {!sjoinValidation.valid ? (
              <div className={`transform-node__message transform-node__message--${sjoinValidation.type}`}>
                ⚠️ {sjoinValidation.message}
                <div className="transform-node__message-hint">
                  1° input: layer da ritagliare<br/>
                  2° input: maschera di ritaglio
                </div>
              </div>
            ) : (
              <>
                {/* Colonna geometria input */}
                <div className="transform-node__config-group">
                  <label className="transform-node__label">Geometria da ritagliare (1° input):</label>
                  <select
                    value={config.input_geom || leftGeomColumns[0]?.name || ''}
                    onChange={(e) => updateConfig('input_geom', e.target.value)}
                    className="transform-node__select"
                  >
                    {leftGeomColumns.map((col, idx) => (
                      <option key={idx} value={col.name}>
                        🌐 {col.name}
                      </option>
                    ))}
                  </select>
                </div>

                {/* Colonna geometria maschera */}
                <div className="transform-node__config-group">
                  <label className="transform-node__label">Geometria maschera (2° input):</label>
                  <select
                    value={config.mask_geom || rightGeomColumns[0]?.name || ''}
                    onChange={(e) => updateConfig('mask_geom', e.target.value)}
                    className="transform-node__select"
                  >
                    {rightGeomColumns.map((col, idx) => (
                      <option key={idx} value={col.name}>
                        🌐 {col.name}
                      </option>
                    ))}
                  </select>
                </div>

                {/* Confirmation */}
                <div className="transform-node__confirm-box transform-node__confirm-box--spatial">
                  <div className="transform-node__confirm-title">✂️ Configurazione Clip:</div>
                  <div className="transform-node__confirm-content">
                    Ritaglia: {config.input_geom || leftGeomColumns[0]?.name || 'auto'}<br/>
                    Maschera: {config.mask_geom || rightGeomColumns[0]?.name || 'auto'}
                  </div>
                </div>
              </>
            )}
          </div>
        )}

        {/* === CONFIG GEO DISSOLVE === */}
        {transform_op === 'geo_dissolve' && (
          <div className="transform-node__config">
            {!spatialValidation.valid ? (
              <div className={`transform-node__message transform-node__message--${spatialValidation.type}`}>
                ⚠️ {spatialValidation.message}
              </div>
            ) : (
              <>
                <div className="transform-node__config-group">
                  <label className="transform-node__label">Colonna geometria:</label>
                  <select
                    value={config.geom_column || geometryColumns[0]?.name || ''}
                    onChange={(e) => updateConfig('geom_column', e.target.value)}
                    className="transform-node__select"
                  >
                    {geometryColumns.map((col, idx) => (
                      <option key={idx} value={col.name}>🌐 {col.name}</option>
                    ))}
                  </select>
                </div>

                <div className="transform-node__config-group">
                  <label className="transform-node__label">Raggruppa per colonna (opzionale):</label>
                  <select
                    value={config.by_column || ''}
                    onChange={(e) => updateConfig('by_column', e.target.value)}
                    className="transform-node__select"
                  >
                    <option value="">-- Dissolve tutto --</option>
                    {spatialInputColumns.filter(c => !c.type?.startsWith('geometry')).map((col, idx) => (
                      <option key={idx} value={col.name}>{col.name}</option>
                    ))}
                  </select>
                </div>

                <div className="transform-node__config-group">
                  <label className="transform-node__label">Aggregazione altri campi:</label>
                  <select
                    value={config.aggfunc || 'first'}
                    onChange={(e) => updateConfig('aggfunc', e.target.value)}
                    className="transform-node__select"
                  >
                    <option value="first">Primo valore</option>
                    <option value="last">Ultimo valore</option>
                    <option value="sum">Somma</option>
                    <option value="mean">Media</option>
                    <option value="count">Conteggio</option>
                  </select>
                </div>

                <div className="transform-node__confirm-box transform-node__confirm-box--spatial">
                  <div className="transform-node__confirm-title">🔗 Configurazione Dissolve:</div>
                  <div className="transform-node__confirm-content">
                    Raggruppa per: {config.by_column || 'tutto'}<br/>
                    Aggregazione: {config.aggfunc || 'first'}
                  </div>
                </div>
              </>
            )}
          </div>
        )}

        {/* === CONFIG GEO INTERSECTION === */}
        {transform_op === 'geo_intersection' && (
          <div className="transform-node__config">
            {!sjoinValidation.valid ? (
              <div className={`transform-node__message transform-node__message--${sjoinValidation.type}`}>
                ⚠️ {sjoinValidation.message}
                <div className="transform-node__message-hint">
                  Collega 2 layer con geometrie
                </div>
              </div>
            ) : (
              <>
                <div className="transform-node__config-group">
                  <label className="transform-node__label">Geometria 1° input:</label>
                  <select
                    value={config.left_geom || leftGeomColumns[0]?.name || ''}
                    onChange={(e) => updateConfig('left_geom', e.target.value)}
                    className="transform-node__select"
                  >
                    {leftGeomColumns.map((col, idx) => (
                      <option key={idx} value={col.name}>🌐 {col.name}</option>
                    ))}
                  </select>
                </div>

                <div className="transform-node__config-group">
                  <label className="transform-node__label">Geometria 2° input:</label>
                  <select
                    value={config.right_geom || rightGeomColumns[0]?.name || ''}
                    onChange={(e) => updateConfig('right_geom', e.target.value)}
                    className="transform-node__select"
                  >
                    {rightGeomColumns.map((col, idx) => (
                      <option key={idx} value={col.name}>🌐 {col.name}</option>
                    ))}
                  </select>
                </div>

                <div className="transform-node__confirm-box transform-node__confirm-box--spatial">
                  <div className="transform-node__confirm-title">∩ Intersezione Geometrica</div>
                  <div className="transform-node__confirm-content">
                    Restituisce solo le aree comuni ai due layer
                  </div>
                </div>
              </>
            )}
          </div>
        )}

        {/* === CONFIG GEO UNION === */}
        {transform_op === 'geo_union' && (
          <div className="transform-node__config">
            {!sjoinValidation.valid ? (
              <div className={`transform-node__message transform-node__message--${sjoinValidation.type}`}>
                ⚠️ {sjoinValidation.message}
                <div className="transform-node__message-hint">
                  Collega 2 layer con geometrie
                </div>
              </div>
            ) : (
              <>
                <div className="transform-node__config-group">
                  <label className="transform-node__label">Geometria 1° input:</label>
                  <select
                    value={config.left_geom || leftGeomColumns[0]?.name || ''}
                    onChange={(e) => updateConfig('left_geom', e.target.value)}
                    className="transform-node__select"
                  >
                    {leftGeomColumns.map((col, idx) => (
                      <option key={idx} value={col.name}>🌐 {col.name}</option>
                    ))}
                  </select>
                </div>

                <div className="transform-node__config-group">
                  <label className="transform-node__label">Geometria 2° input:</label>
                  <select
                    value={config.right_geom || rightGeomColumns[0]?.name || ''}
                    onChange={(e) => updateConfig('right_geom', e.target.value)}
                    className="transform-node__select"
                  >
                    {rightGeomColumns.map((col, idx) => (
                      <option key={idx} value={col.name}>🌐 {col.name}</option>
                    ))}
                  </select>
                </div>

                <div className="transform-node__confirm-box transform-node__confirm-box--spatial">
                  <div className="transform-node__confirm-title">∪ Unione Geometrica</div>
                  <div className="transform-node__confirm-content">
                    Combina le geometrie di entrambi i layer
                  </div>
                </div>
              </>
            )}
          </div>
        )}

        {/* === CONFIG GEO DIFFERENCE === */}
        {transform_op === 'geo_difference' && (
          <div className="transform-node__config">
            {!sjoinValidation.valid ? (
              <div className={`transform-node__message transform-node__message--${sjoinValidation.type}`}>
                ⚠️ {sjoinValidation.message}
                <div className="transform-node__message-hint">
                  1° input: layer di partenza<br/>
                  2° input: layer da sottrarre
                </div>
              </div>
            ) : (
              <>
                <div className="transform-node__config-group">
                  <label className="transform-node__label">Geometria di partenza (1° input):</label>
                  <select
                    value={config.left_geom || leftGeomColumns[0]?.name || ''}
                    onChange={(e) => updateConfig('left_geom', e.target.value)}
                    className="transform-node__select"
                  >
                    {leftGeomColumns.map((col, idx) => (
                      <option key={idx} value={col.name}>🌐 {col.name}</option>
                    ))}
                  </select>
                </div>

                <div className="transform-node__config-group">
                  <label className="transform-node__label">Geometria da sottrarre (2° input):</label>
                  <select
                    value={config.right_geom || rightGeomColumns[0]?.name || ''}
                    onChange={(e) => updateConfig('right_geom', e.target.value)}
                    className="transform-node__select"
                  >
                    {rightGeomColumns.map((col, idx) => (
                      <option key={idx} value={col.name}>🌐 {col.name}</option>
                    ))}
                  </select>
                </div>

                <div className="transform-node__confirm-box transform-node__confirm-box--spatial">
                  <div className="transform-node__confirm-title">− Differenza Geometrica</div>
                  <div className="transform-node__confirm-content">
                    Restituisce: Layer 1 MENO Layer 2
                  </div>
                </div>
              </>
            )}
          </div>
        )}

        {/* === CONFIG GEO SIMPLIFY === */}
        {transform_op === 'geo_simplify' && (
          <div className="transform-node__config">
            {!spatialValidation.valid ? (
              <div className={`transform-node__message transform-node__message--${spatialValidation.type}`}>
                ⚠️ {spatialValidation.message}
              </div>
            ) : (
              <>
                <div className="transform-node__config-group">
                  <label className="transform-node__label">Colonna geometria:</label>
                  <select
                    value={config.geom_column || geometryColumns[0]?.name || ''}
                    onChange={(e) => updateConfig('geom_column', e.target.value)}
                    className="transform-node__select"
                  >
                    {geometryColumns.map((col, idx) => (
                      <option key={idx} value={col.name}>🌐 {col.name}</option>
                    ))}
                  </select>
                </div>

                <div className="transform-node__config-group">
                  <label className="transform-node__label">Tolleranza (gradi per WGS84):</label>
                  <input
                    type="number"
                    step="0.0001"
                    value={config.tolerance || 0.001}
                    onChange={(e) => updateConfig('tolerance', parseFloat(e.target.value))}
                    className="transform-node__input"
                  />
                </div>

                <div className="transform-node__config-group">
                  <label className="transform-node__label">
                    <input
                      type="checkbox"
                      checked={config.preserve_topology !== false}
                      onChange={(e) => updateConfig('preserve_topology', e.target.checked)}
                    />
                    {' '}Preserva topologia
                  </label>
                </div>

                <div className="transform-node__confirm-box transform-node__confirm-box--spatial">
                  <div className="transform-node__confirm-title">📐 Semplificazione (Douglas-Peucker)</div>
                  <div className="transform-node__confirm-content">
                    Tolleranza: {config.tolerance || 0.001}<br/>
                    Topologia: {config.preserve_topology !== false ? 'preservata' : 'non preservata'}
                  </div>
                </div>
              </>
            )}
          </div>
        )}

        {/* === CONFIG GEO CONVEX HULL === */}
        {transform_op === 'geo_convex_hull' && (
          <div className="transform-node__config">
            {!spatialValidation.valid ? (
              <div className={`transform-node__message transform-node__message--${spatialValidation.type}`}>
                ⚠️ {spatialValidation.message}
              </div>
            ) : (
              <>
                <div className="transform-node__config-group">
                  <label className="transform-node__label">Colonna geometria:</label>
                  <select
                    value={config.geom_column || geometryColumns[0]?.name || ''}
                    onChange={(e) => updateConfig('geom_column', e.target.value)}
                    className="transform-node__select"
                  >
                    {geometryColumns.map((col, idx) => (
                      <option key={idx} value={col.name}>🌐 {col.name}</option>
                    ))}
                  </select>
                </div>

                <div className="transform-node__config-group">
                  <label className="transform-node__label">Nome colonna output:</label>
                  <input
                    type="text"
                    value={config.output_column || 'convex_hull'}
                    onChange={(e) => updateConfig('output_column', e.target.value)}
                    className="transform-node__input"
                  />
                </div>

                <div className="transform-node__confirm-box transform-node__confirm-box--spatial">
                  <div className="transform-node__confirm-title">⬡ Convex Hull</div>
                  <div className="transform-node__confirm-content">
                    Output: {config.output_column || 'convex_hull'}
                  </div>
                </div>
              </>
            )}
          </div>
        )}

        {/* === CONFIG GEO REPROJECT === */}
        {transform_op === 'geo_reproject' && (
          <div className="transform-node__config">
            {!spatialValidation.valid ? (
              <div className={`transform-node__message transform-node__message--${spatialValidation.type}`}>
                ⚠️ {spatialValidation.message}
              </div>
            ) : (
              <>
                <div className="transform-node__config-group">
                  <label className="transform-node__label">Colonna geometria:</label>
                  <select
                    value={config.geom_column || geometryColumns[0]?.name || ''}
                    onChange={(e) => updateConfig('geom_column', e.target.value)}
                    className="transform-node__select"
                  >
                    {geometryColumns.map((col, idx) => (
                      <option key={idx} value={col.name}>🌐 {col.name}</option>
                    ))}
                  </select>
                </div>

                <div className="transform-node__config-group">
                  <label className="transform-node__label">Sistema di riferimento destinazione:</label>
                  <select
                    value={config.target_crs || 'EPSG:4326'}
                    onChange={(e) => updateConfig('target_crs', e.target.value)}
                    className="transform-node__select"
                  >
                    <option value="EPSG:4326">WGS 84 (EPSG:4326)</option>
                    <option value="EPSG:3857">Web Mercator (EPSG:3857)</option>
                    <option value="EPSG:32632">UTM 32N (EPSG:32632)</option>
                    <option value="EPSG:32633">UTM 33N (EPSG:32633)</option>
                    <option value="EPSG:6706">RDN2008 (EPSG:6706)</option>
                    <option value="EPSG:25832">ETRS89 / UTM 32N (EPSG:25832)</option>
                    <option value="EPSG:25833">ETRS89 / UTM 33N (EPSG:25833)</option>
                  </select>
                </div>

                <div className="transform-node__confirm-box transform-node__confirm-box--spatial">
                  <div className="transform-node__confirm-title">🌍 Riproiezione</div>
                  <div className="transform-node__confirm-content">
                    Destinazione: {config.target_crs || 'EPSG:4326'}
                  </div>
                </div>
              </>
            )}
          </div>
        )}

        {/* === CONFIG RENAME === */}
        {transform_op === 'rename' && (
          <div className="transform-node__config">
            {!formulaValidation.valid ? (
              <div className={`transform-node__message transform-node__message--${formulaValidation.type}`}>
                ⚠️ {formulaValidation.message}
              </div>
            ) : (
              <>
                <div className="transform-node__config-group">
                  <label className="transform-node__label">Colonna da rinominare:</label>
                  <select
                    value={config.old_name || ''}
                    onChange={(e) => updateConfig('old_name', e.target.value)}
                    className="transform-node__select"
                  >
                    <option value="">-- Seleziona --</option>
                    {inputColumns.map((col, idx) => (
                      <option key={idx} value={col.name}>{col.name}</option>
                    ))}
                  </select>
                </div>

                <div className="transform-node__config-group">
                  <label className="transform-node__label">Nuovo nome:</label>
                  <input
                    type="text"
                    value={config.new_name || ''}
                    onChange={(e) => updateConfig('new_name', e.target.value)}
                    placeholder="nuovo_nome"
                    className="transform-node__input"
                  />
                </div>

                {config.old_name && config.new_name && (
                  <div className="transform-node__confirm-box">
                    <div className="transform-node__confirm-title">✏️ Rinomina:</div>
                    <div className="transform-node__confirm-content">
                      {config.old_name} → {config.new_name}
                    </div>
                  </div>
                )}
              </>
            )}
          </div>
        )}

        {/* === CONFIG DROP COLUMNS === */}
        {transform_op === 'drop_columns' && (
          <div className="transform-node__config">
            {!formulaValidation.valid ? (
              <div className={`transform-node__message transform-node__message--${formulaValidation.type}`}>
                ⚠️ {formulaValidation.message}
              </div>
            ) : (
              <>
                <div className="transform-node__config-group">
                  <label className="transform-node__label">Colonne da eliminare:</label>
                  <div className="transform-node__column-chips">
                    {inputColumns.map((col, idx) => {
                      const isSelected = (config.columns || []).includes(col.name);
                      return (
                        <span
                          key={idx}
                          className={`transform-node__column-chip ${isSelected ? 'transform-node__column-chip--selected' : ''}`}
                          onClick={() => {
                            const current = config.columns || [];
                            const updated = isSelected
                              ? current.filter(c => c !== col.name)
                              : [...current, col.name];
                            updateConfig('columns', updated);
                          }}
                        >
                          {col.name}
                        </span>
                      );
                    })}
                  </div>
                </div>

                {(config.columns || []).length > 0 && (
                  <div className="transform-node__confirm-box">
                    <div className="transform-node__confirm-title">🗑️ Colonne da eliminare:</div>
                    <div className="transform-node__confirm-content">
                      {(config.columns || []).join(', ')}
                    </div>
                  </div>
                )}
              </>
            )}
          </div>
        )}

        {/* === CONFIG FILL NA === */}
        {transform_op === 'fill_na' && (
          <div className="transform-node__config">
            {!formulaValidation.valid ? (
              <div className={`transform-node__message transform-node__message--${formulaValidation.type}`}>
                ⚠️ {formulaValidation.message}
              </div>
            ) : (
              <>
                <div className="transform-node__config-group">
                  <label className="transform-node__label">Colonna (vuoto = tutte):</label>
                  <select
                    value={config.column || ''}
                    onChange={(e) => updateConfig('column', e.target.value)}
                    className="transform-node__select"
                  >
                    <option value="">-- Tutte le colonne --</option>
                    {inputColumns.map((col, idx) => (
                      <option key={idx} value={col.name}>{col.name}</option>
                    ))}
                  </select>
                </div>

                <div className="transform-node__config-group">
                  <label className="transform-node__label">Metodo:</label>
                  <select
                    value={config.method || 'value'}
                    onChange={(e) => updateConfig('method', e.target.value === 'value' ? null : e.target.value)}
                    className="transform-node__select"
                  >
                    <option value="value">Valore fisso</option>
                    <option value="ffill">Propaga avanti (ffill)</option>
                    <option value="bfill">Propaga indietro (bfill)</option>
                  </select>
                </div>

                {!config.method && (
                  <div className="transform-node__config-group">
                    <label className="transform-node__label">Valore sostitutivo:</label>
                    <input
                      type="text"
                      value={config.value || ''}
                      onChange={(e) => updateConfig('value', e.target.value)}
                      placeholder="0 oppure N/A"
                      className="transform-node__input"
                    />
                  </div>
                )}

                <div className="transform-node__confirm-box">
                  <div className="transform-node__confirm-title">🔧 Riempi valori nulli</div>
                  <div className="transform-node__confirm-content">
                    Colonna: {config.column || 'tutte'}<br/>
                    Metodo: {config.method || `valore "${config.value || ''}"`}
                  </div>
                </div>
              </>
            )}
          </div>
        )}

        {/* === CONFIG REPLACE === */}
        {transform_op === 'replace' && (
          <div className="transform-node__config">
            {!formulaValidation.valid ? (
              <div className={`transform-node__message transform-node__message--${formulaValidation.type}`}>
                ⚠️ {formulaValidation.message}
              </div>
            ) : (
              <>
                <div className="transform-node__config-group">
                  <label className="transform-node__label">Colonna:</label>
                  <select
                    value={config.column || ''}
                    onChange={(e) => updateConfig('column', e.target.value)}
                    className="transform-node__select"
                  >
                    <option value="">-- Seleziona --</option>
                    {inputColumns.map((col, idx) => (
                      <option key={idx} value={col.name}>{col.name}</option>
                    ))}
                  </select>
                </div>

                <div className="transform-node__config-group">
                  <label className="transform-node__label">Valore da cercare:</label>
                  <input
                    type="text"
                    value={config.old_value || ''}
                    onChange={(e) => updateConfig('old_value', e.target.value)}
                    placeholder="vecchio valore"
                    className="transform-node__input"
                  />
                </div>

                <div className="transform-node__config-group">
                  <label className="transform-node__label">Sostituisci con:</label>
                  <input
                    type="text"
                    value={config.new_value || ''}
                    onChange={(e) => updateConfig('new_value', e.target.value)}
                    placeholder="nuovo valore"
                    className="transform-node__input"
                  />
                </div>

                <div className="transform-node__config-group">
                  <label className="transform-node__label">
                    <input
                      type="checkbox"
                      checked={config.regex || false}
                      onChange={(e) => updateConfig('regex', e.target.checked)}
                    />
                    {' '}Usa espressione regolare
                  </label>
                </div>

                {config.column && config.old_value && (
                  <div className="transform-node__confirm-box">
                    <div className="transform-node__confirm-title">🔄 Sostituzione:</div>
                    <div className="transform-node__confirm-content">
                      {config.column}: "{config.old_value}" → "{config.new_value || ''}"
                      {config.regex && ' (regex)'}
                    </div>
                  </div>
                )}
              </>
            )}
          </div>
        )}

        {/* === CONFIG SPLIT COLUMN === */}
        {transform_op === 'split_column' && (
          <div className="transform-node__config">
            {!formulaValidation.valid ? (
              <div className={`transform-node__message transform-node__message--${formulaValidation.type}`}>
                ⚠️ {formulaValidation.message}
              </div>
            ) : (
              <>
                <div className="transform-node__config-group">
                  <label className="transform-node__label">Colonna da dividere:</label>
                  <select
                    value={config.column || ''}
                    onChange={(e) => updateConfig('column', e.target.value)}
                    className="transform-node__select"
                  >
                    <option value="">-- Seleziona --</option>
                    {inputColumns.map((col, idx) => (
                      <option key={idx} value={col.name}>{col.name}</option>
                    ))}
                  </select>
                </div>

                <div className="transform-node__config-group">
                  <label className="transform-node__label">Delimitatore:</label>
                  <input
                    type="text"
                    value={config.delimiter || ','}
                    onChange={(e) => updateConfig('delimiter', e.target.value)}
                    placeholder=","
                    className="transform-node__input"
                  />
                </div>

                <div className="transform-node__config-group">
                  <label className="transform-node__label">Nomi nuove colonne (separati da virgola):</label>
                  <input
                    type="text"
                    value={(config.new_columns || []).join(', ')}
                    onChange={(e) => updateConfig('new_columns', e.target.value.split(',').map(s => s.trim()).filter(s => s))}
                    placeholder="parte1, parte2, parte3"
                    className="transform-node__input"
                  />
                </div>

                {config.column && (
                  <div className="transform-node__confirm-box">
                    <div className="transform-node__confirm-title">✂️ Dividi Colonna:</div>
                    <div className="transform-node__confirm-content">
                      {config.column} → separato da "{config.delimiter || ','}"
                    </div>
                  </div>
                )}
              </>
            )}
          </div>
        )}
      </div>

      <Handle
        type="source"
        position={Position.Right}
        className="transform-node__handle"
      />
    </div>
  );
});

TransformNode.displayName = 'TransformNode';

export default TransformNode;