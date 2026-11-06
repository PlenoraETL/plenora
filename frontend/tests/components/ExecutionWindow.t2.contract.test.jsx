/**
 * Fase 5 T2 — finestra di esecuzione, versioni degli eseguibili, catalogo
 * delle operazioni, modifiche ottimistiche della dashboard.
 *
 *  - ExecutionModal (R4.11, R9.4, R9.8, R9-N3, R9-N4): Annulla per ogni
 *    tipo di progetto, finestra chiudibile durante l'esecuzione ("Continua
 *    in secondo piano"), rapporto errori scaricato con l'errore mostrato.
 *  - EseguibileVersionsModal (R9.15): Ripristina e Ripubblica chiedono conferma.
 *  - useTransformRegistry (R4.20, N13): errore con Riprova, poi le operazioni.
 *  - useProjectListData (R9.13): l'errore di una modifica vecchia non annulla
 *    quella piu' recente riuscita.
 */
import React from 'react';
import { render, screen, fireEvent, waitFor, renderHook, act } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const {
  downloadProjectFileMock, activateMock, republishMock, listVersionsMock, registryMock,
  apiMocks,
} = vi.hoisted(() => ({
  downloadProjectFileMock: vi.fn(),
  activateMock: vi.fn(),
  republishMock: vi.fn(),
  listVersionsMock: vi.fn(),
  registryMock: vi.fn(),
  apiMocks: {
    listProjects: vi.fn(), listConnections: vi.fn(), listApiConnections: vi.fn(), listEseguibili: vi.fn(),
  },
}));

vi.mock('../../src/api/projects', () => ({ downloadProjectFile: downloadProjectFileMock }));
vi.mock('../../src/api', () => ({
  activateEseguibileVersion: activateMock,
  republishEseguibile: republishMock,
  listEseguibileVersions: listVersionsMock,
  ...apiMocks,
}));
vi.mock('../../src/api/transforms', () => ({ getTransformRegistry: registryMock }));
vi.mock('../../src/api/features', () => ({ listFeatureConnections: vi.fn(async () => ({ connections: [] })) }));
vi.mock('../../src/api/files', () => ({ getFiles: vi.fn(async () => ({ files: [] })) }));
vi.mock('../../src/api/schedules', () => ({ listSchedules: vi.fn(async () => ({ schedules: [] })) }));
vi.mock('../../src/api/datasources', () => ({ listDataSources: vi.fn(async () => ({ datasources: [] })) }));
// UI-1 (C4): API, data source e schedule come conteggi.
vi.mock('../../src/api/dashboard', () => ({ getDashboardCounts: vi.fn(async () => ({ api_connections: 0, datasources: 0, schedules_active: 0, schedules_by_project: {} })) }));
vi.mock('../../src/api/logRuns', () => ({ getLatestRunByProject: vi.fn(async () => { throw new Error('log giu'); }) }));
vi.mock('../../src/hooks/useToast', () => ({ useToast: () => globalThis.__frontendTestHelpers.createToastMock(vi) }));

import ExecutionModal from '../../src/components/modals/ExecutionModal';
import EseguibileVersionsModal from '../../src/components/eseguibili/EseguibileVersionsModal';
import { useTransformRegistry } from '../../src/hooks/useTransformRegistry';
import { resetTransformRegistry } from '../../src/utils/transformRegistry';
import { useProjectListData } from '../../src/components/projects/useProjectListData';

beforeEach(() => { vi.clearAllMocks(); });

describe('ExecutionModal', () => {
  it.each(['estrai', 'manipola', 'arricchisci'])('%s: Annulla durante l\'esecuzione e chiusura in secondo piano', (projectType) => {
    const onCancel = vi.fn();
    const onClose = vi.fn();
    render(<ExecutionModal isOpen isExecuting projectType={projectType} onCancel={onCancel} onClose={onClose} progress={30} progressMessage="passo 1" taskStatus="progress" />);
    fireEvent.click(screen.getByRole('button', { name: /annulla/i }));
    expect(onCancel).toHaveBeenCalled();
    const background = screen.getByRole('button', { name: 'Continua in secondo piano' });
    expect(background).not.toBeDisabled();
    fireEvent.click(background);
    expect(onClose).toHaveBeenCalledWith();
    expect(screen.getByText('passo 1')).toBeInTheDocument();
  });

  it('backgroundable=false (Eseguibili): durante l\'esecuzione non si chiude', () => {
    render(<ExecutionModal isOpen isExecuting backgroundable={false} onClose={vi.fn()} onCancel={vi.fn()} />);
    expect(screen.getByText('Chiudi', { selector: 'button' })).toBeDisabled();
  });

  it('rapporto errori: un download fallito mostra il motivo (R9-N3)', async () => {
    downloadProjectFileMock.mockRejectedValue({ response: { status: 410, data: {} } });
    render(
      <ExecutionModal
        isOpen
        isExecuting={false}
        onClose={vi.fn()}
        projectType="arricchisci"
        result={{ success: true, project_id: 'p1', error_report: 'out/report.csv', enrichment_stats: { errors: 2, total_rows: 10 } }}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /rapporto degli errori/i }));
    expect(await screen.findByText(/non è più disponibile/i)).toBeInTheDocument();
  });

  it('download del risultato: errore mostrato, pulsante spento durante il download', () => {
    const { rerender } = render(<ExecutionModal isOpen isExecuting={false} onClose={vi.fn()} onDownload={vi.fn()} downloading result={{ success: true }} />);
    expect(screen.getByRole('button', { name: /scaricamento/i })).toBeDisabled();
    rerender(<ExecutionModal isOpen isExecuting={false} onClose={vi.fn()} onDownload={vi.fn()} downloadError="Il file non è più disponibile" result={{ success: true }} />);
    expect(screen.getByRole('alert')).toHaveTextContent('Il file non è più disponibile');
  });
});

