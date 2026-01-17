import React, { useState, useEffect, useMemo } from 'react';
import { Handle, Position, useReactFlow } from 'reactflow';
import { getColumnTypeIcon, getColumnTypeLabel } from '../utils/columnUtils';
import '../styles/ApiEnrichNode.css';

/**
 * ApiEnrichNode - Nodo per arricchire dati chiamando API esterne
 * 
 * Input: dati da nodo precedente (ProjectSource o altro ApiEnrich)
 * Output: dati originali + colonne da API (con tipi)
 */
function ApiEnrichNode({ id, data, selected }) {
  const { setNodes, getNodes, getEdges } = useReactFlow();
  
  const [apiConnections, setApiConnections] = useState([]);
  const [loadingApis, setLoadingApis] = useState(true);
  
  // Config del nodo
  const selectedApiId = data.api_id || '';
  const paramMapping = data.param_mapping || {};
  
  // Carica API disponibili
  useEffect(() => {
    loadApiConnections();
  }, []);
  
  const loadApiConnections = async () => {
    try {
      const res = await fetch('http://localhost:8000/api/api-connections');
      const result = await res.json();
      setApiConnections(result.api_connections || []);
    } catch (error) {
      console.error('Errore caricamento API:', error);
    } finally {
      setLoadingApis(false);
    }
  };
  
  // API selezionata
  const selectedApi = useMemo(() => {
    return apiConnections.find(api => api.id === selectedApiId);
  }, [apiConnections, selectedApiId]);
  
  /**
   * Helper per normalizzare il tipo colonna
   */
  const normalizeType = (col) => {
    return col.type || col.dtype || 'str';
  };

  /**
   * Mappa il tipo dell'API ai nostri tipi standard
   */
  const mapApiTypeToStandard = (apiType) => {
    const typeMap = {
      'string': 'str',
      'text': 'str',
      'number': 'real',
      'integer': 'int',
      'float': 'real',
      'double': 'real',
      'boolean': 'bool',
      'bool': 'bool',
      'date': 'date',
      'datetime': 'date',
      'geojson': 'geometry',
      'geometry': 'geometry',
      'point': 'geometry:point',
      'polygon': 'geometry:polygon',
      'multipolygon': 'geometry:multipolygon',
      'linestring': 'geometry:line',
    };
    return typeMap[(apiType || 'string').toLowerCase()] || 'str';
  };
  
  // Calcola colonne input disponibili (dal nodo precedente)
  const inputColumns = useMemo(() => {
    const edges = getEdges();
    const nodes = getNodes();
    
    const incomingEdge = edges.find(e => e.target === id);
    if (!incomingEdge) return [];
    
    const sourceNode = nodes.find(n => n.id === incomingEdge.source);
    if (!sourceNode) return [];
    
    if (sourceNode.data?.columns) {
      return sourceNode.data.columns
        .filter(c => c.visible !== false)
        .map(c => ({
          ...c,
          type: normalizeType(c)
        }));
    }
    
    return [];
  }, [id, getEdges, getNodes, data._lastUpdate]);
  
  // Calcola colonne output (input + colonne API)
  const outputColumns = useMemo(() => {
    const cols = inputColumns.map(c => ({
      name: c.name,
      type: normalizeType(c),
      visible: true,
      fromApi: false
    }));
    
    if (selectedApi?.output_mapping) {
      selectedApi.output_mapping.forEach(mapping => {
        if (!cols.find(c => c.name === mapping.column)) {
          cols.push({
            name: mapping.column,
            type: mapApiTypeToStandard(mapping.type),
            visible: true,
            fromApi: true
          });
        }
      });
    }
    
    return cols;
  }, [inputColumns, selectedApi]);
  
  // Aggiorna data.columns quando cambiano le colonne output
  useEffect(() => {
    if (outputColumns.length > 0) {
      setNodes(nodes => nodes.map(n => {
        if (n.id === id) {
          return {
            ...n,
            data: {
              ...n.data,
              columns: outputColumns
            }
          };
        }
        return n;
      }));
    }
  }, [outputColumns, id, setNodes]);
  
  // Handlers
  const handleApiChange = (apiId) => {
    const api = apiConnections.find(a => a.id === apiId);
    
    const newMapping = {};
    if (api?.parameters) {
      api.parameters.forEach(param => {
        const matchingCol = inputColumns.find(c => 
          c.name.toLowerCase() === param.name.toLowerCase()
        );
        if (matchingCol) {
          newMapping[param.name] = matchingCol.name;
        }
      });
    }
    
    setNodes(nodes => nodes.map(n => {
      if (n.id === id) {
        return {
          ...n,
          data: {
            ...n.data,
            api_id: apiId,
            param_mapping: newMapping,
            _lastUpdate: Date.now()
          }
        };
      }
      return n;
    }));
  };
  
  const handleMappingChange = (paramName, columnName) => {
    setNodes(nodes => nodes.map(n => {
      if (n.id === id) {
        return {
          ...n,
          data: {
            ...n.data,
            param_mapping: {
              ...n.data.param_mapping,
              [paramName]: columnName
            },
            _lastUpdate: Date.now()
          }
        };
      }
      return n;
    }));
  };
  
  const handleDelete = () => {
    if (data.onDelete) {
      data.onDelete(id);
    }
  };
  
  // Validazione
  const validation = useMemo(() => {
    if (inputColumns.length === 0) {
      return { valid: false, message: 'Collega una sorgente dati' };
    }
    if (!selectedApiId) {
      return { valid: false, message: 'Seleziona un\'API' };
    }
    if (selectedApi?.parameters) {
      const requiredParams = selectedApi.parameters.filter(p => p.required);
      const missingParams = requiredParams.filter(p => !paramMapping[p.name]);
      if (missingParams.length > 0) {
        return { 
          valid: false, 
          message: `Mappa: ${missingParams.map(p => p.name).join(', ')}` 
        };
      }
    }
    return { valid: true, message: 'Pronto' };
  }, [inputColumns, selectedApiId, selectedApi, paramMapping]);

  const nodeClassName = `api-enrich-node${selected ? ' api-enrich-node--selected' : ''}`;

  return (
    <div className={nodeClassName}>
      {/* Header */}
      <div className="api-enrich-node__header">
        <span className="api-enrich-node__header-icon">🔗</span>
        <span>API Enrichment</span>
        <button
          onClick={handleDelete}
          className="api-enrich-node__delete-btn"
          title="Elimina nodo"
        >
          ×
        </button>
      </div>

      {/* Body */}
      <div className="api-enrich-node__body">
        {/* Select API */}
        <div className="api-enrich-node__section">
          <label className="api-enrich-node__label">API da chiamare:</label>
          <select
            value={selectedApiId}
            onChange={(e) => handleApiChange(e.target.value)}
            className="api-enrich-node__select"
          >
            <option value="">-- Seleziona API --</option>
            {apiConnections.map(api => (
              <option key={api.id} value={api.id}>
                {api.name}
              </option>
            ))}
          </select>
        </div>

        {/* API Info */}
        {selectedApi && (
          <div className="api-enrich-node__section">
            <div className="api-enrich-node__api-info">
              <div className="api-enrich-node__api-method">
                {selectedApi.endpoint?.method} {selectedApi.endpoint?.url?.split('/').slice(-1)[0]}
              </div>
              {selectedApi.description && (
                <div className="api-enrich-node__api-desc">{selectedApi.description}</div>
              )}
            </div>
          </div>
        )}

        {/* Mapping parametri */}
        {selectedApi?.parameters && selectedApi.parameters.length > 0 && (
          <div className="api-enrich-node__section">
            <label className="api-enrich-node__label api-enrich-node__label--spacing">
              Mapping parametri:
            </label>
            
            {selectedApi.parameters.map(param => (
              <div key={param.name} className="api-enrich-node__mapping-row">
                <div className="api-enrich-node__param-info">
                  <span className={`api-enrich-node__param-badge ${
                    param.required 
                      ? 'api-enrich-node__param-badge--required' 
                      : 'api-enrich-node__param-badge--optional'
                  }`}>
                    {param.required ? 'REQ' : 'OPT'}
                  </span>
                  <span className="api-enrich-node__param-name">{param.name}</span>
                </div>
                <span className="api-enrich-node__mapping-arrow">→</span>
                <select
                  value={paramMapping[param.name] || ''}
                  onChange={(e) => handleMappingChange(param.name, e.target.value)}
                  className={`api-enrich-node__mapping-select ${
                    paramMapping[param.name] ? 'api-enrich-node__mapping-select--mapped' : ''
                  }`}
                >
                  <option value="">-- colonna --</option>
                  {inputColumns.map(col => (
                    <option key={col.name} value={col.name}>
                      {col.name} ({getColumnTypeLabel(col.type)})
                    </option>
                  ))}
                </select>
              </div>
            ))}
          </div>
        )}

        {/* Output preview */}
        {selectedApi?.output_mapping && selectedApi.output_mapping.length > 0 && (
          <div className="api-enrich-node__section">
            <div className="api-enrich-node__output-preview">
              <div className="api-enrich-node__output-title">
                📤 Colonne aggiunte dall'API:
              </div>
              <div className="api-enrich-node__output-chips">
                {selectedApi.output_mapping.map((mapping, idx) => {
                  const mappedType = mapApiTypeToStandard(mapping.type);
                  const isGeo = mappedType.startsWith('geometry');
                  return (
                    <span
                      key={idx}
                      className={`api-enrich-node__output-chip ${
                        isGeo ? 'api-enrich-node__output-chip--geo' : ''
                      }`}
                      title={getColumnTypeLabel(mappedType)}
                    >
                      {getColumnTypeIcon(mappedType)} +{mapping.column}
                    </span>
                  );
                })}
              </div>
            </div>
          </div>
        )}

        {/* Input columns preview */}
        {inputColumns.length > 0 && (
          <div className="api-enrich-node__section">
            <div className="api-enrich-node__input-preview">
              <div className="api-enrich-node__input-title">
                📥 Colonne in input: {inputColumns.length}
              </div>
              <div className="api-enrich-node__input-chips">
                {inputColumns.slice(0, 8).map((col, idx) => (
                  <span
                    key={idx}
                    className="api-enrich-node__input-chip"
                    title={getColumnTypeLabel(col.type)}
                  >
                    {getColumnTypeIcon(col.type)} {col.name}
                  </span>
                ))}
                {inputColumns.length > 8 && (
                  <span className="api-enrich-node__input-more">
                    +{inputColumns.length - 8} altre
                  </span>
                )}
              </div>
            </div>
          </div>
        )}

        {/* Validation status */}
        <div className={`api-enrich-node__status ${
          validation.valid 
            ? 'api-enrich-node__status--valid' 
            : 'api-enrich-node__status--invalid'
        }`}>
          <span className="api-enrich-node__status-icon">
            {validation.valid ? '✅' : '⚠️'}
          </span>
          <span>{validation.message}</span>
        </div>
      </div>

      {/* Handles */}
      <Handle
        type="target"
        position={Position.Left}
        className="api-enrich-node__handle--input"
      />
      <Handle
        type="source"
        position={Position.Right}
        className="api-enrich-node__handle--output"
      />
    </div>
  );
}

export default ApiEnrichNode;