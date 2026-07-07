import { describe, expect, it } from 'vitest';

import { serializeCanvas, serializeEdge } from '../../src/utils/canvasSerializer';
import { createEdgeFromData, deserializeNodes } from '../../src/utils/nodeFactories';

describe('canvasSerializer contract', () => {
  it('serializes api_enrich and result nodes into the flat backend payload shape', () => {
    const nodes = [
      {
        id: 'api-1',
        type: 'api_enrich',
        position: { x: 10, y: 20 },
        data: {
          api_id: 'conn-1',
          api_name: 'REST Enricher',
          continue_on_error: false,
          request: { body_type: 'multipart', timeout: 600 },
          response: { format: 'binary_file', output_extension: '.zip' },
          extra_files: { style: { file_source_id: 'file-1' } },
          output_kind: 'imdf',
          columns: [{ name: 'seed', type: 'str' }],
        },
      },
      {
        id: 'result-1',
        type: 'result',
        position: { x: 300, y: 20 },
        data: {
          fileName: 'export',
          format: 'csv',
          columns: [{ name: 'seed', type: 'str', alias: 'Seed Label', visible: false }],
        },
      },
    ];
    const edges = [{ id: 'e-1', source: 'api-1', target: 'result-1' }];

    const payload = serializeCanvas(nodes, edges, { mode: 'arricchisci' });

    expect(payload.nodes).toEqual([
      expect.objectContaining({
        id: 'api-1',
        type: 'api_enrich',
        api_id: 'conn-1',
        api_name: 'REST Enricher',
        continue_on_error: false,
        request: { body_type: 'multipart', timeout: 600 },
        response: { format: 'binary_file', output_extension: '.zip' },
        extra_files: { style: { file_source_id: 'file-1' } },
        output_kind: 'imdf',
      }),
      expect.objectContaining({
        id: 'result-1',
        type: 'result',
        fileName: 'export',
        format: 'csv',
        columns: [{ name: 'seed', type: 'str', alias: 'Seed Label', visible: false }],
      }),
    ]);
    expect(payload.nodes[0].data).toBeUndefined();
    expect(payload.nodes[1].data).toBeUndefined();
  });

  it('recomputes result columns from the upstream node while preserving alias and visible overrides', () => {
    const nodes = [
      {
        id: 'src-1',
        type: 'projectSource',
        position: { x: 0, y: 0 },
        data: {
          source_project_id: 'proj-1',
          columns: [
            { name: 'id', type: 'int' },
            { name: 'city', type: 'str' },
          ],
        },
      },
      {
        id: 'result-1',
        type: 'result',
        position: { x: 200, y: 0 },
        data: {
          columns: [
            { name: 'city', type: 'str', alias: 'City Label', visible: false },
            { name: 'obsolete', type: 'str', alias: 'Old', visible: true },
          ],
        },
      },
    ];
    const edges = [{ id: 'e-1', source: 'src-1', target: 'result-1' }];

    const payload = serializeCanvas(nodes, edges);
    const resultNode = payload.nodes.find((node) => node.id === 'result-1');

    expect(resultNode.columns).toEqual([
      { name: 'id', type: 'int' },
      { name: 'city', type: 'str', alias: 'City Label', visible: false },
    ]);
  });

  it('serializes join metadata for configured edges and leaves plain edges untouched', () => {
    const configuredEdge = serializeEdge({
      id: 'e-join',
      source: 'a',
      target: 'b',
      data: {
        join_type: 'inner',
        source_column: 'id',
        target_column: 'order_id',
        is_spatial: true,
        spatial_predicate: 'ST_Intersects',
      },
    });
    const passthroughEdge = { id: 'e-plain', source: 'b', target: 'c' };

    expect(configuredEdge).toEqual({
      id: 'e-join',
      source: 'a',
      target: 'b',
      join_type: 'inner',
      source_column: 'id',
      target_column: 'order_id',
      join_conditions: null,
      on_filters: null,
      is_spatial: true,
      spatial_predicate: 'ST_Intersects',
      source_geom_column: null,
      target_geom_column: null,
      spatial_distance: null,
    });
    expect(serializeEdge(passthroughEdge)).toBe(passthroughEdge);
  });

  it('serializes and rehydrates multi_join pairs with different key names', () => {
    const pairs = [
      { left: 'country', right: 'country_code' },
      { left: 'customer_id', right: 'cust_id' },
    ];
    const nodes = [
      {
        id: 'mj-1',
        type: 'multi_join',
        position: { x: 10, y: 20 },
        data: {
          config: {
            join_pairs: pairs,
            join_type: 'left',
          },
        },
      },
    ];

    const payload = serializeCanvas(nodes, []);
    expect(payload.nodes[0]).toEqual(expect.objectContaining({
      type: 'multi_join',
      join_pairs: pairs,
      left_keys: ['country', 'customer_id'],
      right_keys: ['country_code', 'cust_id'],
      join_keys: [],
      multi_join_type: 'left',
    }));

    const rehydrated = deserializeNodes(payload.nodes, { flags: { isManipulate: true } });
    expect(rehydrated[0].data.config).toEqual(expect.objectContaining({
      join_pairs: pairs,
      left_keys: ['country', 'customer_id'],
      right_keys: ['country_code', 'cust_id'],
      join_type: 'left',
    }));
  });

  it('roundtrips CARICA REST destination fields without coercing them back to DB', () => {
    const backendCanvas = {
      nodes: [
        {
          id: 'carica-rest-1',
          type: 'carica',
          position: { x: 100, y: 200 },
          dest_type: 'rest',
          rest_url: 'https://api.example.com/items',
          rest_method: 'PATCH',
          rest_headers: [{ name: 'Authorization', value: 'Bearer token', secret: true }],
          rest_body_mapping: [{ source: 'city', target: 'payload.city', required: true }],
          rest_batch_mode: 'per_record',
          rest_batch_size: 25,
          rest_timeout_seconds: 45,
          rest_success_statuses: [200, 204],
        },
      ],
      edges: [],
    };

    const rehydrated = deserializeNodes(backendCanvas.nodes);
    const serialized = serializeCanvas(rehydrated, []);

    expect(rehydrated[0].data).toEqual(expect.objectContaining({
      dest_type: 'rest',
      rest_url: 'https://api.example.com/items',
      rest_method: 'PATCH',
      rest_headers: [{ name: 'Authorization', value: 'Bearer token', secret: true }],
      rest_body_mapping: [{ source: 'city', target: 'payload.city', required: true }],
      rest_batch_mode: 'per_record',
      rest_batch_size: 25,
      rest_timeout_seconds: 45,
      rest_success_statuses: [200, 204],
    }));
    expect(serialized.nodes[0]).toEqual(expect.objectContaining({
      type: 'carica',
      dest_type: 'rest',
      rest_url: 'https://api.example.com/items',
      rest_method: 'PATCH',
      rest_body_mapping: [{ source: 'city', target: 'payload.city', required: true }],
      rest_batch_mode: 'per_record',
      rest_batch_size: 25,
    }));
  });

  it('roundtrips a complex join edge without losing join conditions or spatial metadata', () => {
    const backendEdge = {
      id: 'edge-join-complex',
      source: 'join-left',
      target: 'join-right',
      join_type: 'inner',
      source_column: 'left_id',
      target_column: 'right_id',
      join_conditions: [
        { source_column: 'left_id', operator: '=', target_column: 'right_id' },
        { source_column: 'country', operator: '=', target_column: 'country_code' },
      ],
      on_filters: [
        { side: 'left', column: 'status', operator: '=', value: 'ACTIVE' },
      ],
      is_spatial: true,
      spatial_predicate: 'ST_DWithin',
      source_geom_column: 'geom',
      target_geom_column: 'shape',
      spatial_distance: 250,
    };
    const loadedNodes = [
      { id: 'join-left', type: 'transform', data: {} },
      { id: 'join-right', type: 'transform', data: {} },
    ];

    const reactFlowEdge = createEdgeFromData(backendEdge, loadedNodes);
    const serializedEdge = serializeEdge(reactFlowEdge);

    expect(serializedEdge).toEqual(backendEdge);
  });

  it('is stable across deserialize -> serialize cycles once the canvas is in canonical backend shape', () => {
    const backendCanvas = {
      nodes: [
        {
          id: 'source-1',
          type: 'project_source',
          position: { x: 0, y: 0 },
          source_project_id: 'proj-upstream',
          columns: [
            { name: 'id', type: 'int' },
            { name: 'city', type: 'str' },
          ],
        },
        {
          id: 'api-1',
          type: 'api_enrich',
          position: { x: 220, y: 0 },
          api_id: 'conn-1',
          api_name: 'City Enricher',
          enrich_type: 'manual',
          api_type: 'generator',
          parameters: [{ name: 'city', mode: 'mapped', required: true }],
          output_mapping: [{ target: 'city_code', source: '$.code' }],
          static_parameters: {},
          columns: [
            { name: 'id', type: 'int' },
            { name: 'city', type: 'str' },
            { name: 'city_code', type: 'str' },
          ],
        },
        {
          id: 'result-1',
          type: 'result',
          position: { x: 440, y: 0 },
          fileName: 'export',
          format: 'csv',
          columns: [
            { name: 'id', type: 'int' },
            { name: 'city', type: 'str', alias: 'City Label', visible: false },
            { name: 'city_code', type: 'str' },
          ],
        },
      ],
      edges: [
        {
          id: 'edge-1',
          source: 'source-1',
          target: 'api-1',
          join_type: 'left',
          source_column: 'city',
          target_column: 'city',
          join_conditions: null,
          on_filters: null,
          is_spatial: false,
          spatial_predicate: null,
          source_geom_column: null,
          target_geom_column: null,
          spatial_distance: null,
        },
        {
          id: 'edge-2',
          source: 'api-1',
          target: 'result-1',
          join_type: 'left',
          source_column: '',
          target_column: '',
          join_conditions: null,
          on_filters: null,
          is_spatial: false,
          spatial_predicate: null,
          source_geom_column: null,
          target_geom_column: null,
          spatial_distance: null,
        },
      ],
    };

    const rehydratedNodes = deserializeNodes(backendCanvas.nodes, { flags: { isArricchisci: true } });
    const rehydratedEdges = backendCanvas.edges.map((edge) => createEdgeFromData(edge, rehydratedNodes));
    const firstPass = serializeCanvas(rehydratedNodes, rehydratedEdges, { mode: 'arricchisci' });

    const rehydratedNodesAgain = deserializeNodes(firstPass.nodes, { flags: { isArricchisci: true } });
    const rehydratedEdgesAgain = firstPass.edges.map((edge) => createEdgeFromData(edge, rehydratedNodesAgain));
    const secondPass = serializeCanvas(rehydratedNodesAgain, rehydratedEdgesAgain, { mode: 'arricchisci' });

    expect(firstPass.nodes).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          id: 'api-1',
          type: 'api_enrich',
          static_parameters: {},
          param_mapping: {},
          batch: {},
          continue_on_error: true,
          skip_null_params: true,
          max_errors: 0,
        }),
        expect.objectContaining({
          id: 'result-1',
          type: 'result',
          distinct: false,
          limit: null,
          group_by_enabled: false,
          order_by_enabled: false,
          having_enabled: false,
        }),
      ]),
    );
    expect(firstPass.edges).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          id: 'edge-2',
          join_type: 'left',
          source_column: '',
          target_column: '',
        }),
      ]),
    );
    expect(secondPass).toEqual(firstPass);
  });
});
