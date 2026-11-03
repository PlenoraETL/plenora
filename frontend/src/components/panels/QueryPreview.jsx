/**
 * QueryPreview.jsx — Preview SQL della query costruita nel canvas
 *
 * Pannello collassabile in fondo all'editor Estrai che mostra
 * la SQL generata dai nodi del canvas in tempo reale.
 * Evidenzia gli errori di validazione con indicatori visivi.
 */
import React, { useState } from 'react';
import { dataFlowEdges } from '../../utils/runtimeBindingEdges';
import { isColumnVisible } from '../../utils/columnUtils';
import './QueryPreview.css';

/**
 * Componente per visualizzare preview SQL della query
 */
function QueryPreview({ nodes, edges, validationErrors = [] }) {
  const [isCollapsed, setIsCollapsed] = useState(false);
  const [copySuccess, setCopySuccess] = useState(false);

  /**
   * Genera SQL dalla configurazione canvas
   */
  const generateSQL = () => {
    if (nodes.length === 0) {
      return { 
        sql: '-- Aggiungi tabelle al canvas per generare la query',
        errors: []
      };
    }

    // Helper: wrappa espressione colonna con funzione opzionale
    const wrapColWithFunc = (expr, func) => {
      if (!func) return expr;
      if (func === 'TRIM') return `TRIM(${expr})`;
      if (func === 'LTRIM_RTRIM') return `LTRIM(RTRIM(${expr}))`;
      if (func === 'UPPER') return `UPPER(${expr})`;
      if (func === 'LOWER') return `LOWER(${expr})`;
      if (func === 'COALESCE_EMPTY') return `COALESCE(${expr}, '')`;
      return expr;
    };

    const lines = [];
    const errors = [];
    const flowEdges = dataFlowEdges(edges);

    // Trova nodo risultato
    const resultNode = nodes.find(n => n.type === 'resultNode');
    const useDistinct = resultNode?.data?.distinct || false;
    
    // ✅ GROUP BY configuration
    const groupByEnabled = resultNode?.data?.group_by_enabled || false;
    const groupByColumns = resultNode?.data?.group_by_columns || [];
    const aggregations = resultNode?.data?.aggregations || [];

    // SELECT clause
    if (groupByEnabled && (groupByColumns.length > 0 || aggregations.length > 0)) {
      // SELECT con GROUP BY
      lines.push('SELECT');
      const selectColumns = [];
      
      // Colonne di raggruppamento
      groupByColumns.forEach(col => {
        selectColumns.push(`  ${col}`);
      });
      
      // Aggregazioni
      aggregations.forEach(agg => {
        const colExpr = agg.column === '*' ? '*' : agg.column;
        const alias = agg.alias || `${agg.function.toLowerCase()}_${agg.column}`;
        selectColumns.push(`  ${agg.function}(${colExpr}) AS ${alias}`);
      });
      
      if (selectColumns.length === 0) {
        lines.push('  *  -- Configura GROUP BY');
      } else {
        lines.push(selectColumns.join(',\n'));
      }
    } else {
      // SELECT normale
      lines.push(useDistinct ? 'SELECT DISTINCT' : 'SELECT');
      const selectColumns = [];
      
      nodes.forEach(node => {
        if (node.type !== 'tableNode' && node.type !== 'datasourceNode') return;
        if (node.type === 'datasourceNode' && node.data?.mode !== 'source') return;
        
        // Per datasourceNode usa l'id come alias tabella (viene materializzato come CTE)
        const tableName = node.type === 'datasourceNode'
          ? (node.data?.datasource_name || node.id).replace(/[^a-zA-Z0-9_]/g, '_')
          : (node.data.alias || node.data.table_name);
        
        // Colonne normali visibili
        const visibleColumns = node.data.columns?.filter(isColumnVisible) || [];
        visibleColumns.forEach(col => {
          const columnExpr = col.alias 
            ? `  ${tableName}.${col.name} AS ${col.alias}`
            : `  ${tableName}.${col.name}`;
          selectColumns.push(columnExpr);
        });
        
        // Colonne calcolate
        const calculatedColumns = node.data.calculated_columns || [];
        calculatedColumns.forEach(col => {
          const sqlExpr = col.sql || `-- ${col.name} (colonna calcolata)`;
          selectColumns.push(`  ${sqlExpr}`);
        });
      });

      if (selectColumns.length === 0) {
        lines.push('  *  -- Nessuna colonna selezionata');
      } else {
        lines.push(selectColumns.join(',\n'));
      }
    }

    // FROM clause - trova la prima tabella (nodo senza edges in ingresso da altre tabelle)
    // Include sia tableNode che datasourceNode in modalità sorgente
    const sourceNodeTypes = ['tableNode', 'datasourceNode'];
    const tableNodes = nodes.filter(n =>
      sourceNodeTypes.includes(n.type) &&
      !(n.type === 'datasourceNode' && n.data?.mode !== 'source')
    );
    const targetNodeIds = new Set(flowEdges.filter(e => {
      const targetNode = nodes.find(n => n.id === e.target);
      return targetNode && sourceNodeTypes.includes(targetNode.type);
    }).map(e => e.target));
    
    // Prima tabella = quella che non è target di nessun join (o la prima se tutte sono target)
    const firstNode = tableNodes.find(n => !targetNodeIds.has(n.id)) || tableNodes[0];
    
    if (firstNode) {
      let fromTable, fromAlias;
      if (firstNode.type === 'datasourceNode') {
        // CTE generata dal backend: nome basato su datasource_name o id
        fromTable = (firstNode.data?.datasource_name || firstNode.id).replace(/[^a-zA-Z0-9_]/g, '_');
        fromAlias = null;
        lines.push(`-- DATA SOURCE: ${firstNode.data?.datasource_name || '?'} → ${firstNode.data?.file_path || firstNode.data?.folder_path || '/'}`);
      } else {
        fromTable = firstNode.data.table_name;
        fromAlias = firstNode.data.alias;
      }
      lines.push('FROM ' + fromTable + (fromAlias ? ` AS ${fromAlias}` : ''));
    }

    // JOIN clauses
    flowEdges.forEach(edge => {
      const sourceNode = nodes.find(n => n.id === edge.source);
      const targetNode = nodes.find(n => n.id === edge.target);
      
      if (!sourceNode || !targetNode) {
        errors.push(`⚠️ Nodo non trovato per join`);
        return;
      }

      // Skip edge verso nodi destinazione (resultNode, carica, datasourceNode carica)
      if (
        targetNode.type === 'resultNode' ||
        targetNode.type === 'carica' ||
        edge.data?.join_type === 'output' ||
        edge.data?.join_type === 'carica' ||
        (targetNode.type === 'datasourceNode' && targetNode.data?.mode === 'carica')
      ) {
        return;
      }

      const joinType = (edge.data?.join_type || 'left').toUpperCase();

      // Helper per ricavare il nome tabella in SQL (supporta sia tableNode che datasourceNode)
      const getNodeSqlName = (n) => {
        if (n.type === 'datasourceNode') {
          return (n.data?.datasource_name || n.id).replace(/[^a-zA-Z0-9_]/g, '_');
        }
        return n.data?.alias || n.data?.table_name;
      };

      const targetTable = targetNode.type === 'datasourceNode'
        ? getNodeSqlName(targetNode)
        : targetNode.data.table_name;
      const targetAlias = targetNode.type === 'datasourceNode' ? null : targetNode.data.alias;
      const sourceTableName = getNodeSqlName(sourceNode);
      const targetTableName = getNodeSqlName(targetNode);
      
      // ✅ Controlla se è un join spaziale
      const isSpatial = edge.data?.is_spatial || false;
      
      if (isSpatial) {
        // Join spaziale
        const spatialPredicate = edge.data?.spatial_predicate || 'ST_Intersects';
        const sourceGeomCol = edge.data?.source_geom_column || 'geom';
        const targetGeomCol = edge.data?.target_geom_column || 'geom';
        const spatialDistance = edge.data?.spatial_distance;
        
        lines.push(`${joinType} JOIN ${targetTable}${targetAlias ? ` AS ${targetAlias}` : ''}`);
        
        // Costruisci la condizione spaziale
        if (spatialPredicate === 'ST_DWithin' && spatialDistance) {
          lines.push(`  ON ${spatialPredicate}(${sourceTableName}.${sourceGeomCol}, ${targetTableName}.${targetGeomCol}, ${spatialDistance})`);
        } else {
          lines.push(`  ON ${spatialPredicate}(${sourceTableName}.${sourceGeomCol}, ${targetTableName}.${targetGeomCol})`);
        }
      } else {
        // Join normale (attributi) - supporta condizioni multiple (AND)
        const joinConditions = edge.data?.join_conditions;
        
        if (joinConditions && joinConditions.length > 0) {
          // Nuovo formato: condizioni multiple
          const validConditions = joinConditions.filter(c => c.source_column && c.target_column);
          
          if (validConditions.length === 0) {
            errors.push(`⚠️ Join incompleto: ${sourceTableName} → ${targetTableName}`);
            lines.push(`${joinType} JOIN ${targetTable}${targetAlias ? ` AS ${targetAlias}` : ''}`);
            lines.push(`  ON ... -- CONFIGURAZIONE JOIN MANCANTE`);
          } else {
            lines.push(`${joinType} JOIN ${targetTable}${targetAlias ? ` AS ${targetAlias}` : ''}`);
            validConditions.forEach((cond, idx) => {
              const prefix = idx === 0 ? '  ON ' : '  AND ';
              const srcExpr = wrapColWithFunc(`${sourceTableName}.${cond.source_column}`, cond.source_func);
              const tgtExpr = wrapColWithFunc(`${targetTableName}.${cond.target_column}`, cond.target_func);
              lines.push(`${prefix}${srcExpr} = ${tgtExpr}`);
            });
            // Filtri ON aggiuntivi
            const onFilters = edge.data?.on_filters || [];
            onFilters.forEach(f => {
              if (!f.column) return;
              const colExpr = wrapColWithFunc(`${targetTableName}.${f.column}`, f.func);
              
              if (f.operator === 'IS NULL' || f.operator === 'IS NOT NULL') {
                lines.push(`  AND ${colExpr} ${f.operator}`);
              } else {
                lines.push(`  AND ${colExpr} ${f.operator} '${f.value || ''}'`);
              }
            });
          }
        } else {
          // Backward compatibility: singola coppia
          const sourceCol = edge.data?.source_column;
          const targetCol = edge.data?.target_column;

          if (!sourceCol || !targetCol) {
            errors.push(`⚠️ Join incompleto: ${sourceTableName} → ${targetTableName}`);
            lines.push(`${joinType} JOIN ${targetTable}${targetAlias ? ` AS ${targetAlias}` : ''}`);
            lines.push(`  ON ... -- CONFIGURAZIONE JOIN MANCANTE`);
          } else {
            lines.push(`${joinType} JOIN ${targetTable}${targetAlias ? ` AS ${targetAlias}` : ''}`);
            lines.push(`  ON ${sourceTableName}.${sourceCol} = ${targetTableName}.${targetCol}`);
            // Filtri ON aggiuntivi (anche per formato legacy)
            const onFilters = edge.data?.on_filters || [];
            onFilters.forEach(f => {
              if (!f.column) return;
              const colExpr = wrapColWithFunc(`${targetTableName}.${f.column}`, f.func);
              
              if (f.operator === 'IS NULL' || f.operator === 'IS NOT NULL') {
                lines.push(`  AND ${colExpr} ${f.operator}`);
              } else {
                lines.push(`  AND ${colExpr} ${f.operator} '${f.value || ''}'`);
              }
            });
          }
        }
      }
    });

    // WHERE clause
    const whereConditions = [];
    
    nodes.forEach(node => {
      if (node.type === 'tableNode' && node.data.filters && node.data.filters.length > 0) {
        const tableName = node.data.alias || node.data.table_name;
        
        node.data.filters.forEach(filter => {
          if (filter.operator === 'IS NULL' || filter.operator === 'IS NOT NULL') {
            whereConditions.push(`${tableName}.${filter.column} ${filter.operator}`);
          } else if (filter.value) {
            const needsQuotes = ['LIKE', '=', '!='].includes(filter.operator);
            const valueStr = needsQuotes ? `'${filter.value}'` : filter.value;
            whereConditions.push(`${tableName}.${filter.column} ${filter.operator} ${valueStr}`);
          }
        });
      }
    });

    if (whereConditions.length > 0) {
      lines.push('WHERE ' + whereConditions[0]);
      whereConditions.slice(1).forEach(condition => {
        lines.push('  AND ' + condition);
      });
    }

    // ✅ GROUP BY clause
    if (groupByEnabled && groupByColumns.length > 0) {
      lines.push('GROUP BY ' + groupByColumns.join(', '));
    }

    // LIMIT clause
    const limitValue = resultNode?.data?.limit;
    if (limitValue && limitValue > 0) {
      lines.push(`LIMIT ${limitValue}`);
    }

    return { sql: lines.join('\n'), errors };
  };

  const { sql, errors } = generateSQL();

  // Info output
  const resultNode = nodes.find(n => n.type === 'resultNode');
  const outputInfo = resultNode ? {
    fileName: resultNode.data?.fileName || 'output',
    format: resultNode.data?.format || 'csv'
  } : null;

  const handleCopy = () => {
    navigator.clipboard.writeText(sql);
    setCopySuccess(true);
    setTimeout(() => setCopySuccess(false), 2000);
  };

  return (
    <div className={`query-preview ${isCollapsed ? 'query-preview--collapsed' : 'query-preview--expanded'}`}>
      {/* Header */}
      <div 
        className="query-preview__header"
        onClick={() => setIsCollapsed(!isCollapsed)}
      >
        <div className="query-preview__header-left">
          <span className="query-preview__toggle-icon">
            {isCollapsed ? '▲' : '▼'}
          </span>
          <span className="query-preview__title">🔍 Query Preview</span>
          <span className="query-preview__stats">
            {nodes.length} tabelle, {edges.length} join
          </span>
        </div>

        {!isCollapsed && (
          <button type="button"
            onClick={(e) => {
              e.stopPropagation();
              handleCopy();
            }}
            className={`query-preview__copy-btn ${copySuccess ? 'query-preview__copy-btn--success' : ''}`}
          >
            {copySuccess ? '✓ Copiato!' : '📋 Copia SQL'}
          </button>
        )}
      </div>

      {/* Content */}
      {!isCollapsed && (
        <div className="query-preview__content">
          {/* Errori di validazione */}
          {validationErrors && validationErrors.length > 0 && (
            <div className="query-preview__errors">
              <div className="query-preview__errors-header">
                <span className="query-preview__errors-icon">⛔</span>
                Errori di validazione ({validationErrors.length})
              </div>
              {validationErrors.map((err, idx) => (
                <div key={idx} className="query-preview__error-item">
                  {idx + 1}. {err}
                </div>
              ))}
            </div>
          )}

          {/* Warning SQL */}
          {errors && errors.length > 0 && validationErrors.length === 0 && (
            <div className="query-preview__warnings">
              {errors.map((err, idx) => (
                <div key={idx} className="query-preview__warning-item">
                  {err}
                </div>
              ))}
            </div>
          )}

          {/* SQL */}
          <pre className="query-preview__sql">
            {highlightSQL(sql)}
          </pre>

          {/* Output info */}
          {outputInfo && (
            <div className="query-preview__output-info">
              <div className="query-preview__output-label">📁 Output:</div>
              <div className="query-preview__output-filename">
                {outputInfo.fileName}.{outputInfo.format}
              </div>
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
    'WHERE', 'GROUP BY', 'ORDER BY', 'LIMIT', 'DISTINCT', 'HAVING',
    // Funzioni aggregate
    'COUNT', 'SUM', 'AVG', 'MIN', 'MAX',
    // Funzioni di trasformazione
    'TRIM', 'LTRIM', 'RTRIM', 'UPPER', 'LOWER', 'COALESCE',
    // Funzioni spaziali PostGIS
    'ST_Intersects', 'ST_Contains', 'ST_Within', 'ST_Touches', 'ST_Crosses',
    'ST_Overlaps', 'ST_Equals', 'ST_Disjoint', 'ST_Covers', 'ST_CoveredBy',
    'ST_DWithin', 'ST_Distance', 'ST_Buffer', 'ST_Centroid'
  ];
  
  const lines = sql.split('\n');
  let keyCounter = 0;
  
  return lines.map((line, lineIdx) => {
    let parts = [line];
    
    keywords.forEach(keyword => {
      const newParts = [];
      parts.forEach(part => {
        if (typeof part === 'string') {
          const segments = part.split(new RegExp(`\\b(${keyword})\\b`, 'gi'));
          segments.forEach((segment) => {
            if (segment.toUpperCase() === keyword.toUpperCase()) {
              newParts.push(
                <span key={`kw-${keyCounter++}`} className="query-preview__sql-keyword">
                  {segment}
                </span>
              );
            } else if (segment) {
              newParts.push(segment);
            }
          });
        } else {
          newParts.push(part);
        }
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
