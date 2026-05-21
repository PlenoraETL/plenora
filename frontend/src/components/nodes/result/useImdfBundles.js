/**
 * useImdfBundles — "downloader" del ResultNode in modalita' IMDF.
 *
 * Incapsula fetch, hydration e selezione dei bundle IMDF esposti dal
 * backend.
 *
 * Cosa gestisce:
 *   - lista bundle del progetto (listImdfBundles)
 *   - auto-selezione del piu' recente quando non c'e' live task
 *   - hydration metadata via getImdfBundleMetadata quando lo store
 *     non ha manifest per il bundle attivo
 *   - delete lato server + aggiornamento lista locale
 *   - stato UI minimale per il bundle-switcher (open/close) e manager
 *
 * Cosa NON gestisce:
 *   - l'apertura dei modal IMDF (preview/manifest/validation): quella e'
 *     UI state della view, resta nel componente che renderizza i bottoni
 *   - la scelta isImdfMode: ricevuta come input, derivata dal chiamante
 *     ispezionando l'upstream
 *
 * Contract:
 *   Input : { isImdfMode: boolean }
 *   Output:
 *     - bundles: Array<{task_id, ...}>                (dalla /api/imdf/bundles)
 *     - loadingBundles: boolean                         (prima fetch in corso)
 *     - selectedBundleId: string|null                   (override storico dell'utente)
 *     - setSelectedBundleId(tid)                        (setter pass-through)
 *     - bundlesManagerOpen / setBundlesManagerOpen      (modale gestione)
 *     - bundleSwitcherOpen / setBundleSwitcherOpen      (popover switcher)
 *     - liveBundleTaskId: string|null                   (bundle_task_id dal task result corrente)
 *     - bundleTaskId:     string|null                   (override || live || null)
 *     - selectedBundle:   object|null                   (bundle in lista matching bundleTaskId)
 *     - isBundleFallback: boolean                       (true se si sta mostrando uno storico al posto di un live)
 *     - imdfData:         object|null                   (lastTaskResult.imdf dallo store)
 *     - handleDeleteBundle(tid): Promise<void>          (delete lato server + stato locale)
 *
 * Side effects (useEffect):
 *   1. mount/isImdfMode/projectId -> fetch lista + auto-select piu' recente se non c'e' live
 *   2. nuovo live task -> reset override + refetch lista
 *   3. bundleTaskId cambia e store senza manifest per quell'id -> hydrate + setLastTaskResult
 *
 * Non ha cache per-id: lo store e' gia' single source of truth, cachare
 * separatamente causava inconsistenze allo switch.
 */
import { useState, useEffect } from 'react';
import useCanvasStore from '../../../store/useCanvasStore';
import { useToast } from '../../../hooks/useToast';
import { toastApiError } from '../../../utils/toastApiError';
import {
  listImdfBundles,
  getImdfBundleMetadata,
  deleteImdfBundle,
} from '../../../api/imdf';

export function useImdfBundles({ isImdfMode }) {
  const projectId = useCanvasStore(s => s.projectId);
  const lastTaskResult = useCanvasStore(s => s.lastTaskResult);
  const setLastTaskResult = useCanvasStore(s => s.setLastTaskResult);
  const toast = useToast();
  const imdfData = lastTaskResult?.imdf || null;

  const [bundles, setBundles] = useState([]);
  const [loadingBundles, setLoadingBundles] = useState(false);
  const [selectedBundleId, setSelectedBundleId] = useState(null);
  const [bundlesManagerOpen, setBundlesManagerOpen] = useState(false);
  const [bundleSwitcherOpen, setBundleSwitcherOpen] = useState(false);

  const liveBundleTaskId = imdfData?.bundle_task_id || null;
  const bundleTaskId = selectedBundleId || liveBundleTaskId || null;
  const selectedBundle = bundles.find(b => b.task_id === bundleTaskId) || null;
  const isBundleFallback = !liveBundleTaskId && !!selectedBundleId;

  useEffect(() => {
    if (!isImdfMode) return;
    let cancelled = false;
    (async () => {
      setLoadingBundles(true);
      try {
        const list = await listImdfBundles({ project_id: projectId });
        if (cancelled) return;
        const arr = Array.isArray(list) ? list : [];
        setBundles(arr);
        // Auto-select piu' recente solo se non c'e' live ne' scelta esplicita.
        // Uso getState() per leggere fuori closure ed evitare stale reads.
        const state = useCanvasStore.getState();
        const hasLive = !!state.lastTaskResult?.imdf?.bundle_task_id;
        if (!hasLive && arr.length > 0) {
          setSelectedBundleId(prev => prev || arr[0].task_id);
        }
      } catch (e) {
        console.warn('listImdfBundles:', e);
      } finally {
        if (!cancelled) setLoadingBundles(false);
      }
    })();
    return () => { cancelled = true; };
  }, [isImdfMode, projectId]);

  useEffect(() => {
    if (!isImdfMode || !liveBundleTaskId) return;
    if (liveBundleTaskId === selectedBundleId) return;
    setSelectedBundleId(null);
    let cancelled = false;
    (async () => {
      try {
        const list = await listImdfBundles({ project_id: projectId });
        if (!cancelled) setBundles(Array.isArray(list) ? list : []);
      } catch (e) {
        console.warn('listImdfBundles refresh:', e);
      }
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
    // selectedBundleId letto al solo scopo di evitare reset spurio (early return);
    // includerlo rifirerebbe l'effetto a ogni cambio selezione, mentre vogliamo
    // reagire solo all'arrivo di un nuovo liveBundleTaskId.
  }, [isImdfMode, liveBundleTaskId, projectId]);

  useEffect(() => {
    if (!isImdfMode) return;
    const bid = bundleTaskId;
    if (!bid) return;
    if (imdfData?.bundle_task_id === bid && imdfData?.manifest) return;

    let cancelled = false;
    (async () => {
      try {
        const meta = await getImdfBundleMetadata(bid);
        if (cancelled) return;
        const prev = useCanvasStore.getState().lastTaskResult || {};
        setLastTaskResult({
          ...prev,
          imdf: {
            ...(prev.imdf || {}),
            ...meta,
            bundle_task_id: bid,
          },
        });
      } catch (e) {
        console.warn('ResultNode hydrate IMDF:', e);
      }
    })();
    return () => { cancelled = true; };
  }, [isImdfMode, bundleTaskId, imdfData?.bundle_task_id, imdfData?.manifest, setLastTaskResult]);

  const handleDeleteBundle = async (tid) => {
    try {
      await deleteImdfBundle(tid);
      setBundles(prev => prev.filter(b => b.task_id !== tid));
      if (selectedBundleId === tid) setSelectedBundleId(null);
    } catch (e) {
      console.error('deleteImdfBundle:', e);
      toastApiError(toast, e, 'Errore cancellazione bundle');
    }
  };

  return {
    bundles,
    loadingBundles,
    selectedBundleId,
    setSelectedBundleId,
    bundlesManagerOpen,
    setBundlesManagerOpen,
    bundleSwitcherOpen,
    setBundleSwitcherOpen,
    liveBundleTaskId,
    bundleTaskId,
    selectedBundle,
    isBundleFallback,
    imdfData,
    handleDeleteBundle,
  };
}

export default useImdfBundles;
