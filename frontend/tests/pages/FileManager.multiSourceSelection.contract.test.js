import { describe, expect, it } from 'vitest';

import {
  buildMultiSourceSelection,
  resolveMultiSourcePreview,
} from '../../src/pages/FileManager/helpers';

describe('FileManager multi-source column selection', () => {
  it('recognizes sheets from preview content even when the outer file is a ZIP', () => {
    expect(resolveMultiSourcePreview({ sheets: ['Q1', 'Q2'] })).toEqual({
      sourceKind: 'sheet',
      itemList: ['Q1', 'Q2'],
    });
  });

  it('distinguishes an unvisited sheet from an explicit empty selection', () => {
    const result = buildMultiSourceSelection({
      checkedLayers: { Loaded: true, Unvisited: true },
      layerColumns: {
        Loaded: [{ name: 'id' }, { name: 'secret' }],
        Unvisited: [],
      },
      layerSelectedCols: { Loaded: [], Unvisited: [] },
      isSheet: true,
    });

    expect(result.emptySelection).toBe('Loaded');
    expect(result.config).toEqual([
      { sheet: 'Loaded', selected_columns: [] },
      { sheet: 'Unvisited', selected_columns: null },
    ]);
  });

  it('preserves the exact selected columns for a loaded sheet', () => {
    const result = buildMultiSourceSelection({
      checkedLayers: { Data: true },
      layerColumns: { Data: [{ name: 'id' }, { name: 'secret' }] },
      layerSelectedCols: { Data: ['id'] },
      isSheet: true,
    });

    expect(result.emptySelection).toBeUndefined();
    expect(result.config).toEqual([
      { sheet: 'Data', selected_columns: ['id'] },
    ]);
  });
});
