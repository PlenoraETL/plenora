/**
 * useInlinePreview.js — Anteprima compatta NON-modale (#5).
 *
 * Variante leggera di `usePreview` sopra lo stesso ciclo unico
 * (hooks/useTask, Fase 5 T2): l'anteprima del passo B chiesta mentre quella
 * del passo A e' in corso vince sempre (id di richiesta, audit R1-N2);
 * annullata, permessi revocati e task perso hanno un esito (R4.12).
 *
 * Ritorna: { status, data, error, run, clear }
 *   status: 'idle' | 'loading' | 'success' | 'empty' | 'error'
 *   data:   { columns:[{name,type}], rows:[{col:val}], total_count, preview_count, truncated }
 */
import { useCallback } from 'react';
import { previewProject } from '../api';
import { useTask } from './useTask';
import { previewErrorOf } from './usePreview';

const POLL_INTERVAL = 1000;

export function useInlinePreview(projectId) {
  const task = useTask({ scope: projectId, interval: POLL_INTERVAL, outcome: { failureFallback: "Errore durante l'anteprima" } });
  const { state, run: runTask, reset } = task;

  const run = useCallback(async (limit = 10, nodeId = null) => {
    if (!projectId) return;
    await runTask(() => previewProject(projectId, limit, nodeId));
  }, [projectId, runTask]);

  let status = 'idle';
  if (task.isActive) status = 'loading';
  else if (state.phase === 'success') status = (state.result?.rows || []).length === 0 ? 'empty' : 'success';
  else if (state.phase !== 'idle') status = 'error';

  return {
    status,
    data: state.phase === 'success' ? (state.result || {}) : null,
    error: status === 'error' ? previewErrorOf(state) : null,
    run,
    clear: reset,
  };
}

export default useInlinePreview;
