/**
 * DynamicConfig.jsx - Genera UI dinamicamente dal registry JSON
 * 
 * Questo componente legge lo schema dei parametri dal registry e genera
 * automaticamente i controlli UI appropriati.
 * 
 * Posizione: /components/transforms/DynamicConfig.jsx
 */

import React, { useMemo } from 'react';
import ParameterInput from './ParameterInput';
import './DynamicConfig.css';

const DynamicConfig = ({
  transformId,
  config = {},
  updateConfig,
  registry,
  inputColumns = [],
  leftColumns = [],
  rightColumns = [],
  geometryColumns = []
}) => {
  
  // Ottieni configurazione trasformazione dal registry
  const transformConfig = registry?.[transformId];
  
  if (!transformConfig) {
    return (
      <div className="dynamic-config dynamic-config--error">
        <span className="dynamic-config__error-icon">⚠️</span>
        <span>Trasformazione "{transformId}" non trovata</span>
      </div>
    );
  }
  
  const { parameters = [], description } = transformConfig;
  
  // Filtra parametri visibili (gestisce depends_on)
  const visibleParams = useMemo(() => {
    return parameters.filter(param => {
      if (!param.depends_on) return true;
      
      const { param: depParam, value: depValue } = param.depends_on;
      
      // Trova il parametro da cui dipende per ottenere il suo valore di default
      const dependentParam = parameters.find(p => p.id === depParam);
      const defaultValue = dependentParam?.default;
      
      // Usa il valore corrente dal config, oppure il default
      const currentValue = config[depParam] !== undefined ? config[depParam] : defaultValue;
      
      // Supporta array di valori possibili
      if (Array.isArray(depValue)) {
        return depValue.includes(currentValue);
      }
      
      return currentValue === depValue;
    });
  }, [parameters, config]);
  
  // Determina colonne disponibili per ogni parametro
  const getColumnsForParam = (param) => {
    // Se il parametro specifica una source (left/right)
    if (param.source === 'left' && leftColumns.length > 0) {
      return leftColumns;
    }
    if (param.source === 'right' && rightColumns.length > 0) {
      return rightColumns;
    }
    
    // Se è geometry_column, filtra solo geometrie
    if (param.type === 'geometry_column') {
      if (geometryColumns.length > 0) {
        return geometryColumns;
      }
      // Fallback: filtra da inputColumns
      return inputColumns.filter(col => 
        col.type?.toLowerCase().includes('geometry') ||
        col.name?.toLowerCase().includes('geom')
      );
    }
    
    // Se ha filtro specifico
    if (param.filter) {
      return inputColumns.filter(col => {
        const colType = col.type?.toLowerCase() || '';
        switch (param.filter) {
          case 'numeric':
            return ['int', 'real', 'float', 'double', 'number', 'integer'].some(t => colType.includes(t));
          case 'text':
            return ['str', 'text', 'varchar', 'char'].some(t => colType.includes(t));
          case 'geometry':
            return colType.includes('geometry');
          default:
            return true;
        }
      });
    }
    
    return inputColumns;
  };
  
  // Handler per aggiornamento parametro
  const handleParamChange = (paramId, value) => {
    if (updateConfig) {
      updateConfig(paramId, value);
    }
  };
  
  return (
    <div className="dynamic-config">
      {/* Descrizione trasformazione */}
      {description && (
        <div className="dynamic-config__description">
          {description}
        </div>
      )}
      
      {/* Parametri */}
      {visibleParams.length === 0 ? (
        <div className="dynamic-config__empty">
          Nessun parametro richiesto
        </div>
      ) : (
        <div className="dynamic-config__params">
          {visibleParams.map(param => {
            // Aggiungi indicatore SX/DX al label per parametri con source
            const enrichedParam = param.source 
              ? { 
                  ...param, 
                  label: param.source === 'left' 
                    ? `⬅ ${param.label}` 
                    : `➡ ${param.label}`
                }
              : param;
            return (
              <ParameterInput
                key={param.id}
                param={enrichedParam}
                value={config[param.id]}
                onChange={(value) => handleParamChange(param.id, value)}
                columns={getColumnsForParam(param)}
              />
            );
          })}
        </div>
      )}
    </div>
  );
};

export default DynamicConfig;