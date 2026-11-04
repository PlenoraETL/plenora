/**
 * DynamicConfig.jsx - Genera UI dinamicamente dal registry JSON
 * 
 * Questo componente legge lo schema dei parametri dal registry e genera
 * automaticamente i controlli UI appropriati.
 * 
 * Posizione: /components/transforms/DynamicConfig.jsx
 */

import React, { useMemo, useEffect } from 'react';
import ParameterInput from './ParameterInput';
import useCanvasStore from '../../store/useCanvasStore';
import { LIST_SUPERSEDES, isParameterBindingEdge } from '../../utils/runtimeBindingEdges';
import { collectExpressionRefs, rewriteExpressionRefs } from '../../utils/expressionRefs';
import './DynamicConfig.css';

const DynamicConfig = ({
  transformId,
  config = {},
  updateConfig,
  applyDerivedConfig,
  registry,
  inputColumns = [],
  leftColumns = [],
  rightColumns = [],
  geometryColumns = [],
  columnsConfirmed = false,
  targetNodeId = null,
}) => {
  // Il wrapper decide se c'e' una configurazione; tutti gli hook stanno nel
  // corpo interno, montato solo con transformConfig valido. Prima il return
  // anticipato precedeva useMemo/useEffect: scegliere un'operazione valida
  // dopo una sconosciuta mandava in crash il canvas (audit R5.6).
  const transformConfig = registry?.[transformId];
  if (!transformConfig) {
    return (
      <div className="dynamic-config dynamic-config--error">
        <span className="dynamic-config__error-icon">⚠️</span>
        <span>Operazione «{transformId}» non trovata</span>
      </div>
    );
  }
  return (
    <DynamicConfigBody
      key={transformId}
      transformId={transformId}
      transformConfig={transformConfig}
      config={config}
      updateConfig={updateConfig}
      applyDerivedConfig={applyDerivedConfig}
      registry={registry}
      inputColumns={inputColumns}
      leftColumns={leftColumns}
      rightColumns={rightColumns}
      geometryColumns={geometryColumns}
      columnsConfirmed={columnsConfirmed}
      targetNodeId={targetNodeId}
    />
  );
};

