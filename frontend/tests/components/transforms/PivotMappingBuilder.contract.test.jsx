import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { vi } from 'vitest';

import PivotMappingBuilder from '../../../src/components/transforms/PivotMappingBuilder';

/**
 * Contract test per PivotMappingBuilder (132 LOC, 0 prior coverage).
 *
 * Aree:
 *   1. empty state: hint "Nessun mapping" + niente header + niente rows
 *   2. render con dict iniziale: header + 1 row con key/val precompilati
 *   3. addRow: "+ Aggiungi mapping" emette onChange({}) (key vuoto -> dict
 *      vuoto)
 *   4. updateRow auto-prefill: typing key in row vuota popola anche val
 *      con lo stesso valore (branch del source `if !updated[idx].val`)
 *   5. removeRow: × elimina la riga e propaga il dict ridotto
 */

describe('PivotMappingBuilder — contract', () => {
  // ── 1. Empty state ───────────────────────────────────────────────

  it('shows the hint and no header/rows when value is empty', () => {
    render(<PivotMappingBuilder value={{}} onChange={vi.fn()} />);

    expect(
      screen.getByText(/Nessun mapping: verrà creata una colonna/i),
    ).toBeInTheDocument();
    // Niente header nei mode empty.
    expect(screen.queryByText(/Valore sorgente/)).not.toBeInTheDocument();
    expect(
      screen.queryByText(/Nome colonna output/),
    ).not.toBeInTheDocument();
    // Solo il bottone Aggiungi mapping presente.
    expect(
      screen.getByRole('button', { name: /Aggiungi mapping/i }),
    ).toBeInTheDocument();
  });

  // ── 2. Render with dict value ───────────────────────────────────

  it('renders header and prefilled inputs when value is a non-empty dict', () => {
    render(
      <PivotMappingBuilder
        value={{ canone: 'val_canone' }}
        onChange={vi.fn()}
      />,
    );

    // Header visibile (almeno 1 riga presente).
    expect(screen.getByText(/Valore sorgente/)).toBeInTheDocument();
    expect(screen.getByText(/Nome colonna output/)).toBeInTheDocument();
    // Input precompilati con la entry del dict.
    expect(screen.getByDisplayValue('canone')).toBeInTheDocument();
    expect(screen.getByDisplayValue('val_canone')).toBeInTheDocument();
    // Hint NON visibile quando ci sono righe.
    expect(
      screen.queryByText(/verrà creata una colonna per ogni valore unico/i),
    ).not.toBeInTheDocument();
  });

  // ── 3. Add row ──────────────────────────────────────────────────

  it('adds a new empty row on "+ Aggiungi mapping" click and emits empty dict', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<PivotMappingBuilder value={{}} onChange={onChange} />);

    await user.click(
      screen.getByRole('button', { name: /Aggiungi mapping/i }),
    );

    // Header ora visibile (la riga e' stata aggiunta).
    expect(screen.getByText(/Valore sorgente/)).toBeInTheDocument();
    // rowsToDict() filtra le righe con key vuoto -> dict vuoto.
    expect(onChange).toHaveBeenLastCalledWith({});
  });

  // ── 4. Auto-prefill val on key typing ───────────────────────────

  it('auto-prefills val when the user types key on a row with empty val', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<PivotMappingBuilder value={{}} onChange={onChange} />);

    // Crea una riga vuota.
    await user.click(
      screen.getByRole('button', { name: /Aggiungi mapping/i }),
    );

    // fireEvent.change con value='canone' completo: emette UN solo
    // updateRow con field='key' e val=''. Il branch !updated[idx].val
    // si attiva e setta val='canone'. (user.type carattere-per-carattere
    // attiverebbe l'auto-prefill solo al primo, perche' dal secondo in
    // poi val e' gia' valorizzato col primo char.)
    const keyInput = screen.getByPlaceholderText('es: canone');
    fireEvent.change(keyInput, { target: { value: 'canone' } });

    expect(onChange).toHaveBeenLastCalledWith({ canone: 'canone' });
  });

  // ── 5. Remove row ───────────────────────────────────────────────

  it('removes a row on × click and emits the reduced dict', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <PivotMappingBuilder
        value={{ canone: 'val_canone', rendita: 'val_rendita' }}
        onChange={onChange}
      />,
    );

    // 2 righe iniziali → 2 bottoni × (uno per riga).
    const removeButtons = screen.getAllByRole('button', { name: '×' });
    expect(removeButtons).toHaveLength(2);

    // Click sul × della prima riga (canone).
    await user.click(removeButtons[0]);

    expect(onChange).toHaveBeenLastCalledWith({ rendita: 'val_rendita' });
  });
});
