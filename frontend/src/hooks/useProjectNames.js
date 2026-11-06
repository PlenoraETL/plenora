/**
 * useProjectNames — id del progetto → nome, per le pagine che ricevono solo
 * l'id (Log Viewer, esecuzioni). Fase 5 T7 (S5, G5: nomi al posto degli id).
 * Dato secondario: un errore lascia gli id (nessun avviso).
 *
 * UI-1 (C9): la lista dei progetti e' la risorsa condivisa `projects`
 * (utils/sharedResources): la stessa risposta della dashboard e degli
 * editor, non una lista intera scaricata di nuovo per i nomi.
 */
import { useMemo } from 'react';
import { useSharedResource } from './useSharedResource';

export function useProjectNames(enabled = true) {
  const resource = useSharedResource('projects', { enabled });
  const list = resource.data;
  return useMemo(() => {
    const items = Array.isArray(list) ? list : (list?.projects || []);
    return new Map(items.map(p => [String(p.id), p.name || String(p.id)]));
  }, [list]);
}
