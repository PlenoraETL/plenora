/**
 * «Utente che esegue» negli Estrai a monte (backend runtime_identity,
 * sezione NEGLI ESTRAI A MONTE, 10/11/2026).
 *
 *  1. Editor: un nodo project_source annotato dal server
 *     (`data.upstream_identity`) fa di «Esegui» l'esecuzione di prova, con
 *     lo username di prova degli Estrai a monte (uno solo).
 *  2. Eseguibile: gli input a monte vivi del dettaglio
 *     (`upstream_identity_inputs`) sono righe in sola lettura; l'admin con
 *     `admin_override` sceglie il valore con la chiave «progetto::input».
 */
import React from 'react';
import { render, screen, cleanup } from '@testing-library/react';
import { describe, it, expect, afterEach } from 'vitest';

import { serverInputNodes, testRunIdentity, upstreamIdentityNodes } from '../../src/utils/runtimeInputKinds';
import { canChooseIdentity, chosenIdentityInputs, runInputs } from '../../src/utils/beforeRun';
import { upstreamDefinitions } from '../../src/components/eseguibili/ExecutableDynamicRunForm';
import BeforeRunModal from '../../src/components/editor/BeforeRunModal';

afterEach(() => { cleanup(); });

const row = (extra = {}) => ({
  key: 'e1::utente', input_key: 'utente', project_id: 'e1', project_name: 'Estrai sedi',
  label: 'Utente', kind: 'current_user', transform: null, admin_override: false, admin_override_label: null,
  ...extra,
});

const source = (inputs, id = 'ps') => ({
  id, type: 'project_source', data: { source_project_id: 'e1', upstream_identity: { via: ['Estrai sedi'], inputs } },
});

describe('1. editor', () => {
  it('a project_source with an upstream identity turns Esegui into the test run', () => {
    const nodes = [source([row({ preview_username: 'E0071' })]), { id: 'out', type: 'result', data: {} }];
    expect(upstreamIdentityNodes(nodes).map(n => n.id)).toEqual(['ps']);
    expect(serverInputNodes(nodes)).toHaveLength(1);
    expect(testRunIdentity(nodes)).toEqual({ username: 'E0071', problem: null });
  });

  it('one test username for the project and the upstream projects', () => {
    const own = { id: 'u', type: 'project_input', data: { input_kind: 'current_user', preview_username: 'A' } };
    expect(testRunIdentity([own, source([row({ preview_username: 'B' })])]).problem).toBe('conflict');
    expect(testRunIdentity([source([row()])]).problem).toBe('missing');
  });

  it('without the annotation nothing changes', () => {
    expect(serverInputNodes([{ id: 'ps', type: 'project_source', data: { source_project_id: 'e2' } }])).toEqual([]);
  });
});

describe('2. Eseguibile', () => {
  const admin = { username: 'capo', role: 'admin' };
  const esecutore = { username: 'E0041', role: 'esecutore' };

  it('upstream inputs become read-only definitions with their key', () => {
    const [d] = upstreamDefinitions({ upstream_identity_inputs: [row()] });
    expect(d).toMatchObject({ input_key: 'e1::utente', input_kind: 'current_user', required: false,
      label: 'Utente (Estrai sedi)', upstream: { project_id: 'e1', project_name: 'Estrai sedi' } });
    // Mai nel payload del client (lo mette il server).
    expect(runInputs([d], {})).toEqual({});
    expect(canChooseIdentity(d, admin)).toBe(false);
  });

  it('the admin chooses the upstream value only with admin_override', () => {
    const [d] = upstreamDefinitions({ upstream_identity_inputs: [row({ admin_override: true })] });
    expect(chosenIdentityInputs([d], { 'e1::utente': ' 004_IS ' }, admin)).toEqual({ 'e1::utente': '004_IS' });
    expect(chosenIdentityInputs([d], { 'e1::utente': '004_IS' }, esecutore)).toEqual({});
  });

  it('the window says the filter is in the upstream project', () => {
    const definitions = upstreamDefinitions({ upstream_identity_inputs: [row()] });
    render(
      <BeforeRunModal
        request={{ id: 1, mode: 'manipola', inputDefinitions: definitions, user: null, values: {} }}
        onLaunch={() => {}}
        onCancel={() => {}}
      />,
    );
    expect(screen.getByText(/Filtro del progetto a monte «Estrai sedi»/)).toBeInTheDocument();
    expect(screen.getByLabelText('Utente (Estrai sedi)')).toHaveAttribute('readonly');
  });
});

describe('3. guida', () => {
  it('the guide explains the upstream filter as the backend does', async () => {
    const { UTENTE_CHE_ESEGUE_GUIDE } = await import('../../src/utils/guides/utenteCheEsegue');
    const section = UTENTE_CHE_ESEGUE_GUIDE.sections.find(s => s.title === 'Il filtro negli Estrai a monte');
    const body = JSON.stringify(section);
    [/si riesegue solo per quella esecuzione/, /non si usa mai al posto di quello filtrato/,
      /finché chi lo gestisce non lo ripubblica/, /Arricchisci non riesegue/].forEach(re => expect(body).toMatch(re));
  });
});
