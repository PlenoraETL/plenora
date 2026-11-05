import React, { useState } from 'react';
import { Position } from '@xyflow/react';
import useCanvasStore from '../../store/useCanvasStore';
import NodeHandle from '../canvas/NodeHandle';
import {
  createParameterBindingEdge,
  isBindableTransformParameter,
  isParameterBindingEdge,
  suggestedInputKind,
} from '../../utils/runtimeBindingEdges';
import { newProjectInputData } from '../../utils/uniqueName';


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
  const prepareParameterBinding = useCanvasStore(state => state.prepareParameterBinding);
  const [refusal, setRefusal] = useState(null);

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

  // Stessa regola del trascinamento sul canvas (store.prepareParameterBinding).
  const allowed = () => {
    const check = prepareParameterBinding(targetNodeId, param.id);
    setRefusal(check.ok ? null : check.message);
    return check.ok;
  };

  const connect = (sourceId) => {
    if (sourceId && !allowed()) return;
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
    if (!allowed()) return;
    const target = nodes.find(node => node.id === targetNodeId);
    const id = `project_input_${Date.now()}`;
    const label = `Input ${param.label || param.id}`;
    const kind = suggestedInputKind(param.type);
    const options = (param.options || []).map(option => (
      typeof option === 'object' ? option.value : option
    ));
    const node = {
      id,
      type: 'project_input',
      position: {
        x: Math.max(0, Number(target?.position?.x || 360) - 340),
        y: Number(target?.position?.y || 0) + 40,
      },
      // Chiave unica derivata dall'etichetta, ordine dopo il piu' alto
      // (utils/uniqueName, R8.19).
      data: newProjectInputData(nodes, {
        kind,
        label,
        default_value: null,
        validation: kind === 'select' ? { options } : {},
      }),
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
    <span className="runtime-binding nodrag" title="Collega questo parametro a un input runtime">
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
      {refusal && <span className="runtime-binding__refusal" role="alert">{refusal}</span>}
    </span>
  );
}
