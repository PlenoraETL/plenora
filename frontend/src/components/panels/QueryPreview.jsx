/**
 * QueryPreview.jsx — Anteprima della query costruita nel canvas di Estrai.
 *
 * Fase 5, T6 (R3.13, N14): la query mostrata e' QUELLA DELL'ESECUZIONE. Il
 * backend la costruisce sul canvas corrente (anche non salvato) con lo
 * stesso piano dell'esecuzione (POST /projects/{id}/query-preview,
 * estrai/query_plan + QueryGenerator con le opzioni della connessione):
 * ORDER BY, HAVING, layer ArcGIS, tabelle non collegate, filtri 0, IN e
 * BETWEEN, alias delle colonne calcolate, filtri presi da un input runtime.
 * Qui non si ricostruisce niente: si mostra la risposta.
 *
 * La richiesta parte dopo una pausa nelle modifiche, sull'impronta del
 * contenuto (utils/canvasContentKey: spostare i nodi non la rifa'); l'id di
 * richiesta di useResource fa vincere l'ultima.
 */
import React, { useMemo, useState } from 'react';
import { useShallow } from 'zustand/react/shallow';
import useCanvasStore from '../../store/useCanvasStore';
import { previewProjectQuery } from '../../api';
import { useResource } from '../../hooks/useResource';
import { useDebouncedValue } from '../../hooks/useDebouncedValue';
import { canvasContentKey } from '../../utils/canvasContentKey';
import { serializeCanvas } from '../../utils/canvasSerializer';
import { ResourceError } from '../common/ListState';
import '../common/ListState.css';
import './QueryPreview.css';
import { term } from '../../utils/glossary';

const formatValue = (value) => {
  if (value === null || value === undefined) return 'NULL';
  if (Array.isArray(value)) return `(${value.map(formatValue).join(', ')})`;
  if (typeof value === 'string') return `'${value}'`;
  return String(value);
};

/** Una riga per parametro: valore, o «dall'input X all'avvio». */
export function paramLabel(param) {
  if (param.input_key) {
    return `${formatValue(param.value)} — dall'input «${param.input_label || param.input_key}» all'avvio`;
  }
  return formatValue(param.value);
}

