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
 * contenuto (utils/canvasContentKey: spostare i nodi non la rifa'); la
 * richiesta superata si annulla (segnale di useResource) e vince l'ultima.
 */
import React, { useMemo, useState } from 'react';
import useCanvasStore from '../../store/useCanvasStore';
import { previewProjectQuery } from '../../api';
import { useResource } from '../../hooks/useResource';
import { useDebouncedValue } from '../../hooks/useDebouncedValue';
import { currentCanvasContentKey } from '../../hooks/usePreviewStale';
import { useCanvasDerived } from '../../hooks/useCanvasDerived';
import { serializeCanvas } from '../../utils/canvasSerializer';
import { ResourceError } from '../common/ListState';
import '../common/ListState.css';
import './QueryPreview.css';
import { term } from '../../utils/glossary';
import { pressable } from '../../utils/pressable';

// Pausa dopo l'ultima modifica prima di chiedere la query.
const QUERY_PREVIEW_DELAY_MS = 400;

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
  const graphLoaded = useCanvasStore(s => s.graphLoaded);
  // UI-2 (V7, C14): si aspetta la pausa sul CONTATORE del contenuto (store/
  // graphRevisions, un numero), poi si legge l'impronta una volta sola: la
  // stessa, memorizzata, della mappa delle colonne (utils/canvasContentKey).
  // Niente serializzazione a ogni tasto ne' a ogni fotogramma.
  const contentRev = useCanvasStore(s => s.contentRev);
  const { value: settledRev } = useDebouncedValue(contentRev, QUERY_PREVIEW_DELAY_MS);
  // Anche all'apertura (graphLoaded): la prima richiesta porta gia' la
  // firma del canvas caricato, e la pausa dopo non la ripete.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const settledKey = useMemo(() => currentCanvasContentKey(), [settledRev, projectId, graphLoaded]);
  const hasNodes = useCanvasDerived(s => (s.nodes || []).some(n => n.type !== 'comment'), { on: 'structure' });

  const query = useResource(({ signal }) => {
    const st = useCanvasStore.getState();
    return previewProjectQuery(st.projectId, serializeCanvas(st.nodes, st.edges), { signal });
  }, {
    key: ['estrai-query-preview', projectId, settledKey],
    enabled: Boolean(projectId) && graphLoaded && hasNodes,
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
  const highlighted = useMemo(() => (sql ? highlightSQL(sql) : null), [sql]);

  const resultInfo = useCanvasDerived(s => {
    const out = (s.nodes || []).find(n => n.type === 'resultNode');
    return out ? `${out.data?.fileName || 'output'}.${out.data?.format || 'csv'}` : null;
  });

  const tablesCount = result?.kind === 'sql' ? (result.tables_used || []).length : null;
  const params = result?.params || [];

  // P9 (Fase 5 T7): la clipboard c'e' solo in HTTPS/localhost; «Copiato»
  // solo se la copia e' riuscita, altrimenti lo si dice.
  const handleCopy = async () => {
    let ok = false;
    try {
      if (navigator.clipboard?.writeText) { await navigator.clipboard.writeText(sql); ok = true; }
    } catch { ok = false; }
    setCopySuccess(ok ? 'ok' : 'fail');
    setTimeout(() => setCopySuccess(false), 2000);
  };

  return (
    <div className={`query-preview ${isCollapsed ? 'query-preview--collapsed' : 'query-preview--expanded'}`}>
      <div className="query-preview__header" {...pressable(() => setIsCollapsed(!isCollapsed))}>
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
            className={`query-preview__copy-btn ${copySuccess === 'ok' ? 'query-preview__copy-btn--success' : ''}`}
          >
            {copySuccess === 'ok' ? '✓ Copiato!' : copySuccess === 'fail' ? 'Copia non disponibile: seleziona il testo' : '📋 Copia SQL'}
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

          {sql && <pre className="query-preview__sql">{highlighted}</pre>}

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
 * Syntax highlighting per SQL. perf-2 (Fase 5 T7): UNA espressione regolare
 * compilata una volta (prima ~40 regex per riga a ogni render) e il
 * risultato memorizzato per testo (useMemo al chiamante).
 */
const SQL_KEYWORDS = [
    'SELECT', 'FROM', 'LEFT', 'RIGHT', 'INNER', 'OUTER', 'FULL', 'JOIN', 'ON', 'AS',
    'WHERE', 'GROUP BY', 'ORDER BY', 'LIMIT', 'DISTINCT', 'HAVING', 'AND', 'OR', 'IN', 'BETWEEN',
    'ASC', 'DESC', 'FETCH', 'TOP',
    'COUNT', 'SUM', 'AVG', 'MIN', 'MAX',
    'TRIM', 'LTRIM', 'RTRIM', 'UPPER', 'LOWER', 'COALESCE',
    'ST_Intersects', 'ST_Contains', 'ST_Within', 'ST_Touches', 'ST_Crosses',
    'ST_Overlaps', 'ST_Equals', 'ST_Disjoint', 'ST_Covers', 'ST_CoveredBy',
    'ST_DWithin', 'ST_Distance', 'ST_Buffer', 'ST_Centroid', 'ST_AsText',
  ];
const KEYWORD_RE = new RegExp(`\\b(${[...SQL_KEYWORDS].sort((x, y) => y.length - x.length).join('|')})\\b`, 'gi');
const KEYWORD_SET = new Set(SQL_KEYWORDS.map(k => k.toUpperCase()));

function highlightSQL(sql) {
  const lines = sql.split('\n');
  return lines.map((line, lineIdx) => (
    <React.Fragment key={lineIdx}>
      {line.split(KEYWORD_RE).map((segment, i) => (
        KEYWORD_SET.has(segment.toUpperCase()) && i % 2 === 1
          ? <span key={i} className="query-preview__sql-keyword">{segment}</span>
          : segment
      ))}
      {lineIdx < lines.length - 1 && '\n'}
    </React.Fragment>
  ));
}

export default QueryPreview;
