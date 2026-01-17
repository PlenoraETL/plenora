import React, { useState } from 'react';
import '../styles/QueryPreview.css';

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

    const lines = [];
    const errors = [];

    // Trova nodo risultato per DISTINCT
    const resultNode = nodes.find(n => n.type === 'resultNode');
    const useDistinct = resultNode?.data?.distinct || false;

    // SELECT clause
    lines.push(useDistinct ? 'SELECT DISTINCT' : 'SELECT');
    const selectColumns = [];
    
    nodes.forEach(node => {
      if (node.type !== 'tableNode') return;
      
      const tableName = node.data.alias || node.data.table_name;
      
      // Colonne normali visibili
      const visibleColumns = node.data.columns?.filter(col => col.visible) || [];
      visibleColumns.forEach(col => {
        const columnExpr = col.alias 
          ? `  ${tableName}.${col.name} AS ${col.alias}`
          : `  ${tableName}.${col.name}`;
        selectColumns.push(columnExpr);
      });
      
      // ✅ Colonne calcolate
      const calculatedColumns = node.data.calculated_columns || [];
      calculatedColumns.forEach(col => {
        // Mostra SQL se disponibile, altrimenti placeholder
        const sqlExpr = col.sql || `-- ${col.name} (colonna calcolata)`;
        selectColumns.push(`  ${sqlExpr}`);
      });
    });

    if (selectColumns.length === 0) {
      lines.push('  *  -- Nessuna colonna selezionata');
    } else {
      lines.push(selectColumns.join(',\n'));
    }

    // FROM clause
    const firstNode = nodes[0];
    if (firstNode) {
      const fromTable = firstNode.data.table_name;
      const fromAlias = firstNode.data.alias;
      lines.push('FROM ' + fromTable + (fromAlias ? ` AS ${fromAlias}` : ''));
    }

    // JOIN clauses
    edges.forEach(edge => {
      const sourceNode = nodes.find(n => n.id === edge.source);
      const targetNode = nodes.find(n => n.id === edge.target);
      
      if (!sourceNode || !targetNode) {
        errors.push(`⚠️ Nodo non trovato per join`);
        return;
      }

      if (targetNode.type === 'resultNode') {
        return;
      }

      const joinType = (edge.data?.join_type || 'left').toUpperCase();
      const targetTable = targetNode.data.table_name;
      const targetAlias = targetNode.data.alias;
      const sourceTableName = sourceNode.data.alias || sourceNode.data.table_name;
      const targetTableName = targetNode.data.alias || targetNode.data.table_name;
      
      const sourceCol = edge.data?.source_column;
      const targetCol = edge.data?.target_column;

      if (!sourceCol || !targetCol) {
        errors.push(`⚠️ Join incompleto: ${sourceTableName} → ${targetTableName}`);
        lines.push(`${joinType} JOIN ${targetTable}${targetAlias ? ` AS ${targetAlias}` : ''}`);
        lines.push(`  ON ... -- CONFIGURAZIONE JOIN MANCANTE`);
      } else {
        lines.push(`${joinType} JOIN ${targetTable}${targetAlias ? ` AS ${targetAlias}` : ''}`);
        lines.push(`  ON ${sourceTableName}.${sourceCol} = ${targetTableName}.${targetCol}`);
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
          <button
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
  const keywords = ['SELECT', 'FROM', 'LEFT', 'RIGHT', 'INNER', 'OUTER', 'FULL', 'JOIN', 'ON', 'AS', 'WHERE', 'GROUP BY', 'ORDER BY', 'LIMIT', 'DISTINCT'];
  
  const lines = sql.split('\n');
  
  return lines.map((line, lineIdx) => {
    let parts = [line];
    
    keywords.forEach(keyword => {
      const newParts = [];
      parts.forEach(part => {
        if (typeof part === 'string') {
          const segments = part.split(new RegExp(`\\b(${keyword})\\b`, 'g'));
          segments.forEach((segment, i) => {
            if (segment === keyword) {
              newParts.push(
                <span key={`${lineIdx}-${keyword}-${i}`} className="query-preview__sql-keyword">
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