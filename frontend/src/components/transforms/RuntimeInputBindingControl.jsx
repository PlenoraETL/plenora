import React from 'react';
import { Position } from '@xyflow/react';
import useCanvasStore from '../../store/useCanvasStore';
import NodeHandle from '../canvas/NodeHandle';
import {
  createParameterBindingEdge,
  isBindableTransformParameter,
  isParameterBindingEdge,
  suggestedInputKind,
} from '../../utils/runtimeBindingEdges';

const normalizeKey = value => String(value || '')
  .toLowerCase()
  .normalize('NFD').replace(/[\u0300-\u036f]/g, '')
  .replace(/[^a-z0-9_]+/g, '_')
  .replace(/^_+|_+$/g, '')
  .replace(/^[^a-z]+/, '');

const uniqueInputKey = (candidate, nodes) => {
  const base = candidate || 'input';
  const existing = new Set(
    (nodes || [])
      .filter(node => node.type === 'project_input')
      .map(node => node.data?.input_key)
      .filter(Boolean),
  );
  if (!existing.has(base)) return base;
  let suffix = 2;
  while (existing.has(`${base}_${suffix}`)) suffix += 1;
  return `${base}_${suffix}`;
};

export default function RuntimeInputBindingControl({
  targetNodeId,
  param,
  transformDefinition,
}) {
  const nodes = useCanvasStore(state => state.nodes);
  const edges = useCanvasStore(state => state.edges);
  const addNodes = useCanvasStore(state => state.addNodes);
  const editEdges = useCanvasStore(state => state.editEdges);
  const isReadOnly = useCanvasStore(state => state.isReadOnly);

  if (!targetNodeId || !isBindableTransformParameter(param, transformDefinition)) {
    return null;
  }

  const targetField = `config.${param.id}`;
  const existing = edges.find(edge => (
    isParameterBindingEdge(edge)
    && edge.target === targetNodeId
    && edge.data?.target_field === targetField
  ));
  const inputs = nodes.filter(node => (
    node.type === 'project_input' && node.data?.input_kind !== 'file'
  ));

  const connect = (sourceId) => {
    editEdges(current => {
      const withoutCurrent = current.filter(edge => !(
        isParameterBindingEdge(edge)
        && edge.target === targetNodeId
        && edge.data?.target_field === targetField
      ));
      if (!sourceId) return withoutCurrent;
      return [...withoutCurrent, createParameterBindingEdge({
        source: sourceId,
        target: targetNodeId,
        targetField,
        targetParameter: param.id,
      })];
    });
  };

  const createAndConnect = () => {
    const ordinal = nodes.filter(node => node.type === 'project_input').length + 1;
    const target = nodes.find(node => node.id === targetNodeId);
    const id = `project_input_${Date.now()}`;
    const label = `Input ${param.label || param.id}`;
    const kind = suggestedInputKind(param.type);
    const options = (param.options || []).map(option => (
      typeof option === 'object' ? option.value : option
    ));
    const inputKey = uniqueInputKey(normalizeKey(label) || `input_${ordinal}`, nodes);
    const node = {
      id,
      type: 'project_input',
      position: {
        x: Math.max(0, Number(target?.position?.x || 360) - 340),
        y: Number(target?.position?.y || 0) + 40,
      },
      data: {
        input_key: inputKey,
        label,
        input_kind: kind,
        required: true,
        default_value: null,
        expected_columns: [],
        validation: kind === 'select' ? { options } : {},
        bindings: [],
        order: ordinal - 1,
      },
    };
    addNodes(node);
    connect(id);
  };

  const onChange = event => {
    if (event.target.value === '__create__') {
      createAndConnect();
    } else {
      connect(event.target.value);
    }
  };

  return (
    <span className="runtime-binding nodrag" title="Collega questo parametro a un Input progetto">
      <NodeHandle
        type="target"
        position={Position.Left}
        id={`parameter:${param.id}`}
        className="runtime-binding__handle"
        isConnectable={!isReadOnly}
      />
      <span className="runtime-binding__icon">⌁</span>
      <select
        aria-label={`Input runtime per ${param.label || param.id}`}
        value={existing?.source || ''}
        onChange={onChange}
        disabled={isReadOnly}
      >
        <option value="">Input runtime…</option>
        {inputs.map(input => (
          <option key={input.id} value={input.id}>
            {input.data?.label || input.data?.input_key || input.id}
          </option>
        ))}
        <option value="__create__">＋ Crea nuovo input</option>
      </select>
    </span>
  );
}
