/**
 * Contract tests for ResultNodeImdfView.
 *
 * Invarianti che questa view deve preservare:
 *   1. 3 bottoni "Visualizza ..." disabled quando imdfData manca:
 *      - Preview   disabled se !imdfData
 *      - Manifest  disabled se !imdfData?.manifest
 *      - Validation disabled se !imdfData?.validation
 *   2. Bottone Esegui visibile sse hasOnExecute=true; il click chiama onExecute.
 *   3. Apertura modali: click "Visualizza IMDF" → ImdfPreviewModal isOpen=true;
 *      "Visualizza Manifest" → ImdfManifestModal isOpen=true; "Visualizza Validation"
 *      → ImdfValidationModal isOpen=true. Uno alla volta (state 'preview'|'manifest'|'validation').
 *   4. I modali IMDF ricevono correttamente imdfData / imdfData.manifest / imdfData.validation.
 *   5. BundleSlotCard riceve bundles + selezione derivati dal hook downloader.
 *
 * Mockiamo i modali IMDF (rendering pesante, gia' coperti da altri contract test)
 * con shim che ri-emettono le loro prop in DOM ispezionabile.
 */

import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const {
  listImdfBundlesMock,
  getImdfBundleMetadataMock,
  deleteImdfBundleMock,
  useCanvasStoreMock,
  storeRef,
} = vi.hoisted(() => ({
  listImdfBundlesMock: vi.fn(),
  getImdfBundleMetadataMock: vi.fn(),
  deleteImdfBundleMock: vi.fn(),
  useCanvasStoreMock: vi.fn(),
  storeRef: { current: { projectId: 'p1', lastTaskResult: null, setLastTaskResult: vi.fn() } },
}));

vi.mock('../../src/api/imdf', () => ({
  listImdfBundles: listImdfBundlesMock,
  getImdfBundleMetadata: getImdfBundleMetadataMock,
  deleteImdfBundle: deleteImdfBundleMock,
}));

vi.mock('../../src/store/useCanvasStore', () => ({
  default: useCanvasStoreMock,
}));

// useToast: useImdfBundles ora ne consuma uno per il path errore
// handleDeleteBundle (alert -> toast). Stub no-op: questi test non
// esercitano il path errore.
vi.mock('../../src/hooks/useToast', () => ({
  useToast: () => ({
    error: vi.fn(),
    success: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
  }),
}));

// Shim dei modali: emettono isOpen + props chiave come data-attrs ispezionabili
vi.mock('../../src/components/modals/ImdfPreviewModal', () => ({
  default: ({ isOpen, imdfData }) => isOpen ? (
    <div data-testid="imdf-preview-modal" data-has-data={!!imdfData} />
  ) : null,
}));
vi.mock('../../src/components/modals/ImdfManifestModal', () => ({
  default: ({ isOpen, manifest, siteId }) => isOpen ? (
    <div data-testid="imdf-manifest-modal"
         data-has-manifest={!!manifest}
         data-site-id={siteId || ''} />
  ) : null,
}));
vi.mock('../../src/components/modals/ImdfValidationModal', () => ({
  default: ({ isOpen, validation }) => isOpen ? (
    <div data-testid="imdf-validation-modal" data-has-validation={!!validation} />
  ) : null,
}));
vi.mock('../../src/components/modals/ImdfBundlesManagerModal', () => ({
  default: ({ isOpen, bundles, selectedTaskId, onDelete }) => isOpen ? (
    <div data-testid="imdf-bundles-manager"
         data-bundle-count={bundles?.length || 0}
         data-selected-task-id={selectedTaskId || ''}>
      <button type="button" onClick={() => onDelete && onDelete('t-del')}>
        trigger-delete
      </button>
    </div>
  ) : null,
}));

// BundleSlotCard: shim minimale che espone bundleTaskId per assert
vi.mock('../../src/components/imdf/BundleSlotCard', () => ({
  default: ({ bundleTaskId, bundles, loading, isFallback }) => (
    <div data-testid="bundle-slot-card"
         data-bundle-task-id={bundleTaskId || ''}
         data-bundle-count={bundles?.length || 0}
         data-loading={String(!!loading)}
         data-fallback={String(!!isFallback)} />
  ),
}));

useCanvasStoreMock.mockImplementation((selector) => selector(storeRef.current));
useCanvasStoreMock.getState = () => storeRef.current;

import ResultNodeImdfView from '../../src/components/nodes/result/ResultNodeImdfView';

beforeEach(() => {
  listImdfBundlesMock.mockReset().mockResolvedValue([]);
  getImdfBundleMetadataMock.mockReset().mockResolvedValue({});
  deleteImdfBundleMock.mockReset().mockResolvedValue({ ok: true });
  storeRef.current = { projectId: 'p1', lastTaskResult: null, setLastTaskResult: vi.fn() };
  useCanvasStoreMock.mockImplementation((selector) => selector(storeRef.current));
  useCanvasStoreMock.getState = () => storeRef.current;
  globalThis.__frontendConsolePolicy.allowError = true;
});

