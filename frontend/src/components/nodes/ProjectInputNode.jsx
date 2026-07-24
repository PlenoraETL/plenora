import React, { memo, useEffect, useMemo, useState } from 'react';
import { Handle, Position } from 'reactflow';
import useCanvasStore from '../../store/useCanvasStore';
import { getTransformRegistry } from '../../api/transforms';
import { listConnectionSets } from '../../api';
import {
  getNodeBindableTargets,
  isParameterBindingEdge,
} from '../../utils/runtimeBindingEdges';
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
  ['connection', 'Connection Set'],
];

const normalizeKey = value => String(value || '')
  .toLowerCase()
  .normalize('NFD').replace(/[\u0300-\u036f]/g, '')
  .replace(/[^a-z0-9_]+/g, '_')
  .replace(/^_+|_+$/g, '')
  .replace(/^[^a-z]+/, '');

let registryCache = null;
let registryPromise = null;
let connectionSetsCache = null;
let connectionSetsPromise = null;

const loadRegistry = () => {
  if (registryCache) return Promise.resolve(registryCache);
  if (!registryPromise) {
    registryPromise = getTransformRegistry().then(value => {
      registryCache = value;
      return value;
    }).finally(() => { registryPromise = null; });
  }
  return registryPromise;
};

const loadConnectionSets = () => {
  if (connectionSetsCache) return Promise.resolve(connectionSetsCache);
  if (!connectionSetsPromise) {
    connectionSetsPromise = listConnectionSets().then(value => {
      connectionSetsCache = value.connection_sets || [];
      return connectionSetsCache;
    }).finally(() => { connectionSetsPromise = null; });
  }
  return connectionSetsPromise;
};

