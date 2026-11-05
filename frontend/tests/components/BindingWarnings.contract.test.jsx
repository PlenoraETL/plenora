/**
 * Avviso sui binding non letti dall'esecuzione (`binding_warnings` del
 * backend, stessa regola della pubblicazione). Sulla card dell'Eseguibile e
 * nella finestra «Prima di avviare» solo per chi puo' gestirlo
 * (can.manageEseguibile); nella finestra NON blocca l'avvio.
 */
import React from 'react';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { vi, describe, it, expect, beforeEach } from 'vitest';

const { runMock, getTaskStatusMock, authState } = vi.hoisted(() => ({
  runMock: vi.fn(),
  getTaskStatusMock: vi.fn(),
  authState: { user: { role: 'admin', user_id: 'a', username: 'anna' } },
}));

vi.mock('../../src/api', () => ({
  analyzeEseguibileUpload: vi.fn(),
  preflightEseguibile: vi.fn(),
  runEseguibile: runMock,
  downloadEseguibileOutput: vi.fn(),
  getTaskStatus: getTaskStatusMock,
  cancelTask: vi.fn(),
}));
vi.mock('../../src/context/AuthContext', () => ({ useAuth: () => authState }));

import EseguibileCard from '../../src/components/projects/EseguibileCard';
import ExecutableDynamicRunForm from '../../src/components/eseguibili/ExecutableDynamicRunForm';

const MESSAGE = /Input collegato a un campo che l.esecuzione non legge: ripubblica dopo averlo corretto/;
const WARNING = { input_key: 'soglia', target_node_id: 'src', target_field: 'value', motivo: "Campo non configurabile da input runtime: 'value'" };

const eseguibile = (warnings) => ({
  id: 'exe-1', name: 'Pulizia', project_type: 'estrai', active_version: 2, active_version_id: 'v2',
  default_output_format: 'csv', can_edit: true, binding_warnings: warnings,
  manifest: { inputs: [{ key: 'soglia', label: 'Soglia', kind: 'text', required: false, order: 1, default_value: '' }] },
});

beforeEach(() => {
  vi.clearAllMocks();
  authState.user = { role: 'admin', user_id: 'a', username: 'anna' };
  runMock.mockResolvedValue({ async: true, task_id: 't1' });
  getTaskStatusMock.mockResolvedValue({ status: 'success', progress: 100, result: {} });
});

describe('BindingWarnings — card dell\'Eseguibile', () => {
  it('chi gestisce vede l\'avviso con input e bersaglio', () => {
    render(<EseguibileCard item={eseguibile([WARNING])} onExecute={vi.fn()} canManage />);
    expect(screen.getByText(MESSAGE)).toBeInTheDocument();
    expect(screen.getByText(/src\.value/)).toBeInTheDocument();
  });

  it('chi non gestisce, o nessun avviso: niente', () => {
    const { rerender } = render(<EseguibileCard item={eseguibile([WARNING])} onExecute={vi.fn()} />);
    expect(screen.queryByText(MESSAGE)).toBeNull();
    rerender(<EseguibileCard item={eseguibile([])} onExecute={vi.fn()} canManage />);
    expect(screen.queryByText(MESSAGE)).toBeNull();
  });
});

describe('BindingWarnings — «Prima di avviare»', () => {
  it('avviso per chi gestisce, NON bloccante: Avvia resta attivo e l\'esecuzione parte', async () => {
    render(<ExecutableDynamicRunForm eseguibile={eseguibile([WARNING])} onClose={vi.fn()} />);
    expect(await screen.findByText(MESSAGE)).toBeInTheDocument();
    const avvia = screen.getByRole('button', { name: /avvia l'esecuzione/i });
    expect(avvia).not.toBeDisabled();
    fireEvent.click(avvia);
    await waitFor(() => expect(runMock).toHaveBeenCalledTimes(1));
  });

  it('chi non gestisce (ruolo user) non vede l\'avviso', async () => {
    authState.user = { role: 'user', user_id: 'u', username: 'ugo' };
    render(<ExecutableDynamicRunForm eseguibile={eseguibile([WARNING])} onClose={vi.fn()} />);
    expect(await screen.findByRole('button', { name: /avvia l'esecuzione/i })).not.toBeDisabled();
    expect(screen.queryByText(MESSAGE)).toBeNull();
  });
});