const renderView = (overrides = {}) => {
  const onExecute = vi.fn();
  const props = {
    hasOnExecute: true,
    onExecute,
    nodeVariant: 'arricchisci',
    executeLabel: 'Esegui Arricchimento',
    ...overrides,
  };
  const utils = render(<ResultNodeImdfView {...props} />);
  return { ...utils, onExecute, props };
};

describe('ResultNodeImdfView — contract', () => {
  it('senza imdfData: i 3 bottoni Visualizza sono disabled; header mostra hint', () => {
    renderView();
    expect(screen.getByText(/esegui per generare i dati/i)).toBeInTheDocument();

    const preview = screen.getByRole('button', { name: /visualizza imdf/i });
    const manifest = screen.getByRole('button', { name: /visualizza manifest/i });
    const validation = screen.getByRole('button', { name: /visualizza validation/i });
    expect(preview).toBeDisabled();
    expect(manifest).toBeDisabled();
    expect(validation).toBeDisabled();
  });

  it('con imdfData completo: bottoni abilitati e aprono i modali corretti', async () => {
    const user = userEvent.setup();
    storeRef.current = {
      projectId: 'p1',
      lastTaskResult: {
        imdf: {
          bundle_task_id: 'live-1',
          manifest: { venue: [] },
          validation: { errors: [] },
          site_id: 'LO2244',
        },
      },
      setLastTaskResult: vi.fn(),
    };
    useCanvasStoreMock.mockImplementation((selector) => selector(storeRef.current));
    useCanvasStoreMock.getState = () => storeRef.current;

    renderView();

    const preview = screen.getByRole('button', { name: /visualizza imdf/i });
    const manifest = screen.getByRole('button', { name: /visualizza manifest/i });
    const validation = screen.getByRole('button', { name: /visualizza validation/i });
    expect(preview).toBeEnabled();
    expect(manifest).toBeEnabled();
    expect(validation).toBeEnabled();

    // Click Preview → apre solo ImdfPreviewModal
    await user.click(preview);
    expect(screen.getByTestId('imdf-preview-modal')).toBeInTheDocument();
    expect(screen.queryByTestId('imdf-manifest-modal')).toBeNull();
    expect(screen.queryByTestId('imdf-validation-modal')).toBeNull();

    // Click Manifest → chiude Preview, apre Manifest (uno alla volta)
    await user.click(manifest);
    expect(screen.queryByTestId('imdf-preview-modal')).toBeNull();
    const manifestModal = screen.getByTestId('imdf-manifest-modal');
    expect(manifestModal).toHaveAttribute('data-has-manifest', 'true');
    expect(manifestModal).toHaveAttribute('data-site-id', 'LO2244');

    // Click Validation → chiude Manifest, apre Validation
    await user.click(validation);
    expect(screen.queryByTestId('imdf-manifest-modal')).toBeNull();
    expect(screen.getByTestId('imdf-validation-modal'))
      .toHaveAttribute('data-has-validation', 'true');
  });

  it('hasOnExecute=true: il bottone Esegui compare e il click invoca onExecute', async () => {
    const user = userEvent.setup();
    const { onExecute } = renderView({ hasOnExecute: true, executeLabel: 'Esegui Pipeline' });

    const execBtn = screen.getByRole('button', { name: /esegui pipeline/i });
    expect(execBtn).toBeInTheDocument();
    await user.click(execBtn);
    expect(onExecute).toHaveBeenCalledTimes(1);
  });

  it('hasOnExecute=false: il bottone Esegui NON viene renderizzato', () => {
    renderView({ hasOnExecute: false });
    expect(screen.queryByRole('button', { name: /esegui/i })).toBeNull();
  });

  it('BundleSlotCard riceve bundles/selezione derivati dal hook downloader', async () => {
    listImdfBundlesMock.mockResolvedValue([
      { task_id: 'b2' },
      { task_id: 'b1' },
    ]);

    renderView();

    // Attendi propagazione async del primo effect del hook
    const card = await screen.findByTestId('bundle-slot-card');
    // Dopo auto-select (no live → arr[0]='b2'):
    //   bundleTaskId='b2', bundleCount=2, isFallback=true, loading=false
    // La transizione loading→data avviene in un tick async; uso waitFor implicito
    // attraverso getByTestId + lettura finale.
    await vi.waitFor(() => {
      expect(card.getAttribute('data-bundle-count')).toBe('2');
      expect(card.getAttribute('data-bundle-task-id')).toBe('b2');
      expect(card.getAttribute('data-fallback')).toBe('true');
    });
  });
});
