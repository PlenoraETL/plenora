/**
 * Contract test per PreviewModal — Batch 1B (post estrazione PreviewTable).
 *
 * Invarianti che il refactor deve preservare:
 *   1. Struttura render baseline (100 righe): thead con colonne,
 *      prima riga tbody, pagination controls.
 *   2. Stabilita' mount su dataset grande (10k righe): il componente
 *      deve montare senza throw. NON e' un benchmark: copre il contratto
 *      che la paginazione limita il DOM, non che "gira veloce".
 *   3. Coupling search/sort/pagination:
 *        - onSearchChange resetta currentPage -> 1
 *        - activeTab rimane invariato al cambio search
 *        - sort non resetta searchTerm ne' svuota l'input
 *
 * NB: non usiamo dataset con colonne geometriche -> MapTab non renderizza
 *     e non servono mock Leaflet.
 */

import React from 'react';
import { render, screen, within, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi } from 'vitest';

vi.mock('../../src/utils/columnUtils', () => ({
  getColumnTypeIcon: vi.fn(() => 'A'),
  getColumnTypeLabel: vi.fn(() => 'text'),
  getColumnSRID: vi.fn(() => null),
  isGeometryType: vi.fn(() => false),
  normalizeColumnType: vi.fn((c) => (typeof c === 'string' ? c : c?.type || 'str')),
}));

import PreviewModal from '../../src/components/modals/PreviewModal';

const makeColumns = () => ([
  { name: 'id', type: 'int' },
  { name: 'name', type: 'str' },
  { name: 'score', type: 'real' },
]);

const makeRows = (n) => Array.from({ length: n }, (_, i) => ({
  id: i + 1,
  name: `row_${i + 1}`,
  score: (i + 1) * 0.5,
}));