function QueryPreview() {
  const [isCollapsed, setIsCollapsed] = useState(false);
  const [copySuccess, setCopySuccess] = useState(false);
  const projectId = useCanvasStore(s => s.projectId);
  // Solo id, tipo e dati dei nodi (non le posizioni): un trascinamento non
  // ricalcola l'impronta.
  const flat = useCanvasStore(useShallow(s => (s.nodes || []).flatMap(n => [n.id, n.type, n.data])));
  const edges = useCanvasStore(s => s.edges);
  const contentKey = useMemo(() => {
    const nodes = [];
    for (let i = 0; i < flat.length; i += 3) nodes.push({ id: flat[i], type: flat[i + 1], data: flat[i + 2] });
    return canvasContentKey(nodes, edges);
  }, [flat, edges]);
  const { value: settledKey } = useDebouncedValue(contentKey, 400);
  const hasNodes = useCanvasStore(s => (s.nodes || []).some(n => n.type !== 'comment'));

  const query = useResource(({ signal }) => {
    const st = useCanvasStore.getState();
    return previewProjectQuery(st.projectId, serializeCanvas(st.nodes, st.edges), { signal });
  }, {
    key: ['estrai-query-preview', projectId, settledKey],
    enabled: Boolean(projectId) && hasNodes,
    keepPreviousData: true,
  });
  const result = query.data || null;

  const sql = useMemo(() => {
    if (!hasNodes) return '-- Aggiungi tabelle al canvas per generare la query';
    if (!result) return query.initialLoading ? '-- Preparazione della query…' : '';
    if (result.kind === 'feature_service' && result.feature_service) {
      const fs = result.feature_service;
      return [
        `-- Lettura del layer ArcGIS (REST), come in esecuzione`,
        `LAYER   ${fs.layer_url}`,
        `WHERE   ${fs.where}`,
        `CAMPI   ${fs.out_fields}`,
        `GEOMETRIA ${fs.return_geometry ? 'sì' : 'no'}`,
        fs.max_features ? `LIMITE  ${fs.max_features}` : null,
      ].filter(Boolean).join('\n');
    }
    return result.sql || '';
  }, [hasNodes, result, query.initialLoading]);

  const resultInfo = useCanvasStore(s => {
    const out = (s.nodes || []).find(n => n.type === 'resultNode');
    return out ? `${out.data?.fileName || 'output'}.${out.data?.format || 'csv'}` : null;
  });

  const tablesCount = result?.kind === 'sql' ? (result.tables_used || []).length : null;
  const params = result?.params || [];

  const handleCopy = () => {
    navigator.clipboard.writeText(sql);
    setCopySuccess(true);
    setTimeout(() => setCopySuccess(false), 2000);
  };

  return (
    <div className={`query-preview ${isCollapsed ? 'query-preview--collapsed' : 'query-preview--expanded'}`}>
      <div className="query-preview__header" onClick={() => setIsCollapsed(!isCollapsed)}>
        <div className="query-preview__header-left">
          <span className="query-preview__toggle-icon">{isCollapsed ? '▲' : '▼'}</span>
          <span className="query-preview__title">🔍 {term('preview').name} della query</span>
          {tablesCount !== null && (
            <span className="query-preview__stats">
              {tablesCount === 1 ? '1 tabella' : `${tablesCount} tabelle`} nella query
            </span>
          )}
        </div>
        {!isCollapsed && sql && (
          <button type="button"
            onClick={(e) => { e.stopPropagation(); handleCopy(); }}
            className={`query-preview__copy-btn ${copySuccess ? 'query-preview__copy-btn--success' : ''}`}
          >
            {copySuccess ? '✓ Copiato!' : '📋 Copia SQL'}
          </button>
        )}
      </div>

      {!isCollapsed && (
        <div className="query-preview__content">
          <ResourceError compact resource={query} title="Query non aggiornata" />
          {result?.error && (
            <div className="query-preview__warnings">
              <div className="query-preview__warning-item">⚠️ {result.error}</div>
            </div>
          )}
          {(result?.notes || []).length > 0 && (
            <div className="query-preview__warnings">
              {result.notes.map(note => <div key={note} className="query-preview__warning-item">{note}</div>)}
            </div>
          )}

          {sql && <pre className="query-preview__sql">{highlightSQL(sql)}</pre>}

          {params.length > 0 && (
            <div className="query-preview__output-info" aria-label="Parametri della query">
              <div className="query-preview__output-label">Parametri:</div>
              <div>
                {params.map(p => (
                  <div key={p.name} className="query-preview__output-filename">
                    :{p.name} = {paramLabel(p)}
                  </div>
                ))}
              </div>
            </div>
          )}

          {resultInfo && (
            <div className="query-preview__output-info">
              <div className="query-preview__output-label">📁 Output:</div>
              <div className="query-preview__output-filename">{resultInfo}</div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/**
 * Syntax highlighting per SQL
 */
function highlightSQL(sql) {
  const keywords = [
    'SELECT', 'FROM', 'LEFT', 'RIGHT', 'INNER', 'OUTER', 'FULL', 'JOIN', 'ON', 'AS',
    'WHERE', 'GROUP BY', 'ORDER BY', 'LIMIT', 'DISTINCT', 'HAVING', 'AND', 'OR', 'IN', 'BETWEEN',
    'ASC', 'DESC', 'FETCH', 'TOP',
    'COUNT', 'SUM', 'AVG', 'MIN', 'MAX',
    'TRIM', 'LTRIM', 'RTRIM', 'UPPER', 'LOWER', 'COALESCE',
    'ST_Intersects', 'ST_Contains', 'ST_Within', 'ST_Touches', 'ST_Crosses',
    'ST_Overlaps', 'ST_Equals', 'ST_Disjoint', 'ST_Covers', 'ST_CoveredBy',
    'ST_DWithin', 'ST_Distance', 'ST_Buffer', 'ST_Centroid', 'ST_AsText',
  ];

  const lines = sql.split('\n');
  let keyCounter = 0;

  return lines.map((line, lineIdx) => {
    let parts = [line];
    keywords.forEach(keyword => {
      const newParts = [];
      parts.forEach(part => {
        if (typeof part !== 'string') { newParts.push(part); return; }
        part.split(new RegExp(`\\b(${keyword})\\b`, 'gi')).forEach((segment) => {
          if (segment.toUpperCase() === keyword.toUpperCase()) {
            newParts.push(<span key={`kw-${keyCounter++}`} className="query-preview__sql-keyword">{segment}</span>);
          } else if (segment) {
            newParts.push(segment);
          }
        });
      });
      parts = newParts;
    });
    return (
      <React.Fragment key={lineIdx}>
        {parts}
        {lineIdx < lines.length - 1 && '\n'}
      </React.Fragment>
    );
  });
}

export default QueryPreview;
