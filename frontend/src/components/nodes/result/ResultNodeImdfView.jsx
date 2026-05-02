/**
 * ResultNodeImdfView — view del ResultNode quando upstream è api_enrich
 * con output_kind='imdf'.
 *
 * Estratto da ResultNode.jsx (Batch 2 step 2 split FE). Il componente si
 * monta solo quando isImdfMode=true (ramo ternario del parent); non ha
 * una prop isImdfMode perche' la sua presenza nel tree lo implica.
 *
 * Owna:
 *   - useImdfBundles (downloader: fetch/hydrate/select dei bundle)
 *   - openImdfModal (stato UI dei 3 modali anteprima: preview/manifest/validation)
 *   - la sezione visiva del ramo IMDF (BundleSlotCard + 4 bottoni)
 *   - i 4 modali strettamente IMDF (Preview, Manifest, Validation, BundlesManager)
 *
 * NON owna:
 *   - ValidationModal (aperta da handleExecute se validazione KO — view del parent)
 *   - RunHistoryModal (non IMDF-specific)
 *   - la logica di execute/preview/validation: ricevuta come callback onExecute.
 *
 * Props:
 *   hasOnExecute  boolean   — se mostrare il bottone Esegui (viene da store.editorActionsRef)
 *   onExecute     ()=>void  — handler Esegui (il parent owna validazione pre-esecuzione)
 *   nodeVariant   string    — estrai | manipola | arricchisci (classi CSS)
 *   executeLabel  string    — label del bottone Esegui
 */
import React, { useState, memo, lazy, Suspense } from 'react';
import BundleSlotCard from '../../imdf/BundleSlotCard';
// Lazy: ImdfPreviewModal pulls maplibre-gl + deck.gl (~1.27 MB raw).
// Caricato on-demand al click "Visualizza IMDF" — evita di pesare sul
// path eager dei 3 editor.
const ImdfPreviewModal = lazy(() => import('../../modals/ImdfPreviewModal'));
import ImdfManifestModal from '../../modals/ImdfManifestModal';
import ImdfValidationModal from '../../modals/ImdfValidationModal';
import ImdfBundlesManagerModal from '../../modals/ImdfBundlesManagerModal';
import { useImdfBundles } from './useImdfBundles';

