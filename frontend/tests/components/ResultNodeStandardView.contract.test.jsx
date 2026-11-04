/**
 * Contract tests for ResultNodeStandardView.
 *
 * Invarianti che questa view deve preservare post-estrazione:
 *   1. Render ESTRAI con dati minimi: mostra formato + filename + bottone Valida.
 *   2. Filename editor: click su display → input; blur/Enter → updateNodeData
 *      con chiave 'fileName' (trim applicato).
 *   3. Format button click → updateNodeData con chiave 'format'.
 *   4. DISTINCT toggle (ESTRAI) → updateNodeData con chiave 'distinct'.
 *   5. hasOnPreview=false → bottone Preview NON renderizzato.
 *      hasOnPreview=true + click → invoca onPreview.
 *   6. hasOnExecute=false → bottone Esegui NON renderizzato.
 *      hasOnExecute=true + click → invoca onExecute() (le opzioni le legge
 *      il percorso unico dallo store, Fase 4 M7).
 *   7. Modalita' esecuzione: MANIPOLA con radio 'recalculate' → runOptions
 *      condivise nello store ({force_recalculate:true}).
 *   8. Click Valida → invoca onValidate.
 *   9. Variante CSS: data.isManipulate=true → bottone execute class contiene 'manipola'.
 *  10. Execute label deriva da variante: ESTRAI→'Esegui Query',
 *      MANIPOLA→'Esegui Pipeline', ARRICCHISCI→'Esegui Arricchimento'.
 *
 * Mock di RunHistoryModal per evitare render pesante; useCanvasStore mockato
 * come in altri contract test (storeRef + vi.hoisted).
 */

import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const {
  useCanvasStoreMock,
  updateNodeDataMock,
  storeRef,
} = vi.hoisted(() => {
  const updateNodeDataMock = vi.fn();
  return {
    updateNodeDataMock,
    useCanvasStoreMock: vi.fn(),
    storeRef: { current: { updateNodeData: updateNodeDataMock } },
  };
});

vi.mock('../../src/store/useCanvasStore', () => ({
  default: useCanvasStoreMock,
}));

vi.mock('../../src/components/modals/RunHistoryModal', () => ({
  default: ({ isOpen }) => isOpen ? (
    <div data-testid="run-history-modal" />
  ) : null,
}));

useCanvasStoreMock.mockImplementation((selector) => selector(storeRef.current));
useCanvasStoreMock.getState = () => storeRef.current;

import ResultNodeStandardView from '../../src/components/nodes/result/ResultNodeStandardView';

beforeEach(() => {
  updateNodeDataMock.mockReset();
  storeRef.current = {
    updateNodeData: updateNodeDataMock,
    runState: 'idle',
    runOptions: { force_recalculate: false },
    // "Ricalcola tutto" e' stato condiviso con "▶ Esegui" della barra (M7).
    setRunOptions: vi.fn((patch) => { storeRef.current.runOptions = { ...storeRef.current.runOptions, ...patch }; }),
  };
  useCanvasStoreMock.mockImplementation((selector) => selector(storeRef.current));
  useCanvasStoreMock.getState = () => storeRef.current;
  globalThis.__frontendConsolePolicy.allowError = true;
});

const renderView = (overrides = {}) => {
  const onValidate = vi.fn();
  const onExecute = vi.fn();
  const onPreview = vi.fn();
  const props = {
    data: { fileName: 'output', format: 'csv', columns: [] },
    id: 'n1',
    upstreamColumns: [],
    hasOnPreview: true,
    hasOnExecute: true,
    onValidate,
    onExecute,
    onPreview,
    ...overrides,
  };
  const utils = render(<ResultNodeStandardView {...props} />);
  return { ...utils, onValidate, onExecute, onPreview, props };
};

