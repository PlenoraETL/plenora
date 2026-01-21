/**
 * FormulaConfig.jsx - Configurazione per operazione Formula
 */
import React, { useRef } from 'react';
import { getColumnTypeIcon } from '../../../utils/columnUtils';

const FormulaConfig = ({ 
  config, 
  updateConfig, 
  validation,
  inputColumns
}) => {
  const formulaRef = useRef(null);

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

  if (!validation.valid) {
    return (
      <div className={`transform-node__message transform-node__message--${validation.type}`}>
        {validation.type === 'warning' ? '⚠️' : '❌'} {validation.message}
      </div>
    );
  }

  return (
    <div className="transform-node__config">
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

      {/* Colonne cliccabili */}
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
    </div>
  );
};

export default FormulaConfig;