const DynamicConfigBody = ({
  transformId,
  transformConfig,
  config = {},
  updateConfig,
  applyDerivedConfig,
  registry,
  inputColumns = [],
  leftColumns = [],
  rightColumns = [],
  geometryColumns = [],
  columnsConfirmed = false,
  targetNodeId = null,
}) => {
  const { parameters = [], description } = transformConfig;
  // Le patch automatiche degli effetti sotto sono dati derivati: non devono
  // rendere il canvas "modificato" all'apertura (audit R1.8).
  const derivedConfig = applyDerivedConfig || updateConfig;

  const getParamValue = (param) => {
    if (config[param.id] !== undefined) return config[param.id];
    if (transformId === 'join' && param.id === 'left_keys' && config.left_on) {
      return [config.left_on];
    }
    if (transformId === 'join' && param.id === 'right_keys' && config.right_on) {
      return [config.right_on];
    }
    return undefined;
  };
  
  // Filtra parametri visibili (gestisce depends_on)
  // Parametri collegati a un input runtime (stringa stabile: niente
  // re-render a ogni cambio del canvas).
  const boundKey = useCanvasStore(s => (s.edges || [])
    .filter(e => e.target === targetNodeId && isParameterBindingEdge(e))
    .map(e => e.data?.target_parameter || String(e.targetHandle || '').replace(/^parameter:/, ''))
    .sort().join(','));
  // Con un parametro singolo collegato, la lista che lo sostituirebbe non si
  // mostra: aggiungerla annullerebbe il collegamento (runtimeBindingEdges).
  const hiddenList = useMemo(() => {
    const rule = LIST_SUPERSEDES[transformId];
    const bound = new Set(boundKey ? boundKey.split(',') : []);
    return rule && rule.params.some(p => bound.has(p)) ? rule.list : null;
  }, [transformId, boundKey]);

  const visibleParams = useMemo(() => {
    return parameters.filter(param => {
      if (param.id === hiddenList) return false;
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
  }, [parameters, config, hiddenList]);
  
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

  const columnKey = (col) => col?.id || col?.column_id || null;

  const collectRefs = (expression) => collectExpressionRefs(expression, inputColumns);

  // Handler per aggiornamento parametro
  const handleParamChange = (param, value, meta = null) => {
    if (updateConfig) {
      const patch = { [param.id]: value };
      if (param.type === 'column' || param.type === 'geometry_column') {
        patch[`${param.id}_column_id`] = columnKey(meta);
      } else if (param.type === 'columns' || param.type === 'column_order') {
        patch[`${param.id}_column_ids`] = Array.isArray(meta)
          ? meta.map(columnKey).filter(Boolean)
          : [];
      } else if (param.type === 'expression') {
        patch[`${param.id}_column_refs`] = collectRefs(value);
      }
      updateConfig(patch);
    }
  };

  // Auto-select geometry_column quando esiste una sola candidata e
  // l'utente non ne ha ancora scelta una. Senza, op_geo_buffer e
  // simili partono con `geom_column=None` → fallback `_auto_detect`
  // backend che logga "UTM zone guard CRITICAL" e calcola buffer su
  // un CRS sbagliato.
  useEffect(() => {
    if (!updateConfig || !columnsConfirmed) return;
    for (const param of visibleParams) {
      if (param.type !== 'geometry_column') continue;
      const current = config[param.id];
      if (current) continue;
      const candidates = getColumnsForParam(param);
      if (candidates.length === 1) {
        derivedConfig({
          [param.id]: candidates[0].name,
          [`${param.id}_column_id`]: columnKey(candidates[0]),
        });
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visibleParams, geometryColumns, inputColumns, leftColumns, rightColumns, config, columnsConfirmed]);

  useEffect(() => {
    if (!updateConfig || !columnsConfirmed) return;
    const patch = {};
    for (const param of visibleParams) {
      const columnsForParam = getColumnsForParam(param);
      if ((param.type === 'column' || param.type === 'geometry_column') && config[param.id] && !config[`${param.id}_column_id`]) {
        const match = columnsForParam.find(col => col.name === config[param.id]);
        const key = columnKey(match);
        if (key) patch[`${param.id}_column_id`] = key;
      }
      if ((param.type === 'columns' || param.type === 'column_order') && Array.isArray(config[param.id]) && !config[`${param.id}_column_ids`]) {
        const ids = config[param.id]
          .map(name => columnKey(columnsForParam.find(col => col.name === name)))
          .filter(Boolean);
        if (ids.length) patch[`${param.id}_column_ids`] = ids;
      }
      if (param.type === 'expression' && config[param.id] && !config[`${param.id}_column_refs`]) {
        const refs = collectRefs(config[param.id]);
        if (refs.length) patch[`${param.id}_column_refs`] = refs;
      }
    }
    if (Object.keys(patch).length > 0) {
      derivedConfig(patch);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visibleParams, inputColumns, leftColumns, rightColumns, geometryColumns, columnsConfirmed]);

  useEffect(() => {
    if (!updateConfig || !columnsConfirmed) return;

    const patch = {};
    for (const param of visibleParams) {
      if (param.type !== 'expression') continue;
      const expression = config[param.id];
      if (!expression || typeof expression !== 'string') continue;

      const refsKey = `${param.id}_column_refs`;
      const refs = Array.isArray(config[refsKey]) ? config[refsKey] : [];
      // Regola unica (utils/expressionRefs): si riscrive solo cio' che si
      // risolve in modo univoco, per id; mai `name` -> `name_R` a valle di
      // un Join con nomi doppi.
      const nextExpression = rewriteExpressionRefs(expression, refs, getColumnsForParam(param));

      const refreshedRefs = collectRefs(nextExpression);
      if (nextExpression !== expression) {
        patch[param.id] = nextExpression;
      }
      if (JSON.stringify(refreshedRefs) !== JSON.stringify(refs)) {
        patch[refsKey] = refreshedRefs;
      }
    }

    if (Object.keys(patch).length > 0) {
      derivedConfig(patch);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visibleParams, inputColumns, leftColumns, rightColumns, geometryColumns, config, columnsConfirmed]);
  
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
                value={getParamValue(param)}
                onChange={(value, meta) => handleParamChange(param, value, meta)}
                columns={getColumnsForParam(param)}
                targetNodeId={targetNodeId}
                transformDefinition={transformConfig}
              />
            );
          })}
        </div>
      )}
    </div>
  );
};

export default DynamicConfig;