describe('PreviewModal — contract (post split PreviewTable)', () => {
  it('render 100 righe: thead + prima riga tbody + pagination', () => {
    const data = {
      columns: makeColumns(),
      rows: makeRows(100),
      total_count: 100,
      preview_count: 100,
      truncated: false,
    };

    render(
      <PreviewModal
        isOpen={true}
        onClose={vi.fn()}
        data={data}
        loading={false}
        error={null}
        title="Preview"
        projectType="estrai"
        initialLimit={100}
      />
    );

    // Header colonne presenti dentro la tabella (i nomi compaiono anche nello StatsTab
    // hidden via CSS, quindi scoppiamo la ricerca al <table>).
    const table = screen.getByRole('table');
    expect(within(table).getByText('id')).toBeInTheDocument();
    expect(within(table).getByText('name')).toBeInTheDocument();
    expect(within(table).getByText('score')).toBeInTheDocument();

    // La prima riga tbody contiene row_1 (paginazione 20/pagina, currentPage=1)
    expect(within(table).getByText('row_1')).toBeInTheDocument();
    // La riga 21 NON e' in pagina 1
    expect(within(table).queryByText('row_21')).toBeNull();

    // Pagination: totalPages = ceil(100/20) = 5 → "1 / 5"
    expect(screen.getByText('1 / 5')).toBeInTheDocument();
  });

  it('stabilita contract: 10k righe montano senza throw e DOM resta limitato a una pagina', () => {
    const data = {
      columns: makeColumns(),
      rows: makeRows(10_000),
      total_count: 10_000,
      preview_count: 10_000,
      truncated: false,
    };

    // Mount non deve throw — contratto di stabilita', non benchmark
    expect(() => render(
      <PreviewModal
        isOpen={true}
        onClose={vi.fn()}
        data={data}
        loading={false}
        error={null}
        title="Preview 10k"
        projectType="estrai"
        initialLimit={10000}
      />
    )).not.toThrow();

    // Invariante DOM: solo 20 righe renderizzate (pagination), non 10k
    const table = screen.getByRole('table');
    const bodyRows = within(table).getAllByRole('row');
    // thead group row + thead col row + 20 tbody = 22; accetta 21 o 22
    // (21 se grouping disattivo, 22 se attivo). In ogni caso << 10k.
    expect(bodyRows.length).toBeGreaterThan(0);
    expect(bodyRows.length).toBeLessThan(30);

    // Pagination: 10000 / 20 = 500 pagine
    expect(screen.getByText('1 / 500')).toBeInTheDocument();
  });

  it('coupling search/sort/pagination: search resetta pagina; sort preserva search; tab resta "table"', async () => {
    const user = userEvent.setup();
    const data = {
      columns: makeColumns(),
      rows: makeRows(100),
      total_count: 100,
      preview_count: 100,
      truncated: false,
    };

    render(
      <PreviewModal
        isOpen={true}
        onClose={vi.fn()}
        data={data}
        loading={false}
        error={null}
        title="Preview"
        projectType="estrai"
        initialLimit={100}
      />
    );

    // Vai a pagina 3 via doppio click sul "»"
    const nextBtn = screen.getByRole('button', { name: '»' });
    await user.click(nextBtn);
    await user.click(nextBtn);
    expect(screen.getByText('3 / 5')).toBeInTheDocument();

    // Cambio search: deve resettare currentPage a 1 (coupling contract)
    const searchInput = screen.getByPlaceholderText(/cerca nei dati/i);
    await user.type(searchInput, 'row_7');
    // row_7, row_70..79 matchano → filteredRows = 11, totalPages = 1
    expect(screen.getByText(/risultati/i)).toBeInTheDocument();
    // Non e' a "3 / X" ma a "1 / X" o nessuna pagination (totalPages <=1)
    expect(screen.queryByText(/^3 \/ /)).toBeNull();

    // Tab attiva rimane "Tabella" (non cambia per effetto della search)
    const tableTab = screen.getByRole('button', { name: /tabella/i });
    expect(tableTab.className).toMatch(/active/);

    // Ora clicca su header colonna "name" per sort (dentro la tabella, non StatsTab)
    const table = screen.getByRole('table');
    await user.click(within(table).getByText('name'));

    // searchTerm preservato (input ancora contiene "row_7")
    expect(searchInput.value).toBe('row_7');

    // Tab attiva ancora "Tabella"
    expect(tableTab.className).toMatch(/active/);

    // Clear search via × button
    const clearBtn = screen.getByRole('button', { name: '×' });
    fireEvent.click(clearBtn);
    expect(searchInput.value).toBe('');
    // Dopo clear, pagination torna visibile e siamo a pagina 1 (contract coupling)
    expect(screen.getByText('1 / 5')).toBeInTheDocument();
  });
});


// =============================================================================
// PR-cleanup UX Tier 1: permission_revoked banner inline
// =============================================================================

describe('PreviewModal — permission_revoked banner', () => {
  it('rende <PermissionRevokedBanner> quando permissionRevoked=true e niente errore generico', () => {
    render(
      <PreviewModal
        isOpen={true}
        onClose={() => {}}
        loading={false}
        error="Ruolo declassato durante la preview"
        permissionRevoked={true}
        title="Test Preview"
      />,
    );

    // Banner rendered (titolo "Permessi revocati ...")
    expect(screen.getByText(/Permessi revocati/i)).toBeInTheDocument();
    // Errore generico NON rendered: il banner prevale
    expect(screen.queryByText('Errore caricamento anteprima')).toBeNull();
  });

  it('senza permissionRevoked (default false) mostra il blocco errore generico', () => {
    render(
      <PreviewModal
        isOpen={true}
        onClose={() => {}}
        loading={false}
        error="Errore qualunque"
        title="Test Preview"
      />,
    );

    // Banner NON rendered
    expect(screen.queryByText(/Permessi revocati/i)).toBeNull();
    // Errore generico rendered (backward compat)
    expect(screen.getByText('Errore caricamento anteprima')).toBeInTheDocument();
  });
});
