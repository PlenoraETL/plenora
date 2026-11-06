import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import NodePalette from '../../src/components/canvas/NodePalette';

/** Contract Fase 4 M2: palette con ricerca e tastiera. */
const groups = [
  { label: 'Più usate', onlyWhenEmpty: true, items: [{ id: 'filter', label: 'Filtra righe' }] },
  { label: 'Filtra', items: [{ id: 'filter', label: 'Filtra righe' }, { id: 'sample', label: 'Campiona' }] },
  { label: 'Riorganizza', items: [{ id: 'rename', label: 'Rinomina colonne' }] },
];

describe('NodePalette', () => {
  it('filters by text, hides the "most used" group while searching, picks with Enter', () => {
    const onPick = vi.fn();
    const onClose = vi.fn();
    render(<NodePalette open groups={groups} onPick={onPick} onClose={onClose} context="Pulisci codici" />);
    expect(screen.getByText(/Dopo/)).toBeTruthy();
    const input = screen.getByRole('searchbox', { name: /cerca/i });
    fireEvent.change(input, { target: { value: 'rinom' } });
    expect(screen.queryByText('Più usate')).toBeNull();
    expect(screen.queryByText('Campiona')).toBeNull();
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(onPick).toHaveBeenCalledWith(expect.objectContaining({ id: 'rename' }));
    expect(onClose).toHaveBeenCalled();
  });

  it('closes with Escape without picking', () => {
    const onPick = vi.fn();
    const onClose = vi.fn();
    render(<NodePalette open groups={groups} onPick={onPick} onClose={onClose} />);
    fireEvent.keyDown(screen.getByRole('searchbox', { name: /cerca/i }), { key: 'Escape' });
    expect(onClose).toHaveBeenCalled();
    expect(onPick).not.toHaveBeenCalled();
  });
});
