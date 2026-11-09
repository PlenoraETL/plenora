/**
 * «Utente che esegue»: valore scelto dagli amministratori (09/11/2026).
 *
 *  1. «Prima di avviare» degli Eseguibili: con `admin_override` nel manifest,
 *     admin e superadmin vedono un campo facoltativo (etichetta del manifest,
 *     nota «Vuoto: si usa il tuo nome utente»); vuoto -> nessun valore
 *     inviato, scritto -> inviato a stima e avvio. Esecutore, user, viewer,
 *     o input senza l'opzione: riga in sola lettura, nessun valore inviato.
 *  2. Pannello dell'input: casella «Gli amministratori possono scegliere il
 *     valore», etichetta e valore di prova; togliere la casella o cambiare
 *     tipo li toglie.
 *  3. Ripubblicazione: le righe solo informative (`blocking: false`) si
 *     elencano a parte e non chiedono la conferma.
 */
import React from 'react';
import { render, screen, waitFor, fireEvent, cleanup } from '@testing-library/react';
import { vi, describe, it, expect, beforeEach, afterEach } from 'vitest';

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
vi.mock('../../src/hooks/useTransformRegistry', () => ({ useTransformRegistry: () => ({ data: null, status: 'loading' }) }));

import ExecutableDynamicRunForm from '../../src/components/eseguibili/ExecutableDynamicRunForm';
import ProjectInputNode from '../../src/components/nodes/ProjectInputNode';
import { InputChangesList, blockingInputChanges } from '../../src/components/eseguibili/SourceStatus';
import { canChooseIdentity, chosenIdentityInputs } from '../../src/utils/beforeRun';
import useCanvasStore from '../../src/store/useCanvasStore';

const LABEL = 'Istituto (solo amministratori)';

const eseguibile = (adminOverride = true) => ({
  id: 'exe-1', name: 'Immobili', project_type: 'estrai', active_version: 2, active_version_id: 'v2',
  default_output_format: 'csv', can_edit: false, binding_warnings: [],
  manifest: { inputs: [{
    key: 'utente', label: 'Utente', kind: 'current_user', required: true, order: 1,
    transform: "utente.str.slice(1, 4) + '_IS'",
    admin_override: adminOverride, admin_override_label: adminOverride ? LABEL : null,
  }] },
});

