/**
 * FilterModal.jsx — Configurazione filtri WHERE per una tabella
 *
 * Permette di aggiungere condizioni WHERE su una sorgente dati.
 * Gli operatori disponibili cambiano in base al tipo colonna:
 * stringa (LIKE, =, IS NULL...), numero (>, <, BETWEEN...),
 * data (>, <, BETWEEN con datepicker), geometry (ST_IsValid...).
 */
import React, { useState, useEffect, useMemo, useRef } from 'react';
import { useShallow } from 'zustand/react/shallow';
import { columnTypeFamily, getColumnTypeIcon, getColumnTypeLabel } from '../../utils/columnUtils';
import ModalWrapper, { ModalButton } from '../common/ModalWrapper';
import useCanvasStore from '../../store/useCanvasStore';
import {
  createParameterBindingEdge,
  isParameterBindingEdge,
} from '../../utils/runtimeBindingEdges';
import './FilterModal.css';
import { termName } from '../../utils/glossary';

/**
 * Modal per configurare filtri WHERE su una tabella
 * Supporta operatori intelligenti basati sul tipo colonna
 * 
 * REFACTORED: Usa ModalWrapper per struttura base
 * - Rimosso: gestione ESC manuale (ModalWrapper lo fa)
 * - Rimosso: overlay/stopPropagation (ModalWrapper lo fa)
 * - Rimosso: if (!isOpen) return null (ModalWrapper lo fa)
 */
