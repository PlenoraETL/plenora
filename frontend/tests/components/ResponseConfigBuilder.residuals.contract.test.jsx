import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { vi } from 'vitest';

import ResponseConfigBuilder from '../../src/components/panels/ResponseConfigBuilder';

/**
 * Residual contract test per ResponseConfigBuilder.
 * Estende ResponseConfigBuilder.contract.test.jsx con i path non
 * coperti: auto-detect, root_path, transforms tab (add + update
 * operation), preview tab activation.
 *
 * Aree:
 *   1. autoDetectMappings -> mappings popolato + onChange config con
 *      mappings auto-derivati dai campi scalari
 *   2. root_path input -> onChange.root_path aggiornato
 *   3. transforms tab + addTransform -> contatore tab "Trasformazioni
 *      (1)" + transform item visibile
 *   4. updateTransform operation -> onChange.transforms include la
 *      trasformazione (operation != 'none' supera il filter)
 *   5. preview tab attivato -> header "Preview risultato" + bottone
 *      "Aggiorna preview" + dump JSON config
 */

function lastConfig(onChange) {
  const calls = onChange.mock.calls;
  if (calls.length === 0) return null;
  return calls[calls.length - 1][0];
}

describe('ResponseConfigBuilder — residuals', () => {

  it('auto-detect populates mappings from the scalar fields in sampleData', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <ResponseConfigBuilder
        sampleData={{ name: 'Rome', count: 2 }}
        onChange={onChange}
      />,
    );

    // P11 (Fase 5 T7): il montaggio non riscrive la config.
    expect(onChange).not.toHaveBeenCalled();
    const initial = onChange.mock.calls.length;

    await user.click(
      screen.getByRole('button', { name: /Auto-detect/i }),
    );

    await waitFor(() => {
      expect(onChange.mock.calls.length).toBeGreaterThan(initial);
    });
    const cfg = lastConfig(onChange);
    // Mappings popolato dai campi scalari (name, count). Verifichiamo
    // che entrambi siano presenti, agnostici sull'ordine.
    const paths = (cfg.response_config.mappings || []).map((m) => m.path);
    expect(paths).toEqual(expect.arrayContaining(['name', 'count']));
  });


  it('typing into root_path propagates to the generated config', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <ResponseConfigBuilder
        sampleData={{ data: { items: [] } }}
        onChange={onChange}
      />,
    );

    // P11 (Fase 5 T7): il montaggio non riscrive la config.
    expect(onChange).not.toHaveBeenCalled();
    const initial = onChange.mock.calls.length;

    const rootInput = screen.getByPlaceholderText(/data, results.items/i);
    await user.type(rootInput, 'data.items');

    await waitFor(() => {
      expect(onChange.mock.calls.length).toBeGreaterThan(initial);
    });
    expect(lastConfig(onChange).response_config.root_path).toBe('data.items');
  });


  it('addTransform from the transforms tab increments the counter and renders the item', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <ResponseConfigBuilder
        sampleData={{ count: 2 }}
        initialConfig={{
          mappings: [{ column: 'count', path: 'count', type: 'number' }],
        }}
        onChange={onChange}
      />,
    );

    // Counter iniziale "Trasformazioni (0)" sul tab.
    const transformsTab = screen.getByRole('button', {
      name: /Trasformazioni \(0\)/i,
    });
    await user.click(transformsTab);

    // Il select "Scegli colonna sorgente" ha id='add-transform-source'
    // nella tab transforms).
    const addSelect = document.getElementById('add-transform-source');
    expect(addSelect).toBeTruthy();
    await user.selectOptions(addSelect, 'count');

    // Click "+ Aggiungi trasformazione".
    await user.click(
      screen.getByRole('button', { name: /Aggiungi trasformazione/i }),
    );

    // Counter aggiornato a "Trasformazioni (1)".
    expect(
      screen.getByRole('button', { name: /Trasformazioni \(1\)/i }),
    ).toBeInTheDocument();
    // Transform item visibile: input "Colonna output:" con valore
    // pre-derivato 'count_transformed'.
    expect(screen.getByDisplayValue('count_transformed')).toBeInTheDocument();
  });


  it('changing transform operation propagates to onChange.transforms', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <ResponseConfigBuilder
        sampleData={{ count: 2 }}
        initialConfig={{
          mappings: [{ column: 'count', path: 'count', type: 'number' }],
          transforms: [
            { column: 'x', source: 'count', operation: 'none', value: '' },
          ],
        }}
        onChange={onChange}
      />,
    );

    // Vai alla tab Trasformazioni (counter '(1)' iniziale per il transform
    // pre-popolato; l'operation 'none' viene filtrato dal generatedConfig
    // ma la transform e' presente nello stato interno).
    await user.click(
      screen.getByRole('button', { name: /Trasformazioni/i }),
    );

    // P11 (Fase 5 T7): il montaggio non riscrive la config.
    expect(onChange).not.toHaveBeenCalled();
    const initial = onChange.mock.calls.length;

    // Trova il select operation: e' il combobox che ha 'round' come option.
    const allCombos = screen.getAllByRole('combobox');
    const opSelect = allCombos.find((s) =>
      Array.from(s.options).some((o) => o.value === 'round'),
    );
    expect(opSelect).toBeTruthy();

    await user.selectOptions(opSelect, 'round');

    await waitFor(() => {
      expect(onChange.mock.calls.length).toBeGreaterThan(initial);
    });
    const cfg = lastConfig(onChange);
    // operation='none' viene filtrato; con 'round' la transform appare.
    expect(cfg.response_config.transforms).toEqual([
      expect.objectContaining({
        column: 'x',
        source: 'count',
        operation: 'round',
      }),
    ]);
  });


  it('activates the preview tab showing the header, refresh button and config dump', async () => {
    const user = userEvent.setup();
    render(
      <ResponseConfigBuilder
        sampleData={{ count: 2 }}
        onChange={vi.fn()}
      />,
    );

    await user.click(screen.getByRole('button', { name: /Anteprima/i }));

    expect(
      await screen.findByText(/Anteprima del risultato/i),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: /Aggiorna l'anteprima/i }),
    ).toBeInTheDocument();
    // Config JSON dump visibile.
    expect(
      screen.getByText(/Configurazione generata/i),
    ).toBeInTheDocument();
  });
});
