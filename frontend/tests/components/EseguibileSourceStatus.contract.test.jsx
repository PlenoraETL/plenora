/**
 * N1 (09/11/2026): un Eseguibile pubblicato che non corrisponde piu' al suo
 * progetto si vede, in tre posti con gli stessi testi (components/
 * eseguibili/SourceStatus):
 *  - editor del progetto sorgente: un avviso per Eseguibile cambiato, con
 *    «Pubblica nuova versione» (solo per chi puo' ripubblicare);
 *  - lista degli Eseguibili: badge dal campo `source_changed` della lista;
 *  - finestra delle versioni: anteprima della ripubblicazione con i
 *    collegamenti degli input che non troverebbero piu' il campo; con
 *    problemi «Ripubblica dal progetto» e' disabilitato.
 * Caso reale: binding cod_ist -> filters.1.value con un solo filtro.
 */
import React from 'react';
import { render, screen, waitFor, cleanup } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const { api, toastMock } = vi.hoisted(() => ({
  api: {
    listSourceStatusOfProject: vi.fn(),
    getEseguibileSourceStatus: vi.fn(),
    listEseguibileVersions: vi.fn(),
    republishEseguibile: vi.fn(),
    activateEseguibileVersion: vi.fn(),
    listEseguibili: vi.fn(),
    updateEseguibile: vi.fn(),
    deleteEseguibile: vi.fn(),
    listProjects: vi.fn(),
  },
  toastMock: globalThis.__frontendTestHelpers.createToastMock(vi),
}));
vi.mock('../../src/api', () => api);
vi.mock('../../src/hooks/useToast', () => ({ useToast: () => toastMock }));
vi.mock('../../src/context/AuthContext', () => ({
  useAuth: () => ({ user: { role: 'admin', username: 'admin', group_id: 'g1' } }),
}));
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return { ...actual, useNavigate: () => vi.fn() };
});
vi.mock('../../src/components/AppSidebar', () => ({ default: () => null }));
vi.mock('../../src/components/eseguibili/EseguibileExecuteWizard', () => ({ default: () => null }));
vi.mock('../../src/components/eseguibili/PublishEseguibileModal', () => ({ default: () => null }));
vi.mock('../../src/components/shared/AllowedGroupsSelector', () => ({ default: () => null }));

import PublishedExecutablesNotice from '../../src/components/eseguibili/PublishedExecutablesNotice';
import EseguibileVersionsModal from '../../src/components/eseguibili/EseguibileVersionsModal';
import EseguibiliList from '../../src/pages/EseguibiliList';
import { SOURCE_CHANGED_BADGE } from '../../src/components/eseguibili/SourceStatus';

const ADMIN = { role: 'admin', group_id: 'g1' };
const status = (changed, extra = {}) => ({
  eseguibile_id: changed ? 'exec_1' : 'exec_2', name: changed ? 'SC_A_patrimonio_unita_AMM' : 'Altro',
  version: 1, published_at: '2026-08-05T08:35:50+00:00', state: changed ? 'changed' : 'same', changed,
  binding_problems: [], publish_error: null, ...extra,
});
const BROKEN = { input_key: 'cod_ist', target_node_id: 'node_1784712505489', target_field: 'filters.1.value', motivo: 'Il filtro 2 non esiste piu\'' };

beforeEach(() => { vi.clearAllMocks(); api.listEseguibileVersions.mockResolvedValue([]); });
afterEach(() => cleanup());

describe('N1: editor del progetto sorgente', () => {
  it('un avviso solo per l\'Eseguibile cambiato, con nome, versione e azione', async () => {
    api.listSourceStatusOfProject.mockResolvedValue([status(true), status(false)]);
    render(<PublishedExecutablesNotice projectId="p1" user={ADMIN} />);
    expect(await screen.findByText(/L'Eseguibile «SC_A_patrimonio_unita_AMM» \(versione 1 del .*\) usa una copia del progetto precedente/)).toBeTruthy();
    expect(screen.queryByText(/«Altro»/)).toBeNull();
    expect(screen.getAllByRole('button', { name: 'Pubblica nuova versione' })).toHaveLength(1);
  });

  it('nessun avviso con la stessa impronta; nessuna richiesta per chi non ripubblica', async () => {
    api.listSourceStatusOfProject.mockResolvedValue([status(false)]);
    render(<PublishedExecutablesNotice projectId="p1" user={ADMIN} />);
    await waitFor(() => expect(api.listSourceStatusOfProject).toHaveBeenCalledWith('p1'));
    expect(screen.queryByText(/usa una copia del progetto/)).toBeNull();
    cleanup();
    api.listSourceStatusOfProject.mockClear();
    render(<PublishedExecutablesNotice projectId="p1" user={{ role: 'user', group_id: 'g1' }} />);
    expect(api.listSourceStatusOfProject).not.toHaveBeenCalled();
  });
});

describe('N1: anteprima della ripubblicazione', () => {
  it('elenca ogni collegamento rotto con il motivo e blocca «Ripubblica»', async () => {
    api.getEseguibileSourceStatus.mockResolvedValue(status(true, { binding_problems: [BROKEN, { ...BROKEN, input_key: 'anno', motivo: 'nodo inesistente' }] }));
    render(<EseguibileVersionsModal eseguibile={{ id: 'exec_1', name: 'SC_A' }} onClose={() => {}} />);
    expect(await screen.findByText(/Il filtro 2 non esiste piu'/)).toBeTruthy();
    expect(screen.getByText(/nodo inesistente/)).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Ripubblica dal progetto' })).toBeDisabled();
  });

  it('progetto invariato: nessuna anteprima, ripubblicazione libera', async () => {
    api.getEseguibileSourceStatus.mockResolvedValue(status(false));
    render(<EseguibileVersionsModal eseguibile={{ id: 'exec_2', name: 'Altro' }} onClose={() => {}} />);
    await waitFor(() => expect(api.getEseguibileSourceStatus).toHaveBeenCalledWith('exec_2'));
    expect(screen.queryByText(SOURCE_CHANGED_BADGE, { exact: false })).toBeNull();
    expect(screen.getByRole('button', { name: 'Ripubblica dal progetto' })).not.toBeDisabled();
  });
});

describe('N1: lista degli Eseguibili', () => {
  it('badge sul solo Eseguibile con il progetto cambiato', async () => {
    const base = { description: '', default_output_format: 'xlsx', project_type: 'estrai', active_version: 1, inputs_count: 1, can_edit: true };
    api.listEseguibili.mockResolvedValue([
      { ...base, id: 'exec_1', name: 'Cambiato', source_changed: true },
      { ...base, id: 'exec_2', name: 'Uguale', source_changed: false },
    ]);
    render(<EseguibiliList />);
    await screen.findByText('Cambiato');
    expect(screen.getAllByText(SOURCE_CHANGED_BADGE, { exact: false })).toHaveLength(1);
    expect(screen.getAllByRole('button', { name: 'Pubblica nuova versione' })).toHaveLength(1);
  });
});
