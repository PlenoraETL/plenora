/**
 * Contract: stack UNICO delle finestre (Fase 5, T4; R6.1, R6.5, R6.6, R6.8,
 * N6, N7, N10, N14). Sostituisce il test di useDialogFocus.
 *
 *  - Esc chiude solo la finestra in cima; Tab gira solo nella finestra in cima;
 *  - lo scroll della pagina torna al valore di prima solo quando si chiude
 *    l'ULTIMA finestra;
 *  - id del titolo unici con due finestre aperte;
 *  - focus iniziale: rispetta autoFocus, salta la ×; ritorno a chi ha aperto;
 *  - overlay: chiude solo con un clic iniziato e finito sull'overlay, e solo
 *    se la finestra lo consente;
 *  - busy: niente Esc, niente overlay, × disabilitata;
 *  - beforeClose(false) tiene aperto;
 *  - la regola "c'e' una finestra aperta" delle scorciatoie.
 */
import React, { useState } from 'react';
import { render, screen, fireEvent, act, cleanup } from '@testing-library/react';
import { describe, it, expect, vi, afterEach } from 'vitest';

import useDialog from '../../src/hooks/useDialog';
import ModalWrapper from '../../src/components/common/ModalWrapper';
import { __dialogStackSize, isShortcutBlocked } from '../../src/utils/dialogStack';

afterEach(() => { cleanup(); document.body.style.overflow = ''; });

function Dialog({ isOpen = true, onClose, busy = false, closeOnOverlay = true, beforeClose = null, autoFocusName = false, label = 'D' }) {
  const d = useDialog({ isOpen, onClose, busy, closeOnOverlay, beforeClose });
  if (!isOpen) return null;
  return (
    <div data-testid={`overlay-${label}`} {...d.overlayProps}>
      <div ref={d.ref} {...d.dialogProps}>
        <h2 id={d.titleId}>{label}</h2>
        <button type="button" {...d.closeButtonProps}>×</button>
        <button type="button">primo-{label}</button>
        <input aria-label={`nome-${label}`} autoFocus={autoFocusName} />
        <button type="button">ultimo-{label}</button>
      </div>
    </div>
  );
}

const esc = () => fireEvent.keyDown(document.activeElement || document.body, { key: 'Escape' });

