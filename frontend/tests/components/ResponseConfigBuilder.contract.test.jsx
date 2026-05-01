import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { vi } from 'vitest';

import ResponseConfigBuilder from '../../src/components/panels/ResponseConfigBuilder';

/**
 * Contract test per ResponseConfigBuilder (978 LOC, 0 prior coverage).
 *
 * Aree:
 *   1. empty state quando sampleData e' assente
 *   2. render JsonTreeNode con sampleData → fields top-level visibili
 *   3. click checkbox "Estrai" su scalar → onChange con mappings updated
 *   4. click "Esplodi" su array → onChange con iterate_on updated
 *   5. tab navigation: structure → mappings panel
 *   6. preset selection (geojson_features) → onChange con response_preset
 *      + UI mostra "Configurazione automatica attiva"
 */

function lastConfig(onChange) {
  const calls = onChange.mock.calls;
  if (calls.length === 0) return null;
  return calls[calls.length - 1][0];
}

describe('ResponseConfigBuilder — contract', () => {
  // ── 1. Empty state ────────────────────────────────────────────────

  it('shows the empty state when sampleData is missing', () => {
    render(<ResponseConfigBuilder sampleData={null} onChange={vi.fn()} />);
    expect(
      screen.getByText(/Esegui prima il test dell'API/i),
    ).toBeInTheDocument();
    // Niente tab structure quando empty.
    expect(screen.queryByRole('button', { name: /Struttura/i })).not.toBeInTheDocument();
  });

  // ── 2. Render tree with sampleData ────────────────────────────────

  it('renders the JSON tree showing top-level fields from sampleData', () => {
    const onChange = vi.fn();
    render(
      <ResponseConfigBuilder
        sampleData={{ count: 2, name: 'Rome' }}
        onChange={onChange}
      />,
    );

    // Tab "Struttura" attivo per default.
    expect(
      screen.getByRole('button', { name: /Struttura/i }),
    ).toBeInTheDocument();
    // I top-level fields compaiono nel tree (root expanded by default).
    expect(screen.getByText('count')).toBeInTheDocument();
    expect(screen.getByText('name')).toBeInTheDocument();
  });

  // ── 3. Click scalar checkbox → mappings ──────────────────────────

  it('checking a scalar field propagates a new mapping via onChange', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <ResponseConfigBuilder
        sampleData={{ count: 2, name: 'Rome' }}
        onChange={onChange}
      />,
    );

    // Aspetta il primo onChange (effect su mount).
    await waitFor(() => expect(onChange).toHaveBeenCalled());
    const initialCalls = onChange.mock.calls.length;

    // I campi scalar hanno checkbox accessibile via name "Estrai".
    const estraiCheckboxes = screen.getAllByRole('checkbox', { name: /Estrai/i });
    expect(estraiCheckboxes.length).toBeGreaterThanOrEqual(2);

    // Click sul primo (campo "count").
    await user.click(estraiCheckboxes[0]);

    await waitFor(() => {
      expect(onChange.mock.calls.length).toBeGreaterThan(initialCalls);
    });
    const cfg = lastConfig(onChange);
    // Forma: { response_config: { root_path, iterate_on, mappings, transforms } }
    expect(cfg.response_config.mappings).toEqual([
      expect.objectContaining({ path: 'count', column: 'count' }),
    ]);
  });

  // ── 4. Click array Esplodi → iterate_on ──────────────────────────

  it('toggling an array node populates iterate_on via onChange', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <ResponseConfigBuilder
        sampleData={{ items: [{ id: 1, label: 'a' }] }}
        onChange={onChange}
      />,
    );

    await waitFor(() => expect(onChange).toHaveBeenCalled());
    const initialCalls = onChange.mock.calls.length;

    // Bottone "💫 Esplodi" su array node (titolo "Esplodi questo array").
    const expandBtn = await screen.findByTitle('Esplodi questo array');
    await user.click(expandBtn);

    await waitFor(() => {
      expect(onChange.mock.calls.length).toBeGreaterThan(initialCalls);
    });
    const cfg = lastConfig(onChange);
    expect(cfg.response_config.iterate_on).toEqual([
      expect.objectContaining({ path: 'items', as: 'items' }),
    ]);
  });

  // ── 5. Tab navigation: structure → mappings ──────────────────────

  it('switches to the Mappings tab and hides the structure toolbar', async () => {
    const user = userEvent.setup();
    render(
      <ResponseConfigBuilder
        sampleData={{ count: 2 }}
        onChange={vi.fn()}
      />,
    );

    // Default: structure tab → toolbar "Root path:" visibile.
    expect(screen.getByText(/Root path:/i)).toBeInTheDocument();

    // Click sul tab "Mappings".
    await user.click(screen.getByRole('button', { name: /Mappings/i }));

    // Mappings panel: header "Campi da estrarre" + empty state.
    expect(
      await screen.findByText(/Campi da estrarre/i),
    ).toBeInTheDocument();
    // Toolbar structure non piu' visibile.
    expect(screen.queryByText(/Root path:/i)).not.toBeInTheDocument();
  });

  // ── 6. Preset selection ───────────────────────────────────────────

  it('selecting a preset emits {response_preset} and shows the preset notice', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <ResponseConfigBuilder
        sampleData={{ type: 'FeatureCollection', features: [] }}
        onChange={onChange}
      />,
    );

    await waitFor(() => expect(onChange).toHaveBeenCalled());
    const initialCalls = onChange.mock.calls.length;

    // Preset select e' la prima combobox (header "🎯 Preset:").
    const presetSelect = screen.getAllByRole('combobox')[0];
    await user.selectOptions(presetSelect, 'geojson_features');

    await waitFor(() => {
      expect(onChange.mock.calls.length).toBeGreaterThan(initialCalls);
    });
    expect(lastConfig(onChange)).toEqual({
      response_preset: 'geojson_features',
    });
    // UI: nota "Configurazione automatica attiva" visibile.
    expect(
      screen.getByText(/Configurazione automatica attiva/i),
    ).toBeInTheDocument();
  });
});