function FilterModal({ isOpen, onClose, nodeId, filters, columns, tableName, onSave }) {
  const [localFilters, setLocalFilters] = useState([]);
  const [bindingSelections, setBindingSelections] = useState({});
  const editEdges = useCanvasStore(state => state.editEdges);
  // Solo gli input scalari (id e dati), non tutti i nodi con le posizioni:
  // un trascinamento non ridisegna la finestra (R9-U N6).
  const scalarFlat = useCanvasStore(useShallow(state => (state.nodes || []).filter(node => (
    node.type === 'project_input'
    && node.data?.input_kind !== 'file'
    && node.data?.input_kind !== 'connection'
  )).flatMap(node => [node.id, node.data])));
  const scalarInputs = useMemo(() => {
    const out = [];
    for (let i = 0; i < scalarFlat.length; i += 2) out.push({ id: scalarFlat[i], type: 'project_input', data: scalarFlat[i + 1] });
    return out;
  }, [scalarFlat]);

  // Normalizza colonne per supportare sia vecchio formato (stringhe) che nuovo ({name, type})
  const normalizedColumns = columns.map(col => {
    if (typeof col === 'string') {
      return { name: col, type: 'str' };
    }
    return { 
      name: col.name, 
      type: col.type || col.dtype || 'str' 
    };
  });

  // La copia locale (buffer della finestra) si fa SOLO all'apertura (N8):
  // prima l'effetto dipendeva anche da `edges` e `filters`, e un cambio
  // degli archi con la finestra aperta riportava le righe al valore salvato.
  const wasOpenRef = useRef(false);
  useEffect(() => {
    const opening = isOpen && !wasOpenRef.current;
    wasOpenRef.current = isOpen;
    if (opening) {
      const edges = useCanvasStore.getState().edges || [];
      // Copia PROFONDA: le righe vengono modificate localmente e "Annulla"
      // non deve toccare gli oggetti in node.data (audit R1.3).
      setLocalFilters(filters.length > 0 ? filters.map(f => ({ ...f })) : [{ column: '', operator: '=', value: '' }]);
      const nextSelections = {};
      filters.forEach((_filter, index) => {
        const binding = edges.find(edge => (
          isParameterBindingEdge(edge)
          && edge.target === nodeId
          && edge.data?.target_field === `filters.${index}.value`
        ));
        if (binding) nextSelections[index] = binding.source;
      });
      setBindingSelections(nextSelections);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen]);

  // RIMOSSO: useEffect per ESC - ModalWrapper lo gestisce automaticamente

  // Definizione operatori con tipi supportati
  const allOperators = [
    { value: '=', label: '=', description: 'Uguale a', types: ['all'] },
    { value: '!=', label: '!=', description: 'Diverso da', types: ['all'] },
    { value: '>', label: '>', description: 'Maggiore di', types: ['int', 'real', 'date'] },
    { value: '<', label: '<', description: 'Minore di', types: ['int', 'real', 'date'] },
    { value: '>=', label: '>=', description: 'Maggiore o uguale', types: ['int', 'real', 'date'] },
    { value: '<=', label: '<=', description: 'Minore o uguale', types: ['int', 'real', 'date'] },
    { value: 'LIKE', label: 'LIKE', description: 'Contiene testo', types: ['str'] },
    { value: 'NOT LIKE', label: 'NOT LIKE', description: 'Non contiene', types: ['str'] },
    { value: 'IN', label: 'IN', description: 'In lista valori', types: ['all'] },
    { value: 'NOT IN', label: 'NOT IN', description: 'Non in lista', types: ['all'] },
    { value: 'IS NULL', label: 'IS NULL', description: 'È nullo', types: ['all'] },
    { value: 'IS NOT NULL', label: 'IS NOT NULL', description: 'Non è nullo', types: ['all'] },
    { value: 'BETWEEN', label: 'BETWEEN', description: 'Tra due valori', types: ['int', 'real', 'date'] }
  ];

  // Fase 5 T5 (R8.18): gli operatori dipendono dalla FAMIGLIA del tipo
  // (utils/columnUtils.columnTypeFamily): integer, float, varchar,
  // timestamp... hanno gli stessi operatori di int, real, str, date.
  const getOperatorsForType = (columnType) => {
    const type = columnTypeFamily(columnType);
    return allOperators.filter(op => {
      if (type === 'geometry') {
        return ['=', '!=', 'IS NULL', 'IS NOT NULL'].includes(op.value);
      }
      if (op.types.includes('all')) return true;
      return op.types.includes(type);
    });
  };

  const getColumnType = (columnName) => {
    const col = normalizedColumns.find(c => c.name === columnName);
    return col?.type || 'str';
  };

  const getValuePlaceholder = (columnType, operator) => {
    if (operator === 'IN' || operator === 'NOT IN') {
      return "valore1, valore2, valore3";
    }
    if (operator === 'BETWEEN') {
      return "valore1 AND valore2";
    }
    if (operator === 'LIKE' || operator === 'NOT LIKE') {
      return "%testo%";
    }
    
    const type = columnTypeFamily(columnType);
    switch (type) {
      case 'int':
        return "es: 42";
      case 'real':
        return "es: 3.14";
      case 'date':
        return "es: 2024-01-15";
      case 'bool':
        return "true o false";
      default:
        return "Inserisci valore...";
    }
  };

  // Il selettore di data solo per un valore singolo: BETWEEN ("v1 AND v2")
  // e IN ("a, b") si scrivono come testo.
  const getInputType = (columnType, operator) => {
    if (['BETWEEN', 'IN', 'NOT IN'].includes(operator)) return 'text';
    return columnTypeFamily(columnType) === 'date' ? 'date' : 'text';
  };

  const addFilter = () => {
    setLocalFilters([...localFilters, { column: '', operator: '=', value: '' }]);
  };

  const removeFilter = (index) => {
    if (localFilters.length > 1) {
      setLocalFilters(localFilters.filter((_, i) => i !== index));
      setBindingSelections(current => Object.fromEntries(
        Object.entries(current)
          .filter(([key]) => Number(key) !== index)
          .map(([key, value]) => [
            Number(key) > index ? Number(key) - 1 : Number(key),
            value,
          ]),
      ));
    } else {
      setLocalFilters([{ column: '', operator: '=', value: '' }]);
      setBindingSelections({});
    }
  };

  const updateFilter = (index, field, value) => {
    // Aggiornamento immutabile: prima `updated[index][field] = value`
    // mutava l'oggetto condiviso con lo store (Annulla non annullava).
    const row = { ...localFilters[index], [field]: value };
    if (field === 'column') {
      const validOperators = getOperatorsForType(getColumnType(value));
      if (!validOperators.find(op => op.value === row.operator)) row.operator = '=';
    }
    setLocalFilters(localFilters.map((f, i) => (i === index ? row : f)));
  };

  const handleSave = () => {
    const validRows = localFilters
      .map((filter, index) => ({ filter, inputNodeId: bindingSelections[index] }))
      .filter(item => item.filter.column);
    const validFilters = validRows.map(item => item.filter);
    onSave(validFilters);
    if (nodeId) {
      editEdges(current => {
        const withoutFilterBindings = current.filter(edge => !(
          isParameterBindingEdge(edge)
          && edge.target === nodeId
          && /^filters\.\d+\.value$/.test(edge.data?.target_field || '')
        ));
        const runtimeEdges = validRows.flatMap((item, index) => (
          item.inputNodeId
            ? [createParameterBindingEdge({
                source: item.inputNodeId,
                target: nodeId,
                targetField: `filters.${index}.value`,
              })]
            : []
        ));
        return [...withoutFilterBindings, ...runtimeEdges];
      });
    }
    onClose();
  };

  // Footer con pulsanti
  const footer = (
    <>
      <ModalButton variant="secondary" onClick={onClose}>
        Annulla
      </ModalButton>
      <ModalButton variant="primary" onClick={handleSave}>
        ✓ Salva filtri
      </ModalButton>
    </>
  );

  return (
    <ModalWrapper
      isOpen={isOpen}
      onClose={onClose}
      title="Filtri WHERE"
      icon="🔍"
      size="md"
      variant="estrai"
      footer={footer}
    >
      {/* Info tabella */}
      <div className="filter-modal__info-box">
        <span className="filter-modal__info-label">Tabella:</span> {tableName}
        <span className="filter-modal__info-count">
          ({normalizedColumns.length} colonne)
        </span>
      </div>

      {/* Lista filtri */}
      <div className="filter-modal__filters-list">
        {localFilters.map((filter, index) => {
          const colType = getColumnType(filter.column);
          const availableOperators = getOperatorsForType(colType);
          const needsValue = !['IS NULL', 'IS NOT NULL'].includes(filter.operator);
          
          return (
            <div key={index} className="filter-modal__filter-item">
              {/* Colonna con icona tipo */}
              <div className="form-group">
                <label>Colonna</label>
                <select
                  value={filter.column}
                  onChange={(e) => updateFilter(index, 'column', e.target.value)}
                >
                  <option value="">-- Seleziona colonna --</option>
                  {normalizedColumns.map((col, idx) => (
                    <option key={idx} value={col.name}>
                      {getColumnTypeIcon(col.type)} {col.name} ({getColumnTypeLabel(col.type)})
                    </option>
                  ))}
                </select>
              </div>

              {/* Operatore (filtrato per tipo) */}
              <div className="form-group">
                <label>
                  Operatore
                  {filter.column && (
                    <span className="filter-modal__type-hint">
                      {' '}(per {getColumnTypeLabel(colType)})
                    </span>
                  )}
                </label>
                <select
                  value={filter.operator}
                  onChange={(e) => updateFilter(index, 'operator', e.target.value)}
                >
                  {availableOperators.map(op => (
                    <option key={op.value} value={op.value}>
                      {op.label} - {op.description}
                    </option>
                  ))}
                </select>
              </div>

              {/* Valore - Hide for IS NULL/IS NOT NULL */}
              {needsValue && (
                <div className="form-group">
                  <label>Valore</label>
                  {scalarInputs.length > 0 && (
                    <select
                      value={bindingSelections[index] || ''}
                      onChange={event => {
                        const inputNodeId = event.target.value;
                        setBindingSelections(current => ({
                          ...current,
                          [index]: inputNodeId,
                        }));
                        if (inputNodeId) updateFilter(index, 'value', '');
                      }}
                    >
                      <option value="">Valore fisso</option>
                      {scalarInputs.map(inputNode => (
                        <option key={inputNode.id} value={inputNode.id}>
                          {termName('runtime_input')}: {inputNode.data?.label || inputNode.data?.input_key}
                        </option>
                      ))}
                    </select>
                  )}
                  <input
                    type={getInputType(colType, filter.operator)}
                    value={filter.value}
                    disabled={Boolean(bindingSelections[index])}
                    onChange={(e) => updateFilter(index, 'value', e.target.value)}
                    placeholder={bindingSelections[index]
                      ? "Valore fornito all'esecuzione"
                      : getValuePlaceholder(colType, filter.operator)}
                  />
                  {filter.operator === 'LIKE' && (
                    <div className="filter-modal__value-hint">
                      💡 Usa % come jolly: %testo% trova "questo testo qui"
                    </div>
                  )}
                  {(filter.operator === 'IN' || filter.operator === 'NOT IN') && (
                    <div className="filter-modal__value-hint">
                      💡 Separa i valori con virgole: valore1, valore2
                    </div>
                  )}
                  {filter.operator === 'BETWEEN' && (
                    <div className="filter-modal__value-hint">
                      💡 Formato: valore_min AND valore_max
                    </div>
                  )}
                </div>
              )}

              {/* Bottone rimuovi filtro */}
              {localFilters.length > 1 && (
                <button type="button"
                  className="filter-modal__remove-btn"
                  onClick={() => removeFilter(index)}
                >
                  🗑️ Rimuovi filtro
                </button>
              )}
            </div>
          );
        })}
      </div>

      {/* Bottone aggiungi filtro */}
      <button type="button" className="filter-modal__add-btn" onClick={addFilter}>
        + Aggiungi filtro (AND)
      </button>

      {/* Preview SQL */}
      {localFilters.some(f => f.column) && (
        <div className="filter-modal__preview">
          <div className="filter-modal__preview-label">{termName('preview')} SQL:</div>
          <div className="filter-modal__preview-sql">
            <span className="filter-modal__sql-keyword">WHERE </span>
            {localFilters
              .filter(f => f.column)
              .map((f, idx) => {
                const needsValue = !['IS NULL', 'IS NOT NULL'].includes(f.operator);
                const colType = getColumnType(f.column);
                const isNumeric = ['int', 'real'].includes(colType);
                
                let valueDisplay = '';
                if (needsValue) {
                  if (f.operator === 'IN' || f.operator === 'NOT IN') {
                    const values = f.value ? f.value.split(',').map(v => 
                      isNumeric ? v.trim() : `'${v.trim()}'`
                    ).join(', ') : '?';
                    valueDisplay = `(${values})`;
                  } else if (f.operator === 'BETWEEN') {
                    valueDisplay = f.value || '? AND ?';
                  } else {
                    const inputNode = scalarInputs.find(node => node.id === bindingSelections[idx]);
                    valueDisplay = inputNode
                      ? `{{${inputNode.data?.input_key || 'input'}}}`
                      : f.value
                      ? (isNumeric ? f.value : `'${f.value}'`)
                      : '?';
                  }
                }
                
                return (
                  <span key={idx}>
                    {idx > 0 && <span className="filter-modal__sql-keyword"> AND </span>}
                    {tableName}.{f.column}
                    <span className="filter-modal__sql-operator"> {f.operator} </span>
                    {valueDisplay}
                  </span>
                );
              })
            }
          </div>
        </div>
      )}
    </ModalWrapper>
  );
}

export default FilterModal;
