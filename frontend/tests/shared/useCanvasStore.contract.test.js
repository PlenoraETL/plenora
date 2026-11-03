import { beforeEach, describe, expect, it, vi } from 'vitest';

const { updateCanvasMock } = vi.hoisted(() => ({
  updateCanvasMock: vi.fn(),
}));

vi.mock('../../src/api', () => ({
  updateCanvas: updateCanvasMock,
}));

import useCanvasStore from '../../src/store/useCanvasStore';

function resetCanvasStore() {
  useCanvasStore.getState().reset();
  useCanvasStore.setState(
    {
      nodes: [],
      edges: [],
      projectId: null,
      mode: 'manipola',
      isDirty: false,
      isReadOnly: false,
      connections: [],
      lastTaskResult: null,
      editorActionsRef: { current: {} },
    },
    false,
  );
}

describe('useCanvasStore save-path contract', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    resetCanvasStore();
  });

  it('serializes the current updated canvas state when handleSaveQuiet runs', async () => {
    const store = useCanvasStore.getState();
    store.init('proj-save-1', 'arricchisci');
    store.addNodes([
      {
        id: 'api-1',
        type: 'api_enrich',
        position: { x: 10, y: 20 },
        data: {
          api_id: 'conn-1',
          api_name: 'Original API',
          static_parameters: {},
          columns: [{ name: 'seed', type: 'str' }],
        },
      },
      {
        id: 'result-1',
        type: 'result',
        position: { x: 260, y: 20 },
        data: {
          fileName: 'output',
          format: 'csv',
          columns: [{ name: 'seed', type: 'str' }],
        },
      },
    ]);
    store.addEdges([{ id: 'edge-1', source: 'api-1', target: 'result-1' }]);
    store.markClean();

    useCanvasStore.getState().updateNodeData('api-1', {
      api_name: 'Updated API',
      static_parameters: { city: 'Rome' },
    });

    await useCanvasStore.getState().handleSaveQuiet();

    expect(updateCanvasMock).toHaveBeenCalledWith(
      'proj-save-1',
      expect.objectContaining({
        nodes: expect.arrayContaining([
          expect.objectContaining({
            id: 'api-1',
            type: 'api_enrich',
            api_name: 'Updated API',
            static_parameters: { city: 'Rome' },
          }),
        ]),
      }),
    );
    expect(useCanvasStore.getState().isDirty).toBe(false);
  });

  it('persists column alias changes instead of only proving that save was called', async () => {
    const store = useCanvasStore.getState();
    store.init('proj-save-2', 'manipola');
    store.addNodes([
      {
        id: 'result-1',
        type: 'result',
        position: { x: 0, y: 0 },
        data: {
          fileName: 'output',
          format: 'csv',
          columns: [
            { name: 'city', type: 'str', alias: null, visible: true },
            { name: 'country', type: 'str', alias: null, visible: true },
          ],
        },
      },
    ]);
    store.markClean();

    useCanvasStore.getState().updateColumnAlias('result-1', 0, 'City Label');

    await useCanvasStore.getState().handleSaveQuiet();

    expect(updateCanvasMock).toHaveBeenCalledWith(
      'proj-save-2',
      expect.objectContaining({
        nodes: expect.arrayContaining([
          expect.objectContaining({
            id: 'result-1',
            columns: expect.arrayContaining([
              expect.objectContaining({ name: 'city', alias: 'City Label' }),
            ]),
          }),
        ]),
      }),
    );
  });

  it('marks parameter-edge edits dirty and persists the selected runtime field', async () => {
    const store = useCanvasStore.getState();
    store.init('proj-binding-1', 'manipola');
    store.addNodes([
      {
        id: 'input-1',
        type: 'project_input',
        position: { x: 0, y: 0 },
        data: {
          input_key: 'province',
          label: 'Provincia',
          input_kind: 'text',
          required: true,
        },
      },
      {
        id: 'filter-1',
        type: 'transform',
        position: { x: 300, y: 0 },
        data: { transform_op: 'filter', config: { value: '' } },
      },
    ]);
    store.addEdges([{
      id: 'binding-1',
      source: 'input-1',
      target: 'filter-1',
      data: { edge_kind: 'parameter_binding', target_field: '' },
    }]);
    store.markClean();

    useCanvasStore.getState().updateEdge('binding-1', edge => ({
      targetHandle: 'parameter:value',
      data: { ...edge.data, target_field: 'config.value' },
    }));

    expect(useCanvasStore.getState().isDirty).toBe(true);
    await useCanvasStore.getState().handleSaveQuiet();

    expect(updateCanvasMock).toHaveBeenCalledWith(
      'proj-binding-1',
      expect.objectContaining({
        nodes: expect.arrayContaining([
          expect.objectContaining({
            id: 'input-1',
            bindings: [{ target_node_id: 'filter-1', target_field: 'config.value' }],
          }),
        ]),
        edges: [],
      }),
    );
  });

  it('does not mark automatic React Flow dimension measurements as unsaved changes', async () => {
    const store = useCanvasStore.getState();
    store.init('proj-dim-auto', 'estrai');
    store.addNodes([
      {
        id: 'result-1',
        type: 'result',
        position: { x: 0, y: 0 },
        data: { fileName: 'output', format: 'csv' },
      },
    ]);
    store.markClean();

    useCanvasStore.getState().onNodesChange([
      {
        id: 'result-1',
        type: 'dimensions',
        dimensions: { width: 290, height: 420 },
      },
    ]);

    expect(useCanvasStore.getState().nodes[0].width).toBe(290);
    expect(useCanvasStore.getState().nodes[0].height).toBe(420);
    expect(useCanvasStore.getState().isDirty).toBe(false);

    await useCanvasStore.getState().handleSaveQuiet();

    expect(updateCanvasMock).not.toHaveBeenCalled();
  });

  it('marks explicit node resizes dirty and persists annotation size', async () => {
    const store = useCanvasStore.getState();
    store.init('proj-resize-1', 'estrai');
    store.addNodes([
      {
        id: 'comment-1',
        type: 'comment',
        position: { x: 15, y: 25 },
        style: { width: 250, height: 150 },
        data: { label: '', title: '', color: '#FBBF24' },
      },
    ]);
    store.markClean();

    useCanvasStore.getState().onNodesChange([
      {
        id: 'comment-1',
        type: 'dimensions',
        updateStyle: true,
        resizing: true,
        dimensions: { width: 360, height: 220 },
      },
    ]);

    expect(useCanvasStore.getState().isDirty).toBe(true);
    expect(useCanvasStore.getState().nodes[0].style).toEqual(
      expect.objectContaining({ width: 360, height: 220 }),
    );

    await useCanvasStore.getState().handleSaveQuiet();

    expect(updateCanvasMock).toHaveBeenCalledWith(
      'proj-resize-1',
      expect.objectContaining({
        nodes: expect.arrayContaining([
          expect.objectContaining({
            id: 'comment-1',
            type: 'comment',
            width: 360,
            height: 220,
          }),
        ]),
      }),
    );
    expect(useCanvasStore.getState().isDirty).toBe(false);
  });

  it('serializes concurrent save requests and persists the latest snapshot', async () => {
    let releaseFirstSave;
    updateCanvasMock
      .mockImplementationOnce(() => new Promise(resolve => { releaseFirstSave = resolve; }))
      .mockResolvedValueOnce({});

    const store = useCanvasStore.getState();
    store.init('proj-save-race', 'manipola');
    store.addNodes([{
      id: 'node-1',
      type: 'transform',
      position: { x: 0, y: 0 },
      data: { transform_op: 'filter', config: { marker: 'iniziale' } },
    }]);
    store.markClean();
    useCanvasStore.getState().updateNodeData('node-1', { config: { marker: 'prima' } });

    const firstSave = useCanvasStore.getState().handleSaveQuiet();
    await vi.waitFor(() => expect(updateCanvasMock).toHaveBeenCalledTimes(1));

    useCanvasStore.getState().updateNodeData('node-1', { config: { marker: 'finale' } });
    const queuedSave = useCanvasStore.getState().handleSaveQuiet();
    expect(updateCanvasMock).toHaveBeenCalledTimes(1);

    releaseFirstSave({});
    await Promise.all([firstSave, queuedSave]);

    expect(updateCanvasMock).toHaveBeenCalledTimes(2);
    expect(updateCanvasMock.mock.calls[1][1]).toEqual(expect.objectContaining({
      nodes: expect.arrayContaining([
        expect.objectContaining({
          id: 'node-1',
          config: { marker: 'finale' },
        }),
      ]),
    }));
    expect(useCanvasStore.getState().isDirty).toBe(false);
  });
});
