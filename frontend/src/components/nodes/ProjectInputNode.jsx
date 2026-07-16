import React, { memo, useMemo } from 'react';
import { Handle, Position } from 'reactflow';
import useCanvasStore from '../../store/useCanvasStore';
import './ProjectInputNode.css';

const INPUT_TYPES = [
  ['file', 'File tabellare'],
  ['text', 'Testo'],
  ['integer', 'Intero'],
  ['number', 'Numero'],
  ['boolean', 'Sì / No'],
  ['date', 'Data'],
  ['datetime', 'Data e ora'],
  ['select', 'Scelta'],
];

const normalizeKey = value => String(value || '')
  .toLowerCase()
  .normalize('NFD').replace(/[\u0300-\u036f]/g, '')
  .replace(/[^a-z0-9_]+/g, '_')
  .replace(/^_+|_+$/g, '')
  .replace(/^[^a-z]+/, '');

function ProjectInputNode({ id, data, selected }) {
  const updateNodeData = useCanvasStore(s => s.updateNodeData);
  const deleteNode = useCanvasStore(s => s.deleteNode);
  const kind = data.input_kind || 'file';
  const binding = data.bindings?.[0] || {};
  const columnText = useMemo(
    () => (data.expected_columns || []).map(column => column.name).join(', '),
    [data.expected_columns],
  );

  const update = changes => updateNodeData(id, changes);
  const updateBinding = changes => update({
    bindings: [{ ...binding, ...changes }],
  });

  return (
    <div className={`pin ${selected ? 'pin--selected' : ''}`}>
      <div className="pin__header">
        <span>⌨</span>
        <strong>Input progetto</strong>
        <button type="button" className="pin__delete nodrag" onClick={() => deleteNode(id)}>×</button>
      </div>

      <label className="pin__field">
        <span>Etichetta</span>
        <input className="nodrag" value={data.label || ''} onChange={event => update({ label: event.target.value })} />
      </label>
      <label className="pin__field">
        <span>Chiave</span>
        <input
          className="nodrag"
          value={data.input_key || ''}
          onChange={event => update({ input_key: normalizeKey(event.target.value) })}
          placeholder="es. file_clienti"
        />
      </label>
      <label className="pin__field">
        <span>Tipo</span>
        <select className="nodrag" value={kind} onChange={event => update({ input_kind: event.target.value, bindings: [] })}>
          {INPUT_TYPES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
      </label>
      <label className="pin__check nodrag">
        <input type="checkbox" checked={data.required !== false} onChange={event => update({ required: event.target.checked })} />
        Obbligatorio
      </label>

      {kind === 'file' ? (
        <>
          <label className="pin__field">
            <span>Colonne attese</span>
            <input
              className="nodrag"
              value={columnText}
              placeholder="codice, nome, data"
              onChange={event => update({
                expected_columns: event.target.value.split(',')
                  .map(value => value.trim()).filter(Boolean)
                  .map(name => ({ name, type: 'str', required: true })),
              })}
            />
          </label>
          <label className="pin__field">
            <span>Estensioni</span>
            <input
              className="nodrag"
              value={(data.validation?.accepted_extensions || []).join(', ')}
              placeholder="csv, xlsx, gpkg"
              onChange={event => update({ validation: {
                ...(data.validation || {}),
                accepted_extensions: event.target.value.split(',').map(value => value.trim()).filter(Boolean),
              } })}
            />
          </label>
        </>
      ) : (
        <div className="pin__binding">
          <div className="pin__binding-title">Collegamento al canvas</div>
          <p className="pin__binding-hint">Trascina il collegamento sul nodo da configurare.</p>
          <label className="pin__field">
            <span>Campo</span>
            <input
              className="nodrag"
              value={binding.target_field || ''}
              onChange={event => updateBinding({ target_field: event.target.value })}
              placeholder="filters.0.value"
            />
          </label>
        </div>
      )}

      <Handle type="source" position={Position.Right} className="pin__handle" />
    </div>
  );
}

export default memo(ProjectInputNode);
