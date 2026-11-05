/**
 * Fase 5, T6 (R3.13, N14): l'anteprima della query di Estrai mostra la query
 * del BACKEND (POST /projects/{id}/query-preview, stesso piano
 * dell'esecuzione), mai una ricostruzione nel frontend.
 */
import React from 'react';
import fs from 'node:fs';
import path from 'node:path';
import { render, screen, cleanup, waitFor, act } from '@testing-library/react';
import { describe, it, expect, vi, afterEach } from 'vitest';

const { previewMock } = vi.hoisted(() => ({ previewMock: vi.fn() }));
vi.mock('../../src/api', () => ({ previewProjectQuery: previewMock }));

import useCanvasStore from '../../src/store/useCanvasStore';
import QueryPreview, { paramLabel } from '../../src/components/panels/QueryPreview';

const loadCanvas = () => {
  useCanvasStore.getState().init('p1', 'estrai');
  useCanvasStore.getState().loadGraph([
    { id: 'c', type: 'tableNode', position: { x: 0, y: 0 }, data: { table_name: 'comuni', columns: [] } },
    { id: 'out', type: 'resultNode', position: { x: 0, y: 0 }, data: { fileName: 'o', format: 'csv' } },
  ], []);
};

describe('QueryPreview', () => {
  afterEach(() => { cleanup(); previewMock.mockReset(); useCanvasStore.getState().reset(); });

  it('mostra SQL, parametri e input runtime come li restituisce il backend', async () => {
    previewMock.mockResolvedValue({
      kind: 'sql', sql: 'SELECT c.id\nFROM comuni c\nWHERE c.nome = :p_0\nORDER BY c.id DESC',
      params: [{ name: 'p_0', value: '«Comune»', input_key: 'comune', input_label: 'Comune' }],
      tables_used: ['comuni'], feature_service: null, error: null, notes: [],
    });
    loadCanvas();
    render(<QueryPreview />);
    await waitFor(() => expect(screen.getByText(/ORDER/)).toBeTruthy());
    expect(previewMock).toHaveBeenCalledWith('p1', expect.objectContaining({ nodes: expect.any(Array) }), expect.anything());
    expect(screen.getByText(/dall'input «Comune» all'avvio/)).toBeTruthy();
    expect(screen.getByText('1 tabella nella query')).toBeTruthy();
  });

  it('un errore di generazione si mostra (non una query inventata)', async () => {
    previewMock.mockResolvedValue({ kind: 'sql', sql: null, params: [], tables_used: [], feature_service: null,
      error: 'Nessuna tabella connessa al nodo risultato', notes: [] });
    loadCanvas();
    render(<QueryPreview />);
    await waitFor(() => expect(screen.getByText(/Nessuna tabella connessa/)).toBeTruthy());
    expect(screen.queryByText(/SELECT/)).toBeNull();
  });

  it('spostare un nodo non rifa la richiesta (impronta senza posizioni)', async () => {
    previewMock.mockResolvedValue({ kind: 'sql', sql: 'SELECT 1', params: [], tables_used: [], feature_service: null, error: null, notes: [] });
    loadCanvas();
    render(<QueryPreview />);
    await waitFor(() => expect(previewMock).toHaveBeenCalledTimes(1));
    act(() => useCanvasStore.getState().onNodesChange([{ id: 'c', type: 'position', position: { x: 50, y: 50 } }]));
    await new Promise(r => setTimeout(r, 500));
    expect(previewMock).toHaveBeenCalledTimes(1);
  });

  it('valore di un parametro senza input', () => {
    expect(paramLabel({ value: 0 })).toBe('0');
    expect(paramLabel({ value: 'x' })).toBe("'x'");
  });

  it('il componente non costruisce SQL', () => {
    const text = fs.readFileSync(path.resolve(import.meta.dirname, '../../src/components/panels/QueryPreview.jsx'), 'utf-8');
    expect(text).not.toMatch(/lines\.push\('SELECT|`\s*LEFT JOIN|generateSQL/);
    expect(text).toContain('previewProjectQuery(');
  });
});
