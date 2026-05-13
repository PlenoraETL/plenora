/**
 * Contract: `createApiEnrichNode` deve ribaltare i campi top-level del
 * Pydantic Node (es. `execute_if`, `continue_on_error`) dentro
 * `node.data` per il form UI, senza richiedere sync manuale per ogni
 * nuovo campo aggiunto al BE.
 *
 * Storia: prima del refactor (commit follow-up di f9700d33) il factory
 * enumerava ~22 campi a mano via `nd(node, 'field', default)`. Ogni
 * nuovo campo top-level richiedeva di toccare 3 file (Pydantic Node,
 * canvasSerializer.js, nodeFactories.js). Il refactor sostituisce la
 * enumerazione con uno spread automatico via `nodeTopLevelData(node)`;
 * questo test blinda la semantica.
 */

import { describe, it, expect } from 'vitest';

import { deserializeNodes } from '../../src/utils/nodeFactories';
import { nodeTopLevelData } from '../../src/utils/nodeHelpers';


describe('nodeTopLevelData', () => {
  it('ribalta solo i campi top-level non di trasporto ReactFlow', () => {
    const node = {
      id: 'n1',
      type: 'api_enrich',
      position: { x: 0, y: 0 },
      data: { foo: 'bar' },
      style: { background: 'red' },
      width: 250,
      height: 150,
      selected: true,
      dragging: false,
      measured: { width: 250, height: 150 },
      positionAbsolute: { x: 0, y: 0 },
      zIndex: 1,
      sourcePosition: 'right',
      targetPosition: 'left',
      // Campi del Pydantic Node — questi DEVONO finire nell'output
      api_id: 'conn-1',
      execute_if: 'expr',
    };
    const out = nodeTopLevelData(node);
    expect(out).toEqual({
      api_id: 'conn-1',
      execute_if: 'expr',
    });
  });

  it('skippa i campi null e undefined cosi` i default del factory restano validi', () => {
    const out = nodeTopLevelData({
      id: 'n1',
      type: 'api_enrich',
      position: { x: 0, y: 0 },
      api_id: null,
      api_name: undefined,
      execute_if: 'expr',
      continue_on_error: false,
    });
    expect(out).toEqual({
      // api_id e api_name skippati perche` null/undefined
      execute_if: 'expr',
      continue_on_error: false,  // false NON deve essere skippato
    });
  });

  it('input null/non-object ritorna oggetto vuoto', () => {
    expect(nodeTopLevelData(null)).toEqual({});
    expect(nodeTopLevelData(undefined)).toEqual({});
    expect(nodeTopLevelData('stringa')).toEqual({});
    expect(nodeTopLevelData(42)).toEqual({});
  });
});


describe('createApiEnrichNode via deserializeNodes', () => {
  function deserializeApiEnrich(rawNode) {
    return deserializeNodes([rawNode])[0];
  }

  it('ribalta i campi top-level del Pydantic Node nel data del nodo UI', () => {
    const node = deserializeApiEnrich({
      id: 'api-1',
      type: 'api_enrich',
      position: { x: 0, y: 0 },
      api_id: 'conn-1',
      api_name: 'REST Enricher',
      execute_if: '{{ row.qty > 0 }}',
      continue_on_error: false,
      static_parameters: { fixed_a: 'value-a' },
    });
    expect(node.data.api_id).toBe('conn-1');
    expect(node.data.api_name).toBe('REST Enricher');
    expect(node.data.execute_if).toBe('{{ row.qty > 0 }}');
    expect(node.data.continue_on_error).toBe(false);
    expect(node.data.static_parameters).toEqual({ fixed_a: 'value-a' });
  });

  it('applica i default quando il BE non popola il campo', () => {
    const node = deserializeApiEnrich({
      id: 'api-1',
      type: 'api_enrich',
      position: { x: 0, y: 0 },
      // Solo i campi minimi — gli altri devono usare il default del factory
    });
    expect(node.data.api_id).toBe('');
    expect(node.data.api_name).toBe('');
    expect(node.data.continue_on_error).toBe(true);  // default true
    expect(node.data.skip_null_params).toBe(true);
    expect(node.data.max_errors).toBe(0);
    expect(node.data.execute_if).toBeNull();
    expect(node.data.static_columns).toEqual([]);
  });

  it('node.data ha precedenza sul top-level (preserva edit in corso non salvato)', () => {
    // Caso: l'utente ha modificato execute_if nel form (-> node.data),
    // il GET dal server ritorna ancora il valore vecchio sul top-level.
    // Il factory deve preservare la versione di data.
    const node = deserializeApiEnrich({
      id: 'api-1',
      type: 'api_enrich',
      position: { x: 0, y: 0 },
      execute_if: 'old_value_from_server',  // top-level
      data: { execute_if: 'new_value_from_form_edit' },
    });
    expect(node.data.execute_if).toBe('new_value_from_form_edit');
  });

  it('non porta dentro data le chiavi ReactFlow-runtime', () => {
    const node = deserializeApiEnrich({
      id: 'api-1',
      type: 'api_enrich',
      position: { x: 0, y: 0 },
      api_id: 'conn-1',
      selected: true,
      dragging: false,
      measured: { width: 250, height: 150 },
      zIndex: 5,
    });
    expect(node.data.selected).toBeUndefined();
    expect(node.data.dragging).toBeUndefined();
    expect(node.data.measured).toBeUndefined();
    expect(node.data.zIndex).toBeUndefined();
    // ma `selected` rimane disponibile come prop top-level del nodo
    // (perche` il factory fa `...node` prima di costruire data, e
    // ReactFlow legge le sue runtime keys da li`).
    expect(node.selected).toBe(true);
  });

  it('preserva un campo nuovo top-level non noto al factory (auto-forward del Pydantic Node)', () => {
    // Caso: si aggiunge un nuovo campo `__future_field__` al Pydantic Node
    // lato BE. Senza modifiche al factory FE, il valore deve apparire in
    // node.data.<field> per il form UI. E` il payoff principale del
    // refactor: zero sync manuale per nuovi campi top-level.
    const node = deserializeApiEnrich({
      id: 'api-1',
      type: 'api_enrich',
      position: { x: 0, y: 0 },
      __future_field__: 'sentinel-value',
    });
    expect(node.data.__future_field__).toBe('sentinel-value');
  });
});
