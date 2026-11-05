/**
 * Input «Utente che esegue» (current_user) e tabella dei tipi di input.
 *
 *  1. La tabella del frontend (utils/runtimeInputKinds) e' quella del backend
 *     (contratto generato: backend/tests2/contracts/runtime_input_kinds.json).
 *  2. «Esegui» del progetto si blocca con un messaggio chiaro (nessun utente
 *     dall'editor); il glossario ha il nome italiano del tipo.
 *  3. «Prima di avviare»: riga in sola lettura con lo username di chi e'
 *     collegato; il valore non entra MAI nel payload (lo mette il server).
 */
import React from 'react';
import fs from 'node:fs';
import path from 'node:path';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import { describe, it, expect, vi, afterEach } from 'vitest';

vi.mock('../../src/context/AuthContext', () => ({
  useAuth: () => ({ user: { username: 'mario.rossi' } }),
}));

import BeforeRunModal from '../../src/components/editor/BeforeRunModal';
import {
  RUNTIME_INPUT_KINDS, RUNTIME_INPUT_KINDS_BY_MODE, isServerInjectedKind, projectRunBlockReason, serverInputNodes,
  unsupportedInputNodes,
} from '../../src/utils/runtimeInputKinds';
import {
  beforeRunReasons, initialInputValues, inputsComplete, runInputErrors, runInputs,
} from '../../src/utils/beforeRun';
import { unsupportedInputsResult } from '../../src/utils/backendValidationResult';
import { INPUT_KIND_LABELS } from '../../src/utils/glossary';

const CONTRACT = JSON.parse(fs.readFileSync(
  path.resolve(import.meta.dirname, '../../../backend/tests2/contracts/runtime_input_kinds.json'), 'utf-8'));

afterEach(() => { cleanup(); });

const n = (id, data) => ({ id, type: 'project_input', position: { x: 0, y: 0 }, data });
const USER_DEF = { input_key: 'utente', label: 'Utente', input_kind: 'current_user', required: true, default_value: null };
const YEAR_DEF = { input_key: 'anno', label: 'Anno', input_kind: 'integer', required: true };

describe('one table of runtime input kinds (backend parity)', () => {
  it('the frontend table is the backend contract', () => {
    expect(RUNTIME_INPUT_KINDS.map(k => ({ ...k, modes: [...k.modes] }))).toEqual(CONTRACT);
  });

  it('every kind has an Italian label; only current_user is set by the server', () => {
    expect(Object.keys(INPUT_KIND_LABELS)).toEqual(RUNTIME_INPUT_KINDS.map(k => k.kind));
    expect(INPUT_KIND_LABELS.current_user).toBe('Utente che esegue');
    expect(RUNTIME_INPUT_KINDS.filter(k => isServerInjectedKind(k.kind)).map(k => k.kind)).toEqual(['current_user']);
    Object.values(RUNTIME_INPUT_KINDS_BY_MODE).forEach(kinds => expect(kinds).toContain('current_user'));
  });
});

describe('«Utente che esegue» as a one-row source (Manipola only)', () => {
  it('one role rule drives handle, graph and bindable inputs', async () => {
    const { projectInputRole, projectInputHandle, canBeSource } = await import('../../src/utils/runtimeInputKinds');
    const { isScalarProjectInputNode, serializedBindingsForInput } = await import('../../src/utils/runtimeBindingEdges');
    const { nodeRole } = await import('../../src/utils/canvasGraph');
    const source = n('u', { input_kind: 'current_user', as_source: true, source_column: 'utente' });
    const param = n('p', { input_kind: 'current_user' });
    expect([projectInputRole(source), projectInputRole(param)]).toEqual(['source', 'parameter']);
    expect([projectInputHandle(source.data), projectInputHandle(param.data), projectInputHandle({ input_kind: 'connection' })])
      .toEqual(['output', 'parameter', null]);
    expect([isScalarProjectInputNode(source), isScalarProjectInputNode(param)]).toEqual([false, true]);
    expect([nodeRole(source), nodeRole(param)]).toEqual(['source', 'off']);
    expect(serializedBindingsForInput(source, [])).toEqual([]);
    expect(['estrai', 'manipola', 'arricchisci'].map(m => canBeSource(m, 'current_user'))).toEqual([false, true, false]);
    expect(canBeSource('manipola', 'text')).toBe(false);
  });
});

describe('project run from the editor: only a test run, with a clear message for who cannot', () => {
  it.each(['estrai', 'manipola', 'arricchisci'])('%s', (mode) => {
    const nodes = [n('u', { input_kind: 'current_user', label: 'Utente' }), n('t', { input_kind: 'text' })];
    expect(projectRunBlockReason(mode, 'current_user')).toBe('server');
    // Non blocca piu' da solo: diventa l'esecuzione di prova (serverInputNodes).
    expect(unsupportedInputNodes(nodes, mode)).toEqual([]);
    expect(serverInputNodes(nodes).map(x => x.id)).toEqual(['u']);
    const [issue] = unsupportedInputsResult(serverInputNodes(nodes), mode).issues;
    expect(issue.id).toBe('RUNTIME_IDENTITY_REQUIRED');
    expect(issue.message).toMatch(/«Utente».*Utente che esegue.*esecuzione di prova.*modificare il progetto.*Eseguibile/);
  });
});

describe('«Prima di avviare»: read-only row, never in the payload', () => {
  it('the rule (utils/beforeRun) ignores server-set inputs', () => {
    const defs = [USER_DEF, YEAR_DEF];
    expect(initialInputValues(defs)).toEqual({ anno: '' });
    expect(runInputErrors(defs, { anno: '2024' })).toEqual({});
    expect(inputsComplete(defs, { anno: '2024' })).toBe(true);
    // Anche se qualcuno scrive un valore, non parte mai.
    expect(runInputs(defs, { anno: '2024', utente: 'anna.bianchi' })).toEqual({ anno: 2024 });
    expect(beforeRunReasons({ inputDefinitions: [USER_DEF] })).toEqual(['inputs']);
  });

  it('the window shows the signed-in username read-only and launches without it', () => {
    const onLaunch = vi.fn();
    render(<BeforeRunModal
      request={{ id: 1, mode: 'estrai', inputDefinitions: [USER_DEF, YEAR_DEF], user: null, values: { utente: 'anna.bianchi' } }}
      onLaunch={onLaunch} onCancel={() => {}} />);
    const row = screen.getByLabelText('Utente');
    expect(row).toHaveValue('mario.rossi');
    expect(row).toHaveAttribute('readonly');
    expect(screen.getByText(/lo imposta il server con l'utente che avvia/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/Anno/), { target: { value: '2024' } });
    fireEvent.click(screen.getByRole('button', { name: /Avvia l'esecuzione/ }));
    expect(onLaunch).toHaveBeenCalledTimes(1);
    expect(onLaunch.mock.calls[0][0].inputs).toEqual({ anno: 2024 });
  });
});