describe('ResultNodeStandardView — contract', () => {
  it('render base ESTRAI: mostra sezione formato + filename + bottone Valida', () => {
    renderView();
    expect(screen.getByText(/formato/i)).toBeInTheDocument();
    expect(screen.getByText('output.csv')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /valida/i })).toBeInTheDocument();
  });

  it('format button click → updateNodeData({format})', async () => {
    const user = userEvent.setup();
    renderView();
    // Clicca su XLSX (uno dei formati standard)
    const xlsxBtn = screen.getByRole('button', { name: /xlsx/i });
    await user.click(xlsxBtn);
    expect(updateNodeDataMock).toHaveBeenCalledWith('n1', { format: 'xlsx' });
  });

  it('filename editor: click display → input → blur salva con updateNodeData', async () => {
    const user = userEvent.setup();
    renderView({
      data: { fileName: 'report', format: 'csv', columns: [] },
    });
    const display = screen.getByText('report.csv');
    await user.click(display);

    const input = screen.getByPlaceholderText(/nome file/i);
    await user.clear(input);
    await user.type(input, '  newname  ');
    fireEvent.blur(input);

    expect(updateNodeDataMock).toHaveBeenCalledWith('n1', { fileName: 'newname' });
  });

  it('DISTINCT toggle (ESTRAI) → updateNodeData({distinct})', async () => {
    const user = userEvent.setup();
    renderView();
    const distinctBox = screen.getByRole('checkbox', { name: /distinct/i });
    await user.click(distinctBox);
    expect(updateNodeDataMock).toHaveBeenCalledWith('n1', { distinct: true });
  });

  it('hasOnPreview=false: bottone Preview NON renderizzato', () => {
    renderView({ hasOnPreview: false });
    expect(screen.queryByRole('button', { name: /preview/i })).toBeNull();
  });

  it('hasOnPreview=true: click Preview → onPreview invocato', async () => {
    const user = userEvent.setup();
    const { onPreview } = renderView({ hasOnPreview: true });
    await user.click(screen.getByRole('button', { name: /preview/i }));
    expect(onPreview).toHaveBeenCalledTimes(1);
  });

  it('hasOnExecute=false: bottone Esegui NON renderizzato', () => {
    renderView({ hasOnExecute: false });
    expect(screen.queryByRole('button', { name: /esegui/i })).toBeNull();
  });

  it('hasOnExecute=true ESTRAI: click Esegui → onExecute() (opzioni dallo store)', async () => {
    const user = userEvent.setup();
    const { onExecute } = renderView();
    await user.click(screen.getByRole('button', { name: /esegui query/i }));
    expect(onExecute).toHaveBeenCalledTimes(1);
    expect(onExecute).toHaveBeenCalledWith();
  });

  it('MANIPOLA con radio "recalculate" → runOptions condivise {force_recalculate:true}', async () => {
    const user = userEvent.setup();
    const { onExecute } = renderView({
      data: { fileName: 'output', format: 'csv', columns: [], isManipulate: true },
    });
    // ESTRAI non mostra execution options; MANIPOLA si': scegli "Ricalcola tutto"
    const recalcRadio = screen.getByRole('radio', { name: /ricalcola tutto/i });
    await user.click(recalcRadio);

    expect(storeRef.current.setRunOptions).toHaveBeenCalledWith({ force_recalculate: true });
    await user.click(screen.getByRole('button', { name: /esegui pipeline/i }));
    expect(onExecute).toHaveBeenCalledWith();
  });

  it('click Valida → onValidate invocato', async () => {
    const user = userEvent.setup();
    const { onValidate } = renderView();
    await user.click(screen.getByRole('button', { name: /valida/i }));
    expect(onValidate).toHaveBeenCalledTimes(1);
  });

  it('executeLabel deriva da variante: ESTRAI→Esegui Query, MANIPOLA→Esegui Pipeline, ARRICCHISCI→Esegui Arricchimento', () => {
    const { rerender } = renderView();
    expect(screen.getByRole('button', { name: /esegui query/i })).toBeInTheDocument();

    rerender(
      <ResultNodeStandardView
        data={{ fileName: 'output', format: 'csv', columns: [], isManipulate: true }}
        id="n1"
        upstreamColumns={[]}
        hasOnPreview={true}
        hasOnExecute={true}
        onValidate={vi.fn()}
        onExecute={vi.fn()}
        onPreview={vi.fn()}
      />
    );
    expect(screen.getByRole('button', { name: /esegui pipeline/i })).toBeInTheDocument();

    rerender(
      <ResultNodeStandardView
        data={{ fileName: 'output', format: 'csv', columns: [], isArricchisci: true }}
        id="n1"
        upstreamColumns={[]}
        hasOnPreview={true}
        hasOnExecute={true}
        onValidate={vi.fn()}
        onExecute={vi.fn()}
        onPreview={vi.fn()}
      />
    );
    expect(screen.getByRole('button', { name: /esegui arricchimento/i })).toBeInTheDocument();
  });

  it('variante CSS manipola: bottone execute ha classe contenente "manipola"', () => {
    renderView({
      data: { fileName: 'output', format: 'csv', columns: [], isManipulate: true },
    });
    const execBtn = screen.getByRole('button', { name: /esegui pipeline/i });
    expect(execBtn.className).toMatch(/manipola/);
  });

  it('cronologia run: click apre RunHistoryModal', async () => {
    const user = userEvent.setup();
    renderView();
    expect(screen.queryByTestId('run-history-modal')).toBeNull();
    await user.click(screen.getByRole('button', { name: /cronologia/i }));
    expect(screen.getByTestId('run-history-modal')).toBeInTheDocument();
  });
});
