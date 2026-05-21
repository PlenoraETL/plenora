import { renderHook, waitFor } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { useCanvasValidation } from '../../src/hooks/useCanvasValidation';

const edge = (source, target, data = {}) => ({
  id: `${source}->${target}`,
  source,
  target,
  data,
});

const validSource = {
  id: 'source-1',
  type: 'tableNode',
  data: {
    table_name: 'customers',
    columns: [{ name: 'id', type: 'int', visible: true }],
  },
};

const resultNode = {
  id: 'result-1',
  type: 'resultNode',
  data: { format: 'csv' },
};

describe('useCanvasValidation', () => {
  it('ricalcola la validazione live quando viene configurato il nodo Carica DB', async () => {
    const edges = [
      edge('source-1', 'result-1'),
      edge('result-1', 'carica-1', { join_type: 'carica' }),
    ];
    const initialNodes = [
      validSource,
      resultNode,
      { id: 'carica-1', type: 'carica', data: { write_mode: 'create' } },
    ];

    const { result, rerender } = renderHook(
      ({ nodes }) => useCanvasValidation(nodes, edges, { debounceMs: 1 }),
      { initialProps: { nodes: initialNodes } },
    );

    await waitFor(() => {
      expect(result.current.errors).toEqual(expect.arrayContaining([
        expect.stringMatching(/connessione database/i),
        expect.stringMatching(/schema di destinazione/i),
        expect.stringMatching(/tabella di destinazione/i),
      ]));
    });

    rerender({
      nodes: [
        validSource,
        resultNode,
        {
          id: 'carica-1',
          type: 'carica',
          data: {
            write_mode: 'create',
            connection_id: 'conn-1',
            schema_name: 'public',
            table_name: 'test_import',
          },
        },
      ],
    });

    await waitFor(() => {
      expect(result.current.isValid).toBe(true);
      expect(result.current.errors).toHaveLength(0);
    });
  });
});