const ResultNodeImdfView = memo(({
  hasOnExecute,
  onExecute,
  nodeVariant,
  executeLabel,
}) => {
  // Stato UI locale: quale dei 3 modali anteprima è aperto.
  const [openImdfModal, setOpenImdfModal] = useState(null); // 'preview' | 'manifest' | 'validation' | null

  const {
    bundles,
    loadingBundles,
    setSelectedBundleId,
    bundlesManagerOpen,
    setBundlesManagerOpen,
    bundleSwitcherOpen,
    setBundleSwitcherOpen,
    bundleTaskId,
    selectedBundle,
    isBundleFallback,
    imdfData,
    handleDeleteBundle,
  } = useImdfBundles({ isImdfMode: true });

  return (
    <>
      <div
        className="result-node__section result-node__section--imdf"
        style={{ background: 'rgba(124,58,237,0.08)', borderLeft: '3px solid #7C3AED', padding: '14px 14px', borderRadius: 6 }}
      >
        <div className="result-node__label" style={{ fontWeight: 700, color: '#7C3AED', marginBottom: 10 }}>
          📦 Bundle IMDF
          {!imdfData && (
            <span style={{ marginLeft: 8, fontSize: 11, opacity: 0.6, fontWeight: 400 }}>
              (esegui per generare i dati)
            </span>
          )}
        </div>

        {/* Card bundle attivo con switcher e link manager */}
        <BundleSlotCard
          loading={loadingBundles}
          bundle={selectedBundle}
          bundleTaskId={bundleTaskId}
          isFallback={isBundleFallback}
          bundles={bundles}
          switcherOpen={bundleSwitcherOpen}
          onToggleSwitcher={(e) => { e?.stopPropagation?.(); setBundleSwitcherOpen(v => !v); }}
          onSelectBundle={(tid) => {
            setSelectedBundleId(tid);
            setBundleSwitcherOpen(false);
          }}
          onOpenManager={(e) => {
            e?.stopPropagation?.();
            setBundlesManagerOpen(true);
            setBundleSwitcherOpen(false);
          }}
        />

        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <button
            type="button"
            disabled={!imdfData}
            onClick={(e) => { e.stopPropagation(); setOpenImdfModal('preview'); }}
            onMouseDown={(e) => e.stopPropagation()}
            className="result-node__filename-input"
            style={{ width: '100%', cursor: imdfData ? 'pointer' : 'not-allowed', textAlign: 'left', padding: '10px 14px', fontSize: 14, opacity: imdfData ? 1 : 0.5 }}
          >
            🗺️ Visualizza IMDF
          </button>
          <button
            type="button"
            disabled={!imdfData?.manifest}
            onClick={(e) => { e.stopPropagation(); setOpenImdfModal('manifest'); }}
            onMouseDown={(e) => e.stopPropagation()}
            className="result-node__filename-input"
            style={{ width: '100%', cursor: imdfData?.manifest ? 'pointer' : 'not-allowed', textAlign: 'left', padding: '10px 14px', fontSize: 14, opacity: imdfData?.manifest ? 1 : 0.5 }}
          >
            📋 Visualizza Manifest
          </button>
          <button
            type="button"
            disabled={!imdfData?.validation}
            onClick={(e) => { e.stopPropagation(); setOpenImdfModal('validation'); }}
            onMouseDown={(e) => e.stopPropagation()}
            className="result-node__filename-input"
            style={{ width: '100%', cursor: imdfData?.validation ? 'pointer' : 'not-allowed', textAlign: 'left', padding: '10px 14px', fontSize: 14, opacity: imdfData?.validation ? 1 : 0.5 }}
          >
            ⚠️ Visualizza Validation
          </button>

          {/* Bottone Esegui — anche in modalita' IMDF: senza Run dell'utente i modali sono vuoti. */}
          {hasOnExecute && (
            <button
              type="button"
              onClick={(e) => { e.stopPropagation(); onExecute(); }}
              onMouseDown={(e) => e.stopPropagation()}
              className={`result-node__execute-btn result-node__execute-btn--${nodeVariant}`}
              style={{ marginTop: 6, width: '100%' }}
            >
              <span className="result-node__execute-icon">▶️</span>
              <span>{executeLabel}</span>
            </button>
          )}
        </div>
      </div>

      {/* Modali IMDF: leggono dati dal bundle corrente (imdfData dallo store via hook) */}
      <Suspense fallback={null}>
        {openImdfModal === 'preview' && (
          <ImdfPreviewModal
            isOpen={true}
            onClose={() => setOpenImdfModal(null)}
            imdfData={imdfData}
          />
        )}
      </Suspense>
      <ImdfManifestModal
        isOpen={openImdfModal === 'manifest'}
        onClose={() => setOpenImdfModal(null)}
        manifest={imdfData?.manifest}
        siteId={imdfData?.site_id}
        publish={imdfData?.publish}
      />
      <ImdfValidationModal
        isOpen={openImdfModal === 'validation'}
        onClose={() => setOpenImdfModal(null)}
        validation={imdfData?.validation}
      />

      {/* Modal gestione cronologia bundle */}
      <ImdfBundlesManagerModal
        isOpen={bundlesManagerOpen}
        onClose={() => setBundlesManagerOpen(false)}
        bundles={bundles}
        selectedTaskId={bundleTaskId}
        onSelect={(tid) => {
          setSelectedBundleId(tid);
          setBundlesManagerOpen(false);
        }}
        onDelete={handleDeleteBundle}
      />
    </>
  );
});

ResultNodeImdfView.displayName = 'ResultNodeImdfView';

export default ResultNodeImdfView;
