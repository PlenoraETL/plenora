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

const bindingOptionsForNode = node => {
  if (!node) return [];
  const data = node.data || {};
  const options = [];
  (data.filters || []).forEach((item, index) => options.push({
    value: `filters.${index}.value`,
    label: `Filtro ${index + 1}: ${item.column || item.field || 'valore'}`,
  }));
  (data.conditions || []).forEach((item, index) => {
    options.push({
      value: `conditions.${index}.value`,
      label: `Condizione ${index + 1}: valore`,
    });
    if (Object.prototype.hasOwnProperty.call(item || {}, 'result')) {
      options.push({
        value: `conditions.${index}.result`,
        label: `Condizione ${index + 1}: risultato`,
      });
    }
  });
  (data.having_conditions || []).forEach((item, index) => options.push({
    value: `having_conditions.${index}.value`,
    label: `HAVING ${index + 1}: ${item.column || item.field || 'valore'}`,
  }));
  Object.keys(data.static_parameters || {}).forEach(name => options.push({
    value: `static_parameters.${name}`,
    label: `Parametro statico: ${name}`,
  }));
  if (Object.prototype.hasOwnProperty.call(data, 'value')) {
    options.push({ value: 'value', label: 'Valore del nodo' });
  }
  if (Object.prototype.hasOwnProperty.call(data, 'default_value')) {
    options.push({ value: 'default_value', label: 'Valore predefinito' });
  }
  return options;
};

function ProjectInputNode({ id, data, selected }) {
  const updateNodeData = useCanvasStore(s => s.updateNodeData);
  const deleteNode = useCanvasStore(s => s.deleteNode);
  const nodes = useCanvasStore(s => s.nodes);
  const edges = useCanvasStore(s => s.edges);
  const kind = data.input_kind || 'file';
  const binding = data.bindings?.[0] || {};
  const targetIds = useMemo(
    () => [...new Set(edges.filter(edge => edge.source === id).map(edge => edge.target))],
    [edges, id],
  );
  const targetNode = targetIds.length === 1
    ? nodes.find(node => node.id === targetIds[0])
    : null;
  const bindingOptions = useMemo(() => bindingOptionsForNode(targetNode), [targetNode]);
  const targetLabel = targetNode?.data?.label || targetNode?.data?.name || targetNode?.type;
  const columnText = useMemo(
    () => (data.expected_columns || []).map(column => column.name).join(', '),
    [data.expected_columns],
  );

  const update = changes => updateNodeData(id, changes);
  const updateBinding = changes => update({
    bindings: [{ ...binding, ...changes }],
  });
  const updateValidation = changes => update({
    validation: { ...(data.validation || {}), ...changes },
  });
  const optionalNumber = value => (value === '' ? null : Number(value));

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
          <label className="pin__check nodrag">
            <input type="checkbox" checked={Boolean(data.multiple)} onChange={event => update({ multiple: event.target.checked })} />
            Consenti piu' file (unione per righe)
          </label>
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
              onChange={event => updateValidation({
                accepted_extensions: event.target.value.split(',').map(value => value.trim()).filter(Boolean),
              })}
            />
          </label>
          <label className="pin__field">
            <span>Dimensione massima (MB)</span>
            <input
              className="nodrag"
              type="number"
              min="1"
              value={data.validation?.max_size_bytes ? Math.round(data.validation.max_size_bytes / 1048576) : ''}
              placeholder="limite globale"
              onChange={event => updateValidation({
                max_size_bytes: event.target.value === '' ? null : Number(event.target.value) * 1048576,
              })}
            />
          </label>
        </>
      ) : (
        <>
          <label className="pin__field">
            <span>Valore predefinito</span>
            <input
              className="nodrag"
              type={kind === 'number' || kind === 'integer' ? 'number' : kind === 'date' ? 'date' : kind === 'datetime' ? 'datetime-local' : 'text'}
              step={kind === 'number' ? 'any' : kind === 'integer' ? '1' : undefined}
              value={data.default_value ?? ''}
              onChange={event => update({ default_value: event.target.value === '' ? null : event.target.value })}
            />
          </label>
          {kind === 'select' && (
            <label className="pin__field">
              <span>Opzioni (separate da virgola)</span>
              <input
                className="nodrag"
                value={(data.validation?.options || []).join(', ')}
                onChange={event => updateValidation({
                  options: event.target.value.split(',').map(value => value.trim()).filter(Boolean),
                })}
              />
            </label>
          )}
          {kind === 'text' && (
            <div className="pin__validation-grid">
              <label className="pin__field"><span>Lunghezza minima</span><input className="nodrag" type="number" min="0" value={data.validation?.min_length ?? ''} onChange={event => updateValidation({ min_length: optionalNumber(event.target.value) })} /></label>
              <label className="pin__field"><span>Lunghezza massima</span><input className="nodrag" type="number" min="1" value={data.validation?.max_length ?? ''} onChange={event => updateValidation({ max_length: optionalNumber(event.target.value) })} /></label>
              <label className="pin__field pin__field--wide"><span>Espressione regolare</span><input className="nodrag" value={data.validation?.pattern || ''} onChange={event => updateValidation({ pattern: event.target.value || null })} placeholder="es. [A-Z]{3}" /></label>
            </div>
          )}
          {(kind === 'number' || kind === 'integer') && (
            <div className="pin__validation-grid">
              <label className="pin__field"><span>Valore minimo</span><input className="nodrag" type="number" value={data.validation?.minimum ?? ''} onChange={event => updateValidation({ minimum: optionalNumber(event.target.value) })} /></label>
              <label className="pin__field"><span>Valore massimo</span><input className="nodrag" type="number" value={data.validation?.maximum ?? ''} onChange={event => updateValidation({ maximum: optionalNumber(event.target.value) })} /></label>
            </div>
          )}
          <div className="pin__binding">
          <div className="pin__binding-title">Collegamento al canvas</div>
          {targetIds.length === 0 && (
            <p className="pin__binding-hint pin__binding-hint--warning">
              Trascina il collegamento sul nodo da configurare.
            </p>
          )}
          {targetIds.length > 1 && (
            <p className="pin__binding-hint pin__binding-hint--error">
              Collega l'input a un solo nodo destinazione.
            </p>
          )}
          {targetNode && (
            <p className="pin__binding-hint pin__binding-hint--ok">
              Destinazione: <strong>{targetLabel || targetNode.id}</strong>
            </p>
          )}
          <label className="pin__field">
            <span>Campo configurabile</span>
            <select
              className="nodrag"
              value={binding.target_field || ''}
              onChange={event => updateBinding({ target_field: event.target.value })}
              disabled={!targetNode || bindingOptions.length === 0}
            >
              <option value="">Seleziona il campo...</option>
              {binding.target_field && !bindingOptions.some(item => item.value === binding.target_field) && (
                <option value={binding.target_field}>{binding.target_field} (precedente)</option>
              )}
              {bindingOptions.map(option => (
                <option key={option.value} value={option.value}>{option.label}</option>
              ))}
            </select>
          </label>
          {targetNode && bindingOptions.length === 0 && (
            <p className="pin__binding-hint pin__binding-hint--error">
              Il nodo collegato non espone campi configurabili.
            </p>
          )}
          </div>
        </>
      )}

      <Handle type="source" position={Position.Right} className="pin__handle" />
    </div>
  );
}

export default memo(ProjectInputNode);
