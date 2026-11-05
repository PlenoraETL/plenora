/**
 * useProjectNames — id del progetto → nome, per le pagine che ricevono solo
 * l'id (Log Viewer, esecuzioni). Fase 5 T7 (S5, G5: nomi al posto degli id).
 * Dato secondario: un errore lascia gli id (nessun avviso).
 */
import { useMemo } from 'react';
import { listProjects } from '../api/projects';
import { useResource } from './useResource';

export function useProjectNames(enabled = true) {
  const resource = useResource(listProjects, { key: 'project-names', enabled });
  const list = resource.data;
  return useMemo(() => {
    const items = Array.isArray(list) ? list : (list?.projects || []);
    return new Map(items.map(p => [String(p.id), p.name || String(p.id)]));
  }, [list]);
}
