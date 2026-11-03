export const PARAMETER_EDGE_KIND = 'parameter_binding';

const SCALAR_PARAMETER_TYPES = new Set(['string', 'number', 'boolean', 'select']);
const SCHEMA_AFFECTING_PARAMETER_IDS = new Set([
  'new_column',
  'output_column',
  'output_prefix',
  'prefix',
  'var_name',
  'value_name',
  'wkt_column',
  'lat_column',
  'lon_column',
  'count_column',
  'target_type',
]);

export function isParameterBindingEdge(edge) {
  return edge?.data?.edge_kind === PARAMETER_EDGE_KIND;
}

export function dataFlowEdges(edges) {
  return (edges || []).filter(edge => !isParameterBindingEdge(edge));
}

export function isScalarProjectInputNode(node) {
  return node?.type === 'project_input' && node.data?.input_kind !== 'file';
}

export function isBindableTransformParameter(param, transformDefinition = {}) {
  if (!param?.id) return false;
  if (param.runtime_bindable !== undefined) return Boolean(param.runtime_bindable);
  const addsColumn = transformDefinition?.output?.adds_column;
  return SCALAR_PARAMETER_TYPES.has(param.type)
    && param.id !== addsColumn
    && !SCHEMA_AFFECTING_PARAMETER_IDS.has(param.id);
}

export function parameterEdgeStyle() {
  return {
    type: 'smoothstep',
    animated: false,
    label: 'input',
    style: { stroke: '#8b5cf6', strokeWidth: 2, strokeDasharray: '7 5' },
    labelStyle: { fill: '#7c3aed', fontWeight: 700 },
  };
}

export function createParameterBindingEdge({
  source,
  target,
  targetField = '',
  targetParameter = '',
  targetHandle = '',
  id = null,
}) {
  const handleParameter = String(targetHandle || '').match(/^parameter:(.+)$/)?.[1] || '';
  const parameter = targetParameter || handleParameter;
  const field = targetField || (parameter ? `config.${parameter}` : '');
  return {
    id: id || `binding_${source}_${target}_${parameter || Date.now()}`,
    source,
    target,
    sourceHandle: 'parameter',
    targetHandle: parameter ? `parameter:${parameter}` : undefined,
    ...parameterEdgeStyle(),
    data: {
      edge_kind: PARAMETER_EDGE_KIND,
      target_field: field,
      target_parameter: parameter,
    },
  };
}

export function hydrateParameterBindingEdges(nodes) {
  const result = [];
  (nodes || []).forEach(node => {
    if (node.type !== 'project_input' || node.data?.input_kind === 'file') return;
    (node.data?.bindings || []).forEach((binding, index) => {
      if (!binding?.target_node_id || !binding?.target_field) return;
      const directParameter = String(binding.target_field).match(/^config\.([^.]+)$/)?.[1] || '';
      result.push(createParameterBindingEdge({
        id: `binding_${node.id}_${binding.target_node_id}_${index}`,
        source: node.id,
        target: binding.target_node_id,
        targetField: binding.target_field,
        targetParameter: directParameter,
      }));
    });
  });
  return result;
}

export function serializedBindingsForInput(node, edges) {
  const live = (edges || [])
    .filter(edge => isParameterBindingEdge(edge) && edge.source === node.id)
    .filter(edge => edge.target && edge.data?.target_field)
    .map(edge => ({
      target_node_id: edge.target,
      target_field: edge.data.target_field,
    }));
  return node.data?.input_kind === 'file' ? (node.data?.bindings || []) : live;
}

