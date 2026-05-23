/**
 * Sessione 2 P0 — useMultiModal reconciler su cambio modalNames.
 *
 * Bug originale: `useState(initialState)` cattura solo il primo render.
 * Se `modalNames` cambiava (feature flag dinamico che aggiunge un
 * modale dopo il mount), il nuovo nome restava `undefined` in `modals`
 * → `isOpen('newName')` ritornava `false` ma `open('newName')` produceva
 * uno state shape inconsistente. Inoltre `closeAll`/`openExclusive`
 * resettavano allo stato VECCHIO perdendo eventuali nomi aggiunti.
 *
 * Post-fix: `useEffect` reconciler su `namesKey` che AGGIUNGE chiavi
 * mancanti (default false) + RIMUOVE quelle non piu' in modalNames,
 * preservando lo stato delle chiavi gia' presenti.
 */

import { describe, it, expect } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useMultiModal } from '../../src/hooks/useMultiModal';


describe('useMultiModal reconcile su cambio modalNames (sessione 2 P0)', () => {
  it('aggiunge nuova chiave quando modalNames cresce', () => {
    const { result, rerender } = renderHook(
      ({ names }) => useMultiModal(names),
      { initialProps: { names: ['catalog', 'template'] } }
    );

    expect(result.current.isOpen('catalog')).toBe(false);
    expect(result.current.isOpen('template')).toBe(false);

    // Feature flag dinamico aggiunge un terzo modale
    rerender({ names: ['catalog', 'template', 'settings'] });

    // Pre-fix: result.current.state.settings === undefined → isOpen=false ma
    // open('settings') seguito da isOpen('settings') restituiva sempre false
    // perche' lo state shape mancava di 'settings'.
    expect(result.current.state).toHaveProperty('settings', false);
    expect(result.current.isOpen('settings')).toBe(false);

    act(() => result.current.open('settings'));
    expect(result.current.isOpen('settings')).toBe(true);
  });

  it('preserva lo stato delle chiavi gia presenti quando modalNames cambia', () => {
    const { result, rerender } = renderHook(
      ({ names }) => useMultiModal(names),
      { initialProps: { names: ['catalog', 'template'] } }
    );

    act(() => result.current.open('catalog'));
    expect(result.current.isOpen('catalog')).toBe(true);

    // Aggiungi un nuovo modale: 'catalog' deve rimanere aperto
    rerender({ names: ['catalog', 'template', 'settings'] });
    expect(result.current.isOpen('catalog')).toBe(true);
    expect(result.current.isOpen('settings')).toBe(false);
  });

  it('rimuove chiavi quando modalNames si riduce', () => {
    const { result, rerender } = renderHook(
      ({ names }) => useMultiModal(names),
      { initialProps: { names: ['catalog', 'template', 'settings'] } }
    );

    act(() => result.current.open('settings'));

    // Feature flag dinamico rimuove 'settings'
    rerender({ names: ['catalog', 'template'] });

    expect(result.current.state).not.toHaveProperty('settings');
  });

  it('closeAll dopo modalNames change resetta solo le chiavi attuali', () => {
    const { result, rerender } = renderHook(
      ({ names }) => useMultiModal(names),
      { initialProps: { names: ['catalog'] } }
    );

    rerender({ names: ['catalog', 'template'] });

    act(() => result.current.open('catalog'));
    act(() => result.current.open('template'));
    expect(result.current.isOpen('catalog')).toBe(true);
    expect(result.current.isOpen('template')).toBe(true);

    act(() => result.current.closeAll());
    expect(result.current.isOpen('catalog')).toBe(false);
    expect(result.current.isOpen('template')).toBe(false);
    // E lo state contiene entrambe le chiavi (non solo quelle del
    // initialState frozen al mount)
    expect(result.current.state).toHaveProperty('template');
  });
});
