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
    store.setNodes([
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
    store.setEdges([{ id: 'edge-1', source: 'api-1', target: 'result-1' }]);
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
    store.setNodes([
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
});