export function getNodeBindableTargets(node, registry = null) {
  if (!node) return [];
  const data = node.data || {};
  const options = [];

  if (node.type === 'transform') {
    const definition = registry?.transforms?.[data.transform_op]
      || registry?.[data.transform_op]
      || {};
    (definition.parameters || []).forEach(param => {
      if (isBindableTransformParameter(param, definition)) {
        options.push({
          value: `config.${param.id}`,
          parameter: param.id,
          type: param.type,
          label: param.label || param.id,
        });
      }
      if (param.type === 'condition_list') {
        if (param.runtime_bindable === false) return;
        (data.config?.[param.id] || []).forEach((item, index) => {
          ['value', 'result'].forEach(field => {
            if (Object.prototype.hasOwnProperty.call(item || {}, field)) {
              options.push({
                value: `config.${param.id}.${index}.${field}`,
                parameter: param.id,
                type: 'string',
                label: `${param.label || param.id} ${index + 1}: ${field}`,
              });
            }
          });
        });
      }
    });
  }

  (data.filters || []).forEach((item, index) => options.push({
    value: `filters.${index}.value`,
    parameter: `filters-${index}`,
    type: 'string',
    label: `Filtro ${index + 1}: ${item.column || item.field || 'valore'}`,
  }));
  (data.conditions || []).forEach((item, index) => {
    ['value', 'result'].forEach(field => {
      if (Object.prototype.hasOwnProperty.call(item || {}, field)) {
        options.push({
          value: `conditions.${index}.${field}`,
          parameter: `conditions-${index}`,
          type: 'string',
          label: `Condizione ${index + 1}: ${field}`,
        });
      }
    });
  });
  (data.having_conditions || []).forEach((item, index) => options.push({
    value: `having_conditions.${index}.value`,
    parameter: `having-${index}`,
    type: 'string',
    label: `HAVING ${index + 1}: ${item.column || item.field || 'valore'}`,
  }));
  Object.keys(data.static_parameters || {}).forEach(name => options.push({
    value: `static_parameters.${name}`,
    parameter: `static-${name}`,
    type: 'string',
    label: `Parametro statico: ${name}`,
  }));
  ['value', 'default_value'].forEach(field => {
    if (Object.prototype.hasOwnProperty.call(data, field)) {
      options.push({ value: field, parameter: field, type: 'string', label: field });
    }
  });
  return options;
}

export function suggestedInputKind(paramType) {
  if (paramType === 'number') return 'number';
  if (paramType === 'boolean') return 'boolean';
  if (paramType === 'select') return 'select';
  return 'text';
}

/**
 * Parametri singoli che una lista sostituisce: con la lista presente (anche
 * vuota) il backend ignora i singoli, quindi un input runtime collegato a
 * uno di loro non avrebbe effetto.
 */
export const LIST_SUPERSEDES = {
  filter: { list: 'conditions', params: ['column', 'operator', 'value'] },
  formula: { list: 'items', params: ['new_column', 'formula'] },
};

/**
 * Cosa fare collegando un input runtime al parametro `paramId` di `node`:
 *  - null: nessun conflitto;
 *  - { patch }: la lista ha 0 o 1 voci -> torna al formato singolo (patch
 *    della config) e il collegamento funziona;
 *  - { refuse }: la lista ha piu' voci -> collegamento rifiutato, con il motivo.
 */
export function bindingListResolution(node, paramId) {
  const rule = LIST_SUPERSEDES[node?.data?.transform_op];
  const config = node?.data?.config || {};
  if (!rule || !rule.params.includes(paramId) || !Array.isArray(config[rule.list])) return null;
  const list = config[rule.list];
  if (list.length > 1) {
    return {
      refuse: `Questo passo ha ${list.length} ${rule.list === 'items' ? 'formule' : 'condizioni'}: `
        + "un input runtime può sostituire un parametro solo quando ce n'è una. Riducile a una, poi collega.",
    };
  }
  const item = list[0] || {};
  const patch = { [rule.list]: undefined };
  if (rule.list === 'conditions' && list.length === 1) {
    Object.assign(patch, { column: item.column, operator: item.operator || '==', value: item.value ?? '', column_column_id: item.column_id });
  }
  if (rule.list === 'items' && list.length === 1) {
    Object.assign(patch, { new_column: item.new_column, formula: item.formula });
  }
  return { patch };
}