function ProjectInputNode({ id, data, selected }) {
  const updateNodeData = useCanvasStore(s => s.updateNodeData);
  const deleteNode = useCanvasStore(s => s.deleteNode);
  const nodes = useCanvasStore(s => s.nodes);
  const edges = useCanvasStore(s => s.edges);
  const setEdges = useCanvasStore(s => s.setEdges);
  const updateEdge = useCanvasStore(s => s.updateEdge);
  const isReadOnly = useCanvasStore(s => s.isReadOnly);
  const [registry, setRegistry] = useState(registryCache);
  const [registryStatus, setRegistryStatus] = useState(
    registryCache ? 'ready' : 'idle',
  );
  const [connectionSets, setConnectionSets] = useState(connectionSetsCache || []);
  const kind = data.input_kind || 'file';
  const bindingEdges = useMemo(
    () => edges.filter(edge => isParameterBindingEdge(edge) && edge.source === id),
    [edges, id],
  );
  const columnText = useMemo(
    () => (data.expected_columns || []).map(column => column.name).join(', '),
    [data.expected_columns],
  );

  useEffect(() => {
    if (kind === 'file' || registry) return undefined;
    let active = true;
    setRegistryStatus('loading');
    loadRegistry()
      .then(value => {
        if (!active) return;
        setRegistry(value);
        setRegistryStatus('ready');
      })
      .catch(() => {
        if (active) setRegistryStatus('error');
      });
    return () => { active = false; };
  }, [kind, registry]);

  useEffect(() => {
    if (kind !== 'connection') return undefined;
    let active = true;
    loadConnectionSets()
      .then(items => { if (active) setConnectionSets(items); })
      .catch(() => { if (active) setConnectionSets([]); });
    return () => { active = false; };
  }, [kind]);

  const update = changes => updateNodeData(id, changes);
  const updateBinding = (edgeId, targetField) => {
    const target = bindingEdges.find(edge => edge.id === edgeId)?.target;
    const targetNode = nodes.find(node => node.id === target);
    const option = getNodeBindableTargets(targetNode, registry)
      .find(item => item.value === targetField);
    const directParameter = option
      && option.value === `config.${option.parameter}`
      ? option.parameter
      : '';
    updateEdge(edgeId, edge => ({
      targetHandle: directParameter ? `parameter:${directParameter}` : undefined,
      data: {
        ...edge.data,
        target_field: targetField,
        target_parameter: option?.parameter || '',
      },
    }));
  };
  const removeBinding = edgeId => {
    if (isReadOnly) return;
    setEdges(current => current.filter(edge => edge.id !== edgeId));
  };
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
        <select className="nodrag" value={kind} disabled={isReadOnly} onChange={event => {
          if (isReadOnly) return;
          const nextKind = event.target.value;
          setEdges(current => current.filter(edge => edge.source !== id));
          update({
            input_kind: nextKind,
            bindings: [],
            connection_set_id: nextKind === 'connection' ? (data.connection_set_id || '') : null,
          });
        }}>
          {INPUT_TYPES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
      </label>
      <label className="pin__check nodrag">
        <input type="checkbox" checked={data.required !== false} onChange={event => update({ required: event.target.checked })} />
        Obbligatorio
      </label>

      {kind === 'connection' ? (
        <>
          <label className="pin__field">
            <span>Set di connessioni</span>
            <select
              className="nodrag"
              value={data.connection_set_id || ''}
              disabled={isReadOnly}
              onChange={event => update({
                connection_set_id: event.target.value || null,
                default_value: null,
              })}
            >
              <option value="">Seleziona un set…</option>
              {connectionSets.map(item => (
                <option key={item.id} value={item.id}>
                  {item.name} ({item.members?.filter(member => member.active).length || 0})
                </option>
              ))}
            </select>
          </label>
          <label className="pin__field">
            <span>Valore predefinito (facoltativo)</span>
            <select
              className="nodrag"
              value={data.default_value ?? ''}
              disabled={isReadOnly || !data.connection_set_id}
              onChange={event => update({ default_value: event.target.value || null })}
            >
              <option value="">Nessun valore predefinito</option>
              {(connectionSets.find(item => item.id === data.connection_set_id)?.members || [])
                .filter(member => member.active)
                .map(member => (
                  <option key={member.selector_key} value={member.selector_key}>
                    {member.label}
                  </option>
                ))}
            </select>
          </label>
          <p className="pin__binding-hint">
            Questo input seleziona la connessione dell’intero progetto Estrai.
          </p>
        </>
      ) : kind === 'file' ? (
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
            <div className="pin__binding-title">Collegamenti al canvas</div>
            {bindingEdges.length === 0 && (
              <p className="pin__binding-hint pin__binding-hint--warning">
                Trascina il collegamento sul nodo da configurare oppure usa
                “Input runtime” accanto a un suo parametro.
              </p>
            )}
            {bindingEdges.map(edge => {
              const targetNode = nodes.find(node => node.id === edge.target);
              const options = getNodeBindableTargets(targetNode, registry);
              const targetLabel = targetNode?.data?.label
                || targetNode?.data?.name
                || targetNode?.data?.transform_op
                || targetNode?.type
                || edge.target;
              const currentField = edge.data?.target_field || '';
              const awaitingRegistry = targetNode?.type === 'transform'
                && registryStatus !== 'ready';
              return (
                <div className="pin__binding-row" key={edge.id}>
                  <div className="pin__binding-target">
                    <span>Destinazione</span>
                    <strong>{targetLabel}</strong>
                    <button type="button" className="nodrag" disabled={isReadOnly} onClick={() => removeBinding(edge.id)} title="Rimuovi collegamento">×</button>
                  </div>
                  <label className="pin__field">
                    <span>Parametro configurabile</span>
                    <select
                      className="nodrag"
                      value={currentField}
                      onChange={event => updateBinding(edge.id, event.target.value)}
                      disabled={isReadOnly || !targetNode || awaitingRegistry || options.length === 0}
                    >
                      <option value="">Seleziona il parametro…</option>
                      {currentField && !options.some(item => item.value === currentField) && (
                        <option value={currentField}>{currentField} (legacy)</option>
                      )}
                      {options.map(option => (
                        <option key={option.value} value={option.value}>{option.label}</option>
                      ))}
                    </select>
                  </label>
                  {awaitingRegistry && registryStatus !== 'error' && (
                    <p className="pin__binding-hint">
                      Caricamento parametri configurabili…
                    </p>
                  )}
                  {awaitingRegistry && registryStatus === 'error' && (
                    <p className="pin__binding-hint pin__binding-hint--error">
                      Impossibile caricare il registry dei parametri.
                    </p>
                  )}
                  {targetNode && !awaitingRegistry && options.length === 0 && (
                    <p className="pin__binding-hint pin__binding-hint--error">
                      Il nodo collegato non espone parametri runtime sicuri.
                    </p>
                  )}
                  {!awaitingRegistry && !currentField && options.length > 0 && (
                    <p className="pin__binding-hint pin__binding-hint--warning">
                      Seleziona il parametro da valorizzare.
                    </p>
                  )}
                </div>
              );
            })}
          </div>
        </>
      )}

      {kind !== 'connection' && (
        <Handle
          type="source"
          position={Position.Right}
          id={kind === 'file' ? 'output' : 'parameter'}
          className="pin__handle"
          isConnectable={!isReadOnly}
        />
      )}
    </div>
  );
}

export default memo(ProjectInputNode);
