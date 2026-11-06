/**
 * useProjectListData.js — i dati della dashboard `ProjectList`.
 *
 * Fase 5 T2 (R4.7, R4.9, R9.13): sopra l'hook comune hooks/useResource.
 *  - Tutte le `list*` girano in parallelo (allSettled): un servizio giu' non
 *    toglie gli altri; quelli non caricati sono un avviso con «Riprova»
 *    (`failures`), non liste vuote mute. Progetti non caricati = errore.
 *  - Lo stato delle esecuzioni non caricato NON e' "Mai eseguito":
 *    `runStatusUnavailable` e `runStatusFor(id)` -> { status: 'unavailable' }.
 *  - `loading` solo al primo caricamento (lo skeleton non torna dopo
 *    un'azione); `refreshing` per i reload.
 *  - Modifiche ottimistiche per campo (`updateProjectOptimistic`): un errore
 *    riporta il campo al valore CONFERMATO dal server solo se nel frattempo
 *    non e' partita una modifica piu' recente dello stesso campo (mai
 *    "torna A" mentre il server ha C).
 */

import { useCallback, useMemo, useRef } from 'react';
import { getLatestRunByProject } from '../../api/logRuns';
import { useResource } from '../../hooks/useResource';
import { readShared } from '../../utils/sharedResources';

const SERVICES = ['progetti', 'connessioni', 'conteggi', 'file', 'ArcGIS', 'eseguibili', 'stato delle esecuzioni'];
const UNAVAILABLE = Object.freeze({ status: 'unavailable' });
const NO_COUNTS = Object.freeze({ api_connections: 0, datasources: 0, schedules_active: 0, schedules_by_project: {} });

// UI-1 (C4, C27): le liste dalla cache condivisa (utils/sharedResources: la
// stessa risposta di gestori, editor e nodi, aggiornata quando una
// scrittura la invalida); API, data source e schedule solo come conteggi
// (GET /dashboard/counts), non tre liste intere.
const DASHBOARD_SHARED = Object.freeze(['projects', 'connections', 'dashboardCounts', 'files', 'arcgisConnections', 'eseguibili']);

async function loadDashboard({ force = false } = {}) {
  const results = await Promise.allSettled([
    ...DASHBOARD_SHARED.map((name) => readShared(name, [], { force })),
    getLatestRunByProject(),
  ]);
  // Senza progetti la dashboard non ha senso: e' un errore, non "vuota".
  if (results[0].status === 'rejected') throw results[0].reason;
  const value = (i, fallback) => (results[i].status === 'fulfilled' ? results[i].value : fallback);
  const projectsList = value(0, {}).projects || [];
  const counts = { ...NO_COUNTS, ...(value(2, null) || {}) };
  const arcgisData = value(4, { connections: [] });
  const eseguibiliData = value(5, []);
  const sourcesMap = {};
  const fileSourcesMap = {};
  projectsList.forEach(p => {
    if (p.project_type === 'manipola') {
      if (p.source_project_ids) sourcesMap[p.id] = p.source_project_ids;
      if (p.file_source_ids) fileSourcesMap[p.id] = p.file_source_ids;
    }
  });
  return {
    projects: projectsList,
    connections: value(1, { connections: [] }).connections || [],
    counts,
    files: value(3, { files: [] }).files || [],
    arcgisConnections: arcgisData.connections || arcgisData || [],
    eseguibili: Array.isArray(eseguibiliData) ? eseguibiliData : (eseguibiliData?.items || []),
    runStatusByProject: results[6].status === 'fulfilled' ? (results[6].value?.statuses || {}) : null,
    failures: results.map((r, i) => (r.status === 'rejected' ? SERVICES[i] : null)).filter(Boolean),
    projectSources: sourcesMap,
    projectFileSources: fileSourcesMap,
  };
}

const EMPTY = Object.freeze({});
const NONE = Object.freeze([]);

export function useProjectListData() {
  const resource = useResource(loadDashboard, { key: 'dashboard', shared: DASHBOARD_SHARED });
  const data = resource.data || EMPTY;
  const { setData, reload } = resource;

  // Valori confermati dal server e ultima modifica per (progetto, campo).
  const confirmedRef = useRef(new Map());
  const pendingRef = useRef(new Map());

  const patchProject = useCallback((id, patch) => {
    setData(cur => (cur ? { ...cur, projects: cur.projects.map(p => (p.id === id ? { ...p, ...patch } : p)) } : cur));
  }, [setData]);

  // #optimistic-delete (Epic C): rimozione locale immediata; il chiamante
  // ripristina con reload() se la DELETE fallisce.
  const removeProjectLocal = useCallback((id) => {
    setData(cur => (cur ? { ...cur, projects: cur.projects.filter(p => p.id !== id) } : cur));
  }, [setData]);

  const updateProjectLocal = patchProject;

  const updateProjectOptimistic = useCallback(async (project, patch, request) => {
    const token = {};
    const keys = Object.keys(patch);
    keys.forEach((k) => {
      const key = `${project.id}:${k}`;
      if (!confirmedRef.current.has(key)) confirmedRef.current.set(key, project[k]);
      pendingRef.current.set(key, token);
    });
    patchProject(project.id, patch);
    try {
      const result = await request();
      keys.forEach((k) => {
        const key = `${project.id}:${k}`;
        confirmedRef.current.set(key, patch[k]);
        if (pendingRef.current.get(key) === token) pendingRef.current.delete(key);
      });
      return result;
    } catch (err) {
      const rollback = {};
      keys.forEach((k) => {
        const key = `${project.id}:${k}`;
        if (pendingRef.current.get(key) !== token) return; // una modifica piu' recente decide
        pendingRef.current.delete(key);
        rollback[k] = confirmedRef.current.get(key);
      });
      if (Object.keys(rollback).length) patchProject(project.id, rollback);
      throw err;
    }
  }, [patchProject]);

  const reloadAll = useCallback(() => {
    confirmedRef.current.clear();
    return reload();
  }, [reload]);

  const runStatusUnavailable = resource.hasData && data.runStatusByProject === null;
  const runStatusByProject = data.runStatusByProject || EMPTY;
  const runStatusFor = useCallback(
    (id) => (runStatusUnavailable ? UNAVAILABLE : runStatusByProject[id]),
    [runStatusUnavailable, runStatusByProject],
  );

  return useMemo(() => ({
    projects: data.projects || NONE,
    connections: data.connections || NONE,
    arcgisConnections: data.arcgisConnections || NONE,
    counts: data.counts || NO_COUNTS,
    files: data.files || NONE,
    eseguibili: data.eseguibili || NONE,
    projectSources: data.projectSources || EMPTY,
    projectFileSources: data.projectFileSources || EMPTY,
    runStatusByProject,
    runStatusUnavailable,
    runStatusFor,
    failures: data.failures || NONE,
    resource,
    loading: resource.initialLoading,
    refreshing: resource.refreshing,
    reload: reloadAll,
    removeProjectLocal,
    updateProjectLocal,
    updateProjectOptimistic,
  }), [data, runStatusByProject, runStatusUnavailable, runStatusFor, resource, reloadAll, removeProjectLocal, updateProjectLocal, updateProjectOptimistic]);
}

export default useProjectListData;
