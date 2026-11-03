/**
 * StaticValuesNode.jsx — Nodo canvas per valori statici con template
 * Layout inline: [nome] [valore] [tipo] [×] per ogni campo
 */
import React, { useState, useCallback, useMemo, useRef, memo } from 'react';
import { useEffect } from 'react';
import { useToast } from '../../hooks/useToast';
import { Handle, Position } from 'reactflow';
import { 
  processTemplateVariables, 
  hasTemplateVariables, 
  AVAILABLE_VARIABLES 
} from '../../utils/templateVariables';
import useCanvasStore from '../../store/useCanvasStore';
import './StaticValuesNode.css';

const FIELD_TYPES = [
  { value: 'string',  label: 'Testo',    icon: 'T' },
  { value: 'integer', label: 'Intero',   icon: '№' },
  { value: 'float',   label: 'Decimale', icon: '#' },
  { value: 'boolean', label: 'Bool',     icon: '✓' },
];

const TYPE_COLORS = {
  string:  { bg: 'rgba(75,139,255,0.1)',  color: '#4B8BFF' },
  integer: { bg: 'rgba(52,211,153,0.1)',  color: '#34D399' },
  float:   { bg: 'rgba(251,191,36,0.1)',  color: '#FBBF24' },
  boolean: { bg: 'rgba(155,109,255,0.1)', color: '#9B6DFF' },
};

