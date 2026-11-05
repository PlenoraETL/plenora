/**
 * Contract: scorciatoie del canvas (Fase 5, T4; R6.7, N10, R1.7, R1.11a).
 *  - Canc/Backspace chiedono conferma (regola unica utils/canvasDeletion) e
 *    poi cancellano, annullabile; senza provider non cancellano (fail-closed);
 *  - Ctrl+Z / Ctrl+Y e Canc non agiscono sotto una finestra aperta;
 *  - useConfirmModal mette in coda le richieste (R6.9).
 */
import React from 'react';
import { render, renderHook, screen, fireEvent, act, cleanup } from '@testing-library/react';
import { describe, it, expect, vi, afterEach } from 'vitest';

import useCanvasKeyboard from '../../src/hooks/useCanvasKeyboard';
import { useConfirmModal } from '../../src/hooks/useConfirmModal';
import { ConfirmProvider } from '../../src/context/ConfirmContext';
import ModalWrapper from '../../src/components/common/ModalWrapper';
import { deletionDialog } from '../../src/utils/canvasDeletion';
import { answerConfirm } from '../helpers/confirmDialog';

afterEach(() => cleanup());

function setup({ provider = true } = {}) {
  const calls = { node: vi.fn(), edges: vi.fn(), undo: vi.fn(), redo: vi.fn(), snap: vi.fn() };
  const nodes = [{ id: 'n1', type: 'transform', selected: true, data: { label: 'Filtra' } }];
  const edges = [{ id: 'e1', source: 'n1', target: 'n2', selected: true }];
  const wrapper = provider ? ({ children }) => <ConfirmProvider>{children}</ConfirmProvider> : undefined;
  renderHook(() => useCanvasKeyboard({
    nodes, edges, onDeleteNode: calls.node, onDeleteEdges: calls.edges,
    onUndo: calls.undo, onRedo: calls.redo, onBeforeDelete: calls.snap,
  }), { wrapper });
  return calls;
}

describe('canvas: cancellazione con conferma', () => {
  it('Canc chiede; Annulla non cancella; Elimina cancella dopo lo snapshot', async () => {
    const c = setup();
    await act(async () => { fireEvent.keyDown(document.body, { key: 'Delete' }); });
    expect(screen.getByRole('dialog').textContent).toMatch(/1 passo e 1 collegamento.*Ctrl\+Z/);
    await answerConfirm(false);
    expect(c.node).not.toHaveBeenCalled();
    expect(c.snap).not.toHaveBeenCalled();
    await act(async () => { fireEvent.keyDown(document.body, { key: 'Backspace' }); });
    await answerConfirm(true, { confirmText: 'Elimina' });
    expect(c.snap).toHaveBeenCalledTimes(1);
    expect(c.node).toHaveBeenCalledWith('n1');
    expect(c.edges).toHaveBeenCalledWith(['e1']);
  });

  it('senza ConfirmProvider non si cancella nulla (fail-closed)', async () => {
    const c = setup({ provider: false });
    await act(async () => { fireEvent.keyDown(document.body, { key: 'Delete' }); });
    expect(c.node).not.toHaveBeenCalled();
    expect(c.edges).not.toHaveBeenCalled();
  });

  it('testi del dialog: un passo, un collegamento', () => {
    expect(deletionDialog({ nodes: [{ type: 'transform', data: { label: 'Filtra' } }] }).message).toMatch(/I collegamenti del passo/);
    expect(deletionDialog({ edges: [{ id: 'e' }] }).message).toMatch(/collegamento selezionato/);
  });
});

describe('canvas: niente scorciatoie sotto una finestra', () => {
  it('con una modale aperta Ctrl+Z, Ctrl+Y e Canc non agiscono (anche col focus su un pulsante)', async () => {
    const c = setup();
    render(<ModalWrapper isOpen onClose={vi.fn()} title="Modale">x</ModalWrapper>);
    const close = screen.getByRole('button', { name: 'Chiudi' });
    fireEvent.keyDown(close, { key: 'z', ctrlKey: true });
    fireEvent.keyDown(close, { key: 'y', ctrlKey: true });
    fireEvent.keyDown(document.body, { key: 'Delete' });
    expect(c.undo).not.toHaveBeenCalled();
    expect(c.redo).not.toHaveBeenCalled();
    expect(screen.getAllByRole('dialog')).toHaveLength(1);
  });

  it('senza finestre Ctrl+Z annulla', () => {
    const c = setup();
    fireEvent.keyDown(document.body, { key: 'z', ctrlKey: true });
    expect(c.undo).toHaveBeenCalledTimes(1);
  });
});

describe('useConfirmModal: richieste in coda (R6.9)', () => {
  it('una seconda richiesta non cancella la prima; ognuna ha la sua risposta', async () => {
    const { result } = renderHook(() => useConfirmModal());
    let a; let b;
    act(() => { a = result.current.confirm({ title: 'A' }); });
    act(() => { b = result.current.confirm({ title: 'B' }); });
    expect(result.current.config.title).toBe('A');
    act(() => { result.current.handleConfirm(); });
    await expect(a).resolves.toBe(true);
    expect(result.current.isOpen).toBe(true);
    expect(result.current.config.title).toBe('B');
    act(() => { result.current.handleCancel(); });
    await expect(b).resolves.toBe(false);
    expect(result.current.isOpen).toBe(false);
  });
});