describe('EseguibileVersionsModal — conferma (R9.15)', () => {
  const eseguibile = { id: 'e1', name: 'Pulizia' };
  beforeEach(() => {
    listVersionsMock.mockResolvedValue([
      { id: 'v2', version_number: 2, active: true, project_type: 'estrai', inputs_count: 1, created_at: '2026-11-01T10:00:00Z' },
      { id: 'v1', version_number: 1, active: false, project_type: 'estrai', inputs_count: 1, created_at: '2026-10-01T10:00:00Z' },
    ]);
  });
  it('Ripristina chiede conferma; annullando non parte niente', async () => {
    render(<EseguibileVersionsModal eseguibile={eseguibile} onClose={vi.fn()} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Ripristina' }));
    expect(await screen.findByText('Ripristinare la versione 1?')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Annulla' }));
    expect(activateMock).not.toHaveBeenCalled();
  });
  it('Ripubblica chiede conferma; confermando parte una volta', async () => {
    republishMock.mockResolvedValue({ active_version: 3 });
    render(<EseguibileVersionsModal eseguibile={eseguibile} onClose={vi.fn()} />);
    await screen.findByText('Versione 2');
    fireEvent.click(screen.getByRole('button', { name: /ripubblica dal progetto/i }));
    expect(await screen.findByText('Ripubblicare dal progetto?')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Ripubblica' }));
    await waitFor(() => expect(republishMock).toHaveBeenCalledTimes(1));
  });
});

describe('useTransformRegistry — catalogo non arrivato (R4.20, N13)', () => {
  it('errore con Riprova, poi le operazioni; chi e\' montato le riceve tutte', async () => {
    resetTransformRegistry();
    registryMock.mockRejectedValueOnce(new Error('registry giu')).mockResolvedValueOnce({ transforms: { filter: { name: 'Filtra', category: 'base' } } });
    const first = renderHook(() => useTransformRegistry());
    await waitFor(() => expect(first.result.current.status).toBe('error'));
    const second = renderHook(() => useTransformRegistry({ enabled: false }));
    await act(async () => { await first.result.current.reload(); });
    expect(first.result.current.data?.transforms?.filter).toBeTruthy();
    expect(second.result.current.data?.transforms?.filter).toBeTruthy();
    resetTransformRegistry();
  });
});

describe('useProjectListData — ottimistico per campo (R9.13) e stato delle esecuzioni (R4.9)', () => {
  beforeEach(() => {
    apiMocks.listProjects.mockResolvedValue({ projects: [{ id: 'p1', name: 'A', project_type: 'estrai' }] });
    apiMocks.listConnections.mockResolvedValue({ connections: [] });
    apiMocks.listApiConnections.mockResolvedValue({ api_connections: [] });
    apiMocks.listEseguibili.mockResolvedValue([]);
  });

  it('rinomina A->B lenta che fallisce dopo B->C riuscita: resta C, non torna A', async () => {
    const { result } = renderHook(() => useProjectListData());
    await waitFor(() => expect(result.current.projects).toHaveLength(1));
    let failFirst;
    const p1 = result.current.projects[0];
    let first;
    act(() => {
      first = result.current.updateProjectOptimistic(p1, { name: 'B' }, () => new Promise((_, rej) => { failFirst = rej; }));
    });
    const afterB = result.current.projects[0];
    await act(async () => { await result.current.updateProjectOptimistic(afterB, { name: 'C' }, () => Promise.resolve()); });
    await act(async () => { failFirst(new Error('rete')); await first.catch(() => {}); });
    expect(result.current.projects[0].name).toBe('C');
  });

  it('una sola modifica che fallisce torna al valore confermato', async () => {
    const { result } = renderHook(() => useProjectListData());
    await waitFor(() => expect(result.current.projects).toHaveLength(1));
    await act(async () => {
      await result.current.updateProjectOptimistic(result.current.projects[0], { name: 'X' }, () => Promise.reject(new Error('no'))).catch(() => {});
    });
    expect(result.current.projects[0].name).toBe('A');
  });

  it('stato delle esecuzioni non caricato: "non disponibile", non "Mai eseguito"', async () => {
    const { result } = renderHook(() => useProjectListData());
    await waitFor(() => expect(result.current.projects).toHaveLength(1));
    expect(result.current.runStatusUnavailable).toBe(true);
    expect(result.current.runStatusFor('p1')).toEqual({ status: 'unavailable' });
    expect(result.current.failures).toContain('stato delle esecuzioni');
  });
});