function StaticValuesNode({ id, data, selected }) {
  const toast = useToast();
  const updateNodeData = useCanvasStore(s => s.updateNodeData);
  const deleteNode     = useCanvasStore(s => s.deleteNode);

  const [fields, setFields] = useState(data.fields || []);
  const [showAddRow, setShowAddRow] = useState(false);
  const [newName, setNewName]   = useState('');
  const [newValue, setNewValue] = useState('');
  const [newType, setNewType]   = useState('string');
  const [showVariables, setShowVariables] = useState(false);
  const [activeFieldId, setActiveFieldId] = useState(null); // quale campo sta editando valore
  const nameRef = useRef(null);

  // Scrive nello store solo se i campi locali differiscono da quelli salvati:
  // al montaggio coincidono, quindi aprire il canvas non lo rende modificato.
  useEffect(() => {
    const saved = useCanvasStore.getState().getNodeById(id)?.data?.fields || [];
    if (JSON.stringify(saved) !== JSON.stringify(fields)) updateNodeData(id, { fields });
  }, [fields, id, updateNodeData]);

  // Calcola schema output (usa field_type se definito, altrimenti auto-detect)
  const outputSchema = useMemo(() => fields.map(f => ({
    name: f.name,
    type: f.field_type || 'string',
  })), [fields]);

  const handleAdd = useCallback(() => {
    if (!newName.trim()) return;
    if (fields.some(f => f.name === newName.trim())) {
      toast.warning('Campo già esistente!');
      return;
    }
    setFields(prev => [...prev, {
      id: `field_${Date.now()}`,
      name: newName.trim(),
      value: newValue,
      field_type: newType,
    }]);
    setNewName('');
    setNewValue('');
    setNewType('string');
    setShowAddRow(false);
  }, [newName, newValue, newType, fields, toast]);

  const handleRemove = useCallback((fieldId) => {
    setFields(prev => prev.filter(f => f.id !== fieldId));
  }, []);

  const handleUpdate = useCallback((fieldId, updates) => {
    setFields(prev => prev.map(f => f.id === fieldId ? { ...f, ...updates } : f));
  }, []);

  const insertVariable = useCallback((varName) => {
    if (activeFieldId) {
      handleUpdate(activeFieldId, {
        value: (fields.find(f => f.id === activeFieldId)?.value || '') + varName
      });
    } else {
      setNewValue(prev => prev + varName);
    }
    setShowVariables(false);
  }, [activeFieldId, fields, handleUpdate]);

  const handleKeyDown = (e) => {
    if (e.key === 'Enter') handleAdd();
    if (e.key === 'Escape') { setShowAddRow(false); setShowVariables(false); }
  };

  return (
    <div className={`svn ${selected ? 'svn--selected' : ''}`}>
      {/* Header */}
      <div className="svn__header">
        <span className="svn__icon">📝</span>
        <span className="svn__title">{data.label || 'Valori Statici'}</span>
        <span className="svn__count">{fields.length} {fields.length === 1 ? 'campo' : 'campi'}</span>
        <button
          type="button"
          className="svn__node-del"
          onClick={e => { e.stopPropagation(); deleteNode(id); }}
          onMouseDown={e => e.stopPropagation()}
          title="Elimina nodo"
        >×</button>
      </div>

      {/* Column headers (solo se ci sono campi o si sta aggiungendo) */}
      {(fields.length > 0 || showAddRow) && (
        <div className="svn__col-headers">
          <span className="svn__col-name">Campo</span>
          <span className="svn__col-value">Valore</span>
          <span className="svn__col-type">Tipo</span>
          <span className="svn__col-del" />
        </div>
      )}

      {/* Righe esistenti */}
      <div className="svn__rows">
        {fields.length === 0 && !showAddRow && (
          <div className="svn__empty">Nessun campo — clicca + per aggiungere</div>
        )}

        {fields.map(field => {
          const hasTemplate = hasTemplateVariables(field.value);
          const processed   = hasTemplate ? processTemplateVariables(field.value) : null;
          const typeStyle   = TYPE_COLORS[field.field_type || 'string'];

          return (
            <div key={field.id} className="svn__row">
              {/* Nome */}
              <input
                className="svn__input svn__input--name"
                value={field.name}
                onChange={e => handleUpdate(field.id, { name: e.target.value })}
                placeholder="nome"
                title={field.name}
              />
              {/* Valore */}
              <div className="svn__value-wrap">
                <input
                  className={`svn__input svn__input--value ${hasTemplate ? 'svn__input--template' : ''}`}
                  value={field.value}
                  onChange={e => handleUpdate(field.id, { value: e.target.value })}
                  onFocus={() => setActiveFieldId(field.id)}
                  onBlur={() => setTimeout(() => setActiveFieldId(null), 200)}
                  placeholder="valore"
                />
                {hasTemplate && (
                  <div className="svn__preview" title="Valore processato">→ {processed}</div>
                )}
              </div>
              {/* Tipo */}
              <select
                className="svn__type-select"
                style={{ background: typeStyle.bg, color: typeStyle.color, border: `1px solid ${typeStyle.color}33` }}
                value={field.field_type || 'string'}
                onChange={e => handleUpdate(field.id, { field_type: e.target.value })}
              >
                {FIELD_TYPES.map(t => (
                  <option key={t.value} value={t.value}>{t.icon} {t.label}</option>
                ))}
              </select>
              {/* Cancella */}
              <button
                type="button"
                className="svn__del"
                onClick={() => handleRemove(field.id)}
                title="Rimuovi campo"
              >×</button>
            </div>
          );
        })}

        {/* Riga aggiungi */}
        {showAddRow && (
          <div className="svn__row svn__row--add">
            <input
              ref={nameRef}
              className="svn__input svn__input--name"
              value={newName}
              onChange={e => setNewName(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="nome"
              autoFocus
            />
            <div className="svn__value-wrap">
              <input
                className="svn__input svn__input--value"
                value={newValue}
                onChange={e => setNewValue(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="valore"
                onFocus={() => setActiveFieldId(null)}
              />
            </div>
            <select
              className="svn__type-select"
              style={{ background: TYPE_COLORS[newType].bg, color: TYPE_COLORS[newType].color, border: `1px solid ${TYPE_COLORS[newType].color}33` }}
              value={newType}
              onChange={e => setNewType(e.target.value)}
            >
              {FIELD_TYPES.map(t => (
                <option key={t.value} value={t.value}>{t.icon} {t.label}</option>
              ))}
            </select>
            <button type="button" className="svn__del svn__del--confirm" onClick={handleAdd} title="Conferma">✓</button>
          </div>
        )}
      </div>

      {/* Footer: aggiungi + variabili */}
      <div className="svn__footer">
        {!showAddRow ? (
          <button type="button" className="svn__add-btn" onClick={() => setShowAddRow(true)}>
            + Aggiungi campo
          </button>
        ) : (
          <div className="svn__footer-actions">
            <button type="button" className="svn__footer-btn svn__footer-btn--var"
              onClick={() => setShowVariables(v => !v)} title="Inserisci variabile template">
              ƒ Variabili
            </button>
            <button type="button" className="svn__footer-btn"
              onClick={() => { setShowAddRow(false); setShowVariables(false); }}>
              Annulla
            </button>
          </div>
        )}
      </div>

      {/* Pannello variabili */}
      {showVariables && (
        <div className="svn__variables">
          <div className="svn__variables-header">📅 Variabili Template</div>
          <div className="svn__variables-list">
            {AVAILABLE_VARIABLES.map(v => (
              <button type="button" key={v.name} className="svn__var-btn"
                onClick={() => insertVariable(v.name)} title={v.description}>
                <span className="svn__var-name">{v.name}</span>
                <span className="svn__var-example">{v.example}</span>
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Output schema */}
      {fields.length > 0 && (
        <div className="svn__schema">
          <span className="svn__schema-label">OUTPUT:</span>
          <div className="svn__schema-tags">
            {outputSchema.map((col, i) => (
              <span key={i} className={`svn__schema-tag svn__schema-tag--${col.type}`}>
                {col.name}
              </span>
            ))}
          </div>
        </div>
      )}

      <Handle type="source" position={Position.Right} id="output" className="svn__handle" />
      {/* `<ToastContainer>` legacy rimosso: il container vive nel
          provider globale (`ToastContext`). Senza la rimozione,
          ogni toast (es. "Progetto salvato!" dal ManipolaEditor)
          appariva DUE volte — una via il container globale e una
          via questo container locale che ascolta lo stesso store. */}
    </div>
  );
}

export const createStaticValuesNode = (position = { x: 100, y: 100 }) => ({
  id: `static_${Date.now()}`,
  type: 'staticValues',
  position,
  data: { label: 'Valori Statici', fields: [] }
});

export default memo(StaticValuesNode);