describe('useDialog — stack unico', () => {
  it('Esc chiude solo la finestra in cima', () => {
    const outer = vi.fn();
    const inner = vi.fn();
    render(<><Dialog label="A" onClose={outer} /><Dialog label="B" onClose={inner} /></>);
    expect(__dialogStackSize()).toBe(2);
    esc();
    expect(inner).toHaveBeenCalledTimes(1);
    expect(outer).not.toHaveBeenCalled();
  });

  it('un controllo interno che usa Esc (preventDefault) non chiude la finestra', () => {
    const onClose = vi.fn();
    render(<Dialog onClose={onClose} />);
    const input = screen.getByLabelText('nome-D');
    input.addEventListener('keydown', (e) => e.preventDefault());
    fireEvent.keyDown(input, { key: 'Escape' });
    expect(onClose).not.toHaveBeenCalled();
  });

  it('Tab gira solo nella finestra in cima', () => {
    render(<><Dialog label="A" onClose={vi.fn()} /><Dialog label="B" onClose={vi.fn()} /></>);
    screen.getByText('ultimo-B').focus();
    fireEvent.keyDown(document.activeElement, { key: 'Tab' });
    expect(document.activeElement.closest('[role="dialog"]').textContent).toMatch(/^B/);
  });

  it('lo scroll torna quello di prima solo alla chiusura dell\'ultima finestra', () => {
    document.body.style.overflow = 'scroll';
    function Two() {
      const [inner, setInner] = useState(true);
      return (
        <>
          <Dialog label="A" onClose={vi.fn()} />
          <Dialog label="B" isOpen={inner} onClose={() => setInner(false)} />
        </>
      );
    }
    const { unmount } = render(<Two />);
    expect(document.body.style.overflow).toBe('hidden');
    act(() => { esc(); });
    expect(__dialogStackSize()).toBe(1);
    expect(document.body.style.overflow).toBe('hidden');
    unmount();
    expect(document.body.style.overflow).toBe('scroll');
  });

  it('id del titolo unici con due ModalWrapper aperti', () => {
    render(<><ModalWrapper isOpen onClose={vi.fn()} title="Uno">x</ModalWrapper><ModalWrapper isOpen onClose={vi.fn()} title="Due">y</ModalWrapper></>);
    const ids = screen.getAllByRole('dialog').map(d => d.getAttribute('aria-labelledby'));
    expect(new Set(ids).size).toBe(2);
    ids.forEach(id => expect(document.getElementById(id)).toBeTruthy());
  });

  it('focus iniziale: salta la ×, rispetta autoFocus, torna a chi ha aperto', () => {
    function Host({ auto }) {
      const [open, setOpen] = useState(false);
      return (
        <>
          <button type="button" onClick={() => setOpen(true)}>apri</button>
          <Dialog isOpen={open} onClose={() => setOpen(false)} autoFocusName={auto} />
        </>
      );
    }
    const { unmount } = render(<Host auto={false} />);
    const opener = screen.getByText('apri');
    opener.focus();
    fireEvent.click(opener);
    expect(document.activeElement).toBe(screen.getByText('primo-D'));
    act(() => { esc(); });
    expect(document.activeElement).toBe(opener);
    unmount();

    render(<Host auto />);
    screen.getByText('apri').focus();
    fireEvent.click(screen.getByText('apri'));
    expect(document.activeElement).toBe(screen.getByLabelText('nome-D'));
  });

  it('overlay: chiude solo con mousedown E click sull\'overlay, e solo se consentito', () => {
    const onClose = vi.fn();
    const { unmount } = render(<Dialog onClose={onClose} />);
    const overlay = screen.getByTestId('overlay-D');
    // Selezione partita dal campo e finita fuori: nessuna chiusura (N7).
    fireEvent.mouseDown(screen.getByLabelText('nome-D'));
    fireEvent.click(overlay);
    expect(onClose).not.toHaveBeenCalled();
    // Clic dentro la finestra: nessuna chiusura.
    fireEvent.mouseDown(screen.getByText('primo-D'));
    fireEvent.click(screen.getByText('primo-D'));
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.mouseDown(overlay);
    fireEvent.click(overlay);
    expect(onClose).toHaveBeenCalledTimes(1);
    unmount();

    const other = vi.fn();
    render(<Dialog onClose={other} closeOnOverlay={false} />);
    fireEvent.mouseDown(screen.getByTestId('overlay-D'));
    fireEvent.click(screen.getByTestId('overlay-D'));
    expect(other).not.toHaveBeenCalled();
  });

  it('busy: niente Esc, niente overlay, × disabilitata (R6.6, N6)', () => {
    const onClose = vi.fn();
    render(<Dialog onClose={onClose} busy />);
    esc();
    fireEvent.mouseDown(screen.getByTestId('overlay-D'));
    fireEvent.click(screen.getByTestId('overlay-D'));
    expect(screen.getByRole('button', { name: 'Chiudi' })).toBeDisabled();
    expect(onClose).not.toHaveBeenCalled();
  });

  it('ModalWrapper busy: × disabilitata, Esc ignorato', () => {
    const onClose = vi.fn();
    render(<ModalWrapper isOpen onClose={onClose} title="T" busy>x</ModalWrapper>);
    expect(screen.getByRole('button', { name: 'Chiudi' })).toBeDisabled();
    esc();
    expect(onClose).not.toHaveBeenCalled();
  });

  it('beforeClose che risponde false tiene la finestra aperta', async () => {
    const onClose = vi.fn();
    const beforeClose = vi.fn(async () => false);
    render(<Dialog onClose={onClose} beforeClose={beforeClose} />);
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Chiudi' })); });
    expect(beforeClose).toHaveBeenCalledWith('button');
    expect(onClose).not.toHaveBeenCalled();
  });

  it('regola unica delle scorciatoie: bloccate con una finestra aperta, salvo quella propria in cima', () => {
    const ev = { defaultPrevented: false };
    expect(isShortcutBlocked(ev)).toBe(false);
    const { unmount } = render(<Dialog onClose={vi.fn()} />);
    expect(isShortcutBlocked(ev)).toBe(true);
    const id = screen.getByRole('dialog').id;
    expect(isShortcutBlocked(ev, { ownDialogId: id })).toBe(false);
    unmount();
    expect(isShortcutBlocked(ev)).toBe(false);
    expect(isShortcutBlocked({ defaultPrevented: true })).toBe(true);
  });
});