const launch = async () => {
  const avvia = await screen.findByRole('button', { name: /avvia l'esecuzione/i });
  expect(avvia).not.toBeDisabled();
  fireEvent.click(avvia);
  await waitFor(() => expect(runMock).toHaveBeenCalledTimes(1));
  return runMock.mock.calls[0][1].inputs;
};

beforeEach(() => {
  vi.clearAllMocks();
  authState.user = { role: 'admin', user_id: 'a', username: 'anna' };
  runMock.mockResolvedValue({ async: true, task_id: 't1' });
  getTaskStatusMock.mockResolvedValue({ status: 'success', progress: 100, result: {} });
});
afterEach(() => { cleanup(); });

describe('1. «Prima di avviare»: valore scelto dagli amministratori', () => {
  it.each(['admin', 'superadmin'])('%s: campo facoltativo, il valore scritto si invia', async (roleName) => {
    authState.user = { role: roleName, user_id: 'a', username: 'anna' };
    render(<ExecutableDynamicRunForm eseguibile={eseguibile()} onClose={vi.fn()} />);
    const field = await screen.findByLabelText(LABEL);
    expect(field).not.toHaveAttribute('readonly');
    expect(field).toHaveAttribute('placeholder', 'anna');
    expect(screen.getByText(/Vuoto: si usa il tuo nome utente/)).toBeInTheDocument();
    fireEvent.change(field, { target: { value: ' 004_IS ' } });
    expect(await launch()).toEqual({ utente: '004_IS' });
  });

  it('admin: vuoto non invia nulla (vale il nome utente)', async () => {
    render(<ExecutableDynamicRunForm eseguibile={eseguibile()} onClose={vi.fn()} />);
    fireEvent.change(await screen.findByLabelText(LABEL), { target: { value: '   ' } });
    expect(await launch()).toEqual({});
  });

  it.each(['esecutore', 'user', 'viewer'])('%s: nessun campo, nessun valore inviato', async (roleName) => {
    authState.user = { role: roleName, user_id: 'u', username: 'ugo' };
    render(<ExecutableDynamicRunForm eseguibile={eseguibile()} onClose={vi.fn()} />);
    expect(await screen.findByRole('button', { name: /avvia l'esecuzione/i })).toBeInTheDocument();
    expect(screen.queryByLabelText(LABEL)).toBeNull();
    expect(screen.queryByText(/Vuoto: si usa il tuo nome utente/)).toBeNull();
    expect(screen.getByLabelText('Utente')).toHaveAttribute('readonly');
    expect(await launch()).toEqual({});
  });

  it('admin senza l\'opzione: riga in sola lettura come oggi', async () => {
    render(<ExecutableDynamicRunForm eseguibile={eseguibile(false)} onClose={vi.fn()} />);
    expect(await screen.findByLabelText('Utente')).toHaveAttribute('readonly');
    expect(screen.queryByText(/Vuoto: si usa il tuo nome utente/)).toBeNull();
    expect(await launch()).toEqual({});
  });

  it('la regola: solo admin/superadmin, solo con admin_override true, solo valori non vuoti', () => {
    const d = { input_key: 'utente', input_kind: 'current_user', admin_override: true };
    expect(canChooseIdentity(d, { role: 'admin' })).toBe(true);
    expect(canChooseIdentity(d, { role: 'superadmin' })).toBe(true);
    ['esecutore', 'user', 'viewer'].forEach(r => expect(canChooseIdentity(d, { role: r })).toBe(false));
    expect(canChooseIdentity(d, null)).toBe(false);
    expect(canChooseIdentity({ ...d, admin_override: 'true' }, { role: 'admin' })).toBe(false);
    expect(canChooseIdentity({ ...d, input_kind: 'text' }, { role: 'admin' })).toBe(false);
    expect(chosenIdentityInputs([d], { utente: ' x ' }, { role: 'admin' })).toEqual({ utente: 'x' });
    expect(chosenIdentityInputs([d], { utente: '' }, { role: 'admin' })).toEqual({});
    expect(chosenIdentityInputs([d], { utente: 'x' }, { role: 'user' })).toEqual({});
  });
});

// ---------------------------------------------------------------- 2. pannello

const mount = (data) => {
  const st = useCanvasStore.getState();
  st.reset();
  st.init('p1', 'estrai');
  const full = { input_kind: 'current_user', input_key: 'utente', label: 'Utente', ...data };
  st.loadGraph([{ id: 'u', type: 'project_input', position: { x: 0, y: 0 }, data: full }], []);
  return render(<ProjectInputNode id="u" data={full} />);
};
const nodeData = () => useCanvasStore.getState().nodes.find(n => n.id === 'u').data;
const CHECK = /Gli amministratori possono scegliere il valore/;

describe('2. pannello dell\'input', () => {
  it('la casella scrive admin_override; etichetta e valore di prova compaiono con la casella', () => {
    const { rerender } = mount({});
    expect(screen.queryByLabelText(/Etichetta del campo per gli amministratori/)).toBeNull();
    fireEvent.click(screen.getByLabelText(CHECK));
    expect(nodeData().admin_override).toBe(true);
    rerender(<ProjectInputNode id="u" data={nodeData()} />);
    const label = screen.getByLabelText(/Etichetta del campo per gli amministratori/);
    fireEvent.change(label, { target: { value: LABEL } });
    fireEvent.blur(label);
    expect(nodeData().admin_override_label).toBe(LABEL);
    const test = screen.getByLabelText(/Valore di prova scelto/);
    fireEvent.change(test, { target: { value: '009_IS' } });
    fireEvent.blur(test);
    expect(nodeData().preview_admin_value).toBe('009_IS');
  });

  it('togliere la casella toglie il valore di prova', () => {
    mount({ admin_override: true, preview_admin_value: '009_IS' });
    fireEvent.click(screen.getByLabelText(CHECK));
    expect(nodeData().admin_override).toBeNull();
    expect(nodeData().preview_admin_value).toBeNull();
  });

  it('cambiare tipo toglie opzione, etichetta e valore di prova', () => {
    mount({ admin_override: true, admin_override_label: LABEL, preview_admin_value: '009_IS' });
    fireEvent.change(screen.getByLabelText(/^Tipo/), { target: { value: 'text' } });
    expect(nodeData()).toMatchObject({ input_kind: 'text', admin_override: null, admin_override_label: null, preview_admin_value: null });
  });
});

// ---------------------------------------------------------------- 3. ripubblicazione

describe('3. ripubblicazione: righe solo informative', () => {
  const info = { input_key: 'utente', change: 'admin_override_removed', motivo: 'Gli amministratori non scelgono più', blocking: false };
  const removed = { input_key: 'cod', change: 'removed', motivo: 'Input tolto', blocking: true };

  it('non chiedono conferma; le righe senza «blocking» la chiedono come prima', () => {
    expect(blockingInputChanges([info])).toEqual([]);
    expect(blockingInputChanges([info, removed])).toEqual([removed]);
    expect(blockingInputChanges([{ input_key: 'x', change: 'removed', motivo: 'm' }])).toHaveLength(1);
  });

  it('si elencano a parte, «Da sapere»', () => {
    render(<InputChangesList changes={[info]} />);
    expect(screen.getByText(/Da sapere/)).toBeInTheDocument();
    expect(screen.getByText(info.motivo)).toBeInTheDocument();
    expect(screen.queryByText(/toglierebbe o cambierebbe/)).toBeNull();
  });
});
