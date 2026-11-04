/**
 * Blocco A, regola 4 (F2): stato delle colonne uniforme.
 *  - l'indicatore della barra (ColumnSyncBadge) vale per i TRE editor: e'
 *    nella barra comune (EditorToolbar), non in un editor solo;
 *  - ColumnStatusNote: "colonne in calcolo" prima della risposta, aspetti
 *    incerti, avviso di rete con Riprova (i lettori: ColumnReaders.contract);
 *  - "Output previsto" del GROUP BY senza aggregazioni: come prima della F2
 *    (Manipola: vuoto), mai tutte le colonne come aggregazioni.
 */
import fs from 'node:fs';
import path from 'node:path';
import React from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import EditorToolbar from '../../src/components/editor/EditorToolbar';
import useCanvasStore from '../../src/store/useCanvasStore';
import useColumnStore from '../../src/store/useColumnStore';
import { columnStatusItems } from '../../src/components/common/ColumnStatusNote';
import { groupPreviewItems } from '../../src/components/nodes/result/standard/GroupOrderHavingPanel';

const SRC = path.resolve(import.meta.dirname, '../../src');
const EDITORS = { estrai: 'pages/EstraiEditor.jsx', manipola: 'pages/ManipolaEditor.jsx', arricchisci: 'pages/ArricchisciEditor.jsx' };

beforeEach(() => {
  useCanvasStore.getState().reset();
  useColumnStore.setState({ projectId: 'p1', schemas: {}, states: {}, status: 'ok', error: null, refresh: vi.fn() });
});

describe('indicatore della barra: uguale nei tre editor', () => {
  it.each(Object.keys(EDITORS))('%s: "in calcolo" e l\'errore con Riprova', (mode) => {
    useColumnStore.setState({ status: 'loading' });
    const { rerender } = render(<EditorToolbar mode={mode} title="P" onBack={() => {}} />);
    expect(screen.getByTestId('column-sync-badge').textContent).toMatch(/Colonne in calcolo/);
    act(() => { useColumnStore.setState({ status: 'error', error: 'boom' }); });
    rerender(<EditorToolbar mode={mode} title="P" onBack={() => {}} />);
    fireEvent.click(screen.getByTestId('column-sync-badge'));
    expect(useColumnStore.getState().refresh).toHaveBeenCalledTimes(1);
  });

  it('nessun editor ha un suo indicatore delle colonne (resta solo quello comune)', () => {
    for (const rel of Object.values(EDITORS)) {
      const text = fs.readFileSync(path.join(SRC, rel), 'utf-8');
      expect(text, rel).not.toMatch(/useColumnStore\(|Aggiornamento schemi|Schemi non caricati/);
      expect(text, rel).toMatch(/EditorShell/);
    }
  });
});

describe('ColumnStatusNote: stati in parole', () => {
  it.each([
    ['prima della risposta (zero colonne)', { known: false, status: 'loading', columns: [] }, ['pending']],
    ['sincronizzazione non ancora partita', { known: false, status: 'idle', columns: [] }, ['pending']],
    ['nomi incerti dal server', { known: true, status: 'ok', state: { names_status: 'indeterminate', types_status: 'certain', order_status: 'indeterminate' } }, ['uncertain']],
    ['errore di rete', { known: true, status: 'error', error: 'x' }, ['error']],
    ['tutto noto', { known: true, status: 'ok' }, []],
  ])('%s', (_, result, kinds) => {
    expect(columnStatusItems(result).map(i => i.kind)).toEqual(kinds);
  });
});

describe('Output previsto del GROUP BY', () => {
  const outputColumns = [{ name: 'a' }, { name: 'b' }, { name: 'c' }];
  it('senza aggregazioni: Manipola vuoto, gli altri le colonne di GROUP BY (mai tutte come aggregazioni)', () => {
    expect(groupPreviewItems({ isManipulate: true, groupByColumns: ['a'], aggregations: [], outputColumns })).toEqual([]);
    expect(groupPreviewItems({ isManipulate: false, groupByColumns: ['a'], aggregations: [], outputColumns })).toEqual([['group', 'a']]);
  });
  it('con aggregazioni: i nomi previsti dal server; prima della risposta, la configurazione', () => {
    const aggregations = [{ column: 'b', function: 'SUM', alias: '' }];
    expect(groupPreviewItems({ isManipulate: true, groupByColumns: ['a'], aggregations, outputColumns: [{ name: 'a' }, { name: 'b_sum' }] }))
      .toEqual([['group', 'a'], ['agg', 'b_sum']]);
    expect(groupPreviewItems({ isManipulate: true, groupByColumns: ['a'], aggregations, outputColumns: [] }))
      .toEqual([['group', 'a'], ['agg', 'SUM(b)']]);
  });
});
