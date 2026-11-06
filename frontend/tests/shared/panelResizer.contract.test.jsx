/**
 * Contract della maniglia del pannello del passo (UI-4 U26; UI conferma
 * C-K8): un trascinamento e' UN gesto e UNA scrittura in localStorage, alla
 * fine del gesto qualunque sia (rilascio, gesto interrotto dal sistema,
 * cattura persa); durante il gesto la larghezza cambia solo nella pagina.
 */
import React from 'react';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { PanelResizer } from '../../src/components/editor/EditorPanels';
import { LAYOUT_KEYS } from '../../src/hooks/useStoredState';

const KEY = LAYOUT_KEYS.editorPanelWidth;
let writes;

beforeEach(() => {
  localStorage.clear();
  const real = Storage.prototype.setItem;
  writes = [];
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(function setItem(k, v) {
    if (k === KEY) writes.push(JSON.parse(v));
    return real.call(this, k, v);
  });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

const mount = () => {
  render(<div data-testid="host"><PanelResizer /></div>);
  return screen.getByRole('separator');
};
const host = () => screen.getByTestId('host').style.getPropertyValue('--node-inspector-width');

describe('maniglia del pannello: un gesto, una scrittura', () => {
  it.each([['pointerUp'], ['pointerCancel'], ['lostPointerCapture']])('trascinamento chiuso da %s: larghezza viva, una sola scrittura alla fine', (end) => {
    const handle = mount();
    expect(host()).toBe('400px');
    fireEvent.pointerDown(handle, { clientX: 1000, pointerId: 1 });
    for (let x = 990; x >= 900; x -= 10) fireEvent.pointerMove(handle, { clientX: x, pointerId: 1 });
    // Durante il gesto il pannello segue, la memoria del browser no.
    expect(host()).toBe('500px');
    expect(handle.getAttribute('aria-valuenow')).toBe('500');
    expect(writes).toEqual([]);
    fireEvent[end](handle, { pointerId: 1 });
    expect(writes).toEqual([500]);
    expect(host()).toBe('500px');
    // Finito il gesto, un movimento non cambia piu' niente.
    fireEvent.pointerMove(handle, { clientX: 100, pointerId: 1 });
    expect(host()).toBe('500px');
    expect(writes).toEqual([500]);
  });

  it('limiti rispettati anche durante il gesto; un clic senza movimento non scrive', () => {
    const handle = mount();
    fireEvent.pointerDown(handle, { clientX: 1000, pointerId: 1 });
    fireEvent.pointerMove(handle, { clientX: 0, pointerId: 1 });
    expect(host()).toBe('720px');
    fireEvent.pointerUp(handle, { pointerId: 1 });
    expect(writes).toEqual([720]);
    fireEvent.pointerDown(handle, { clientX: 500, pointerId: 2 });
    fireEvent.pointerUp(handle, { pointerId: 2 });
    expect(writes).toEqual([720]);
  });

  it('nessun ascoltatore sulla finestra: smontare a meta\' gesto non lascia niente', () => {
    const add = vi.spyOn(window, 'addEventListener');
    const handle = mount();
    fireEvent.pointerDown(handle, { clientX: 1000, pointerId: 1 });
    expect(add.mock.calls.filter(([t]) => String(t).startsWith('pointer'))).toEqual([]);
    cleanup();
    expect(writes).toEqual([]);
  });
});
