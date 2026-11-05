/**
 * Contract: ripristino di una versione (Fase 5, T4; N8).
 *  (a) chiede sempre con il dialog del progetto e, con modifiche non
 *      salvate, dice che verranno scartate;
 *  (b) dalla conferma al ricaricamento la finestra resta occupata: × e
 *      "Chiudi" spenti, Esc ignorato;
 *  (c) un solo ripristino alla volta (doppio clic = una richiesta).
 */
import React from 'react';
import { render, screen, fireEvent, act, cleanup } from '@testing-library/react';
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';

const restoreVersionMock = vi.fn();
vi.mock('../../src/utils/serverActions', () => ({ restoreVersion: (...a) => restoreVersionMock(...a) }));
vi.mock('../../src/api/projects', () => ({
  listCanvasVersions: vi.fn(async () => ({ versions: [{ id: 'v1', label: 'prima del refactor', created_at: '2026-11-01T10:00:00Z' }] })),
  createCanvasVersion: vi.fn(),
}));
const toast = { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() };
vi.mock('../../src/hooks/useToast', () => ({ useToast: () => toast }));

import CanvasVersionsModal from '../../src/components/modals/CanvasVersionsModal';
import useCanvasStore from '../../src/store/useCanvasStore';
import { answerConfirm } from '../helpers/confirmDialog';

beforeEach(() => {
  restoreVersionMock.mockReset();
  const s = useCanvasStore.getState();
  s.reset();
  s.init('p1', 'manipola');
  s.loadGraph([{ id: 'n1', type: 'transform', position: { x: 0, y: 0 }, data: { label: 'a' } }], []);
});
afterEach(() => cleanup());

async function open() {
  render(<CanvasVersionsModal isOpen onClose={vi.fn()} projectId="p1" />);
  return screen.findByRole('button', { name: /Ripristina/ });
}

describe('CanvasVersionsModal — ripristino', () => {
  it('con modifiche non salvate lo dice e chiede; Annulla non ripristina', async () => {
    act(() => { useCanvasStore.getState().updateNodeData('n1', { label: 'b' }); });
    const btn = await open();
    await act(async () => { fireEvent.click(btn); });
    expect(screen.getAllByRole('dialog').at(-1).textContent).toMatch(/modifiche non salvate.*scarta/);
    await answerConfirm(false);
    expect(restoreVersionMock).not.toHaveBeenCalled();
  });

  it('confermato: un solo ripristino e finestra occupata fino al ricaricamento', async () => {
    let finish;
    restoreVersionMock.mockImplementation(() => new Promise((r) => { finish = r; }));
    const btn = await open();
    await act(async () => { fireEvent.click(btn); fireEvent.click(btn); });
    expect(screen.getAllByRole('dialog')).toHaveLength(2);
    await answerConfirm(true, { confirmText: 'Ripristina' });
    expect(restoreVersionMock).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('button', { name: /Ripristino…/ })).toBeDisabled();
    // × e "Chiudi" del piede: spenti.
    const closers = screen.getAllByRole('button', { name: 'Chiudi' });
    expect(closers).toHaveLength(2);
    closers.forEach(b => expect(b).toBeDisabled());
    await act(async () => { finish({ ok: true }); });
    // Riuscito: resta occupata (il reload arriva da serverActions).
    expect(screen.getByRole('button', { name: /Ripristino…/ })).toBeDisabled();
  });
});
