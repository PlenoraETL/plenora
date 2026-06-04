/**
 * useProjectListData.js — Hook che incapsula le 8 fetch parallele
 * della dashboard `ProjectList`.
 *
 * Tutte le `list*` API girano in Promise.all per minimizzare il
 * round-trip totale. Le fetch non-critiche (files, schedules, arcgis,
 * eseguibili, datasources) hanno fallback inline `.catch(...)` per
 * non bloccare il render se una singola integrazione e' giu`.
 *
 * Ritorna:
 *  - lo state delle 8 collezioni + 2 indici (projectSources,
 *    projectFileSources per i progetti manipola)
 *  - `loading` flag (true solo durante la fetch iniziale o reload)
 *  - `reload()` per re-fetch (passato come `onCreated` ai modal di
 *    creazione, e come callback dei delete handlers)
 */

import { useState, useEffect, useCallback, useRef } from 'react';
import {
  listProjects,
  listConnections,
  listApiConnections,
  listEseguibili,
} from '../../api';
import { listFeatureConnections } from '../../api/features';
import { getFiles } from '../../api/files';
import { listSchedules } from '../../api/schedules';
import { listDataSources } from '../../api/datasources';
import { getLatestRunByProject } from '../../api/logRuns';
import { useToast } from '../../hooks/useToast';
import { useMountedRef } from '../../hooks/useMountedRef';

export function useProjectListData() {
  const toast = useToast();
  // P1 (2026-05-26): toast tramite ref invece che in deps di `reload`.
  // Pre-fix: `toast` nelle deps di useCallback. Se il toast provider
  // re-rendera (cambio variant, theme, ecc.) cambia l'identita' della
  // reference; reload e' ricreato; useEffect([reload]) lo ri-triggera
  // → potenziale loop di re-fetch su re-render del provider.
  const toastRef = useRef(toast);
  toastRef.current = toast;
  const [projects, setProjects] = useState([]);
  const [connections, setConnections] = useState([]);
  const [arcgisConnections, setArcgisConnections] = useState([]);
  const [apiConnections, setApiConnections] = useState([]);
  const [files, setFiles] = useState([]);
  const [schedules, setSchedules] = useState([]);
  const [dataSources, setDataSources] = useState([]);
  const [eseguibili, setEseguibili] = useState([]);
  const [projectSources, setProjectSources] = useState({});
  const [projectFileSources, setProjectFileSources] = useState({});
  // #11: stato dell'ultima run per progetto { project_id: {status, ts_start} }.
  const [runStatusByProject, setRunStatusByProject] = useState({});
  const [loading, setLoading] = useState(true);

  // Guard contro reload concorrenti + setState post-unmount.
  // `reloadIdRef`: la callback memorizza il proprio id; alla resolve si
  // confronta con il ref. Se non combacia → reload piu' recente ha
  // sovrascritto la nostra invocazione, scartiamo i risultati per
  // evitare race "vecchia risposta arriva dopo nuova".
  const mountedRef = useMountedRef();
  const reloadIdRef = useRef(0);

  const reload = useCallback(async () => {
    const myId = reloadIdRef.current + 1;
    reloadIdRef.current = myId;
    try {
      setLoading(true);
      // Pre-fix (P1 contracts-flows): `Promise.all` + 3 fetch senza
      // `.catch()` (listProjects/listConnections/listApiConnections) →
      // se UNO dei 3 falliva, l'intero Promise.all rejected, scartando
      // ANCHE i risultati degli altri 5 che avevano gia' completato.
      // Tutto-o-niente fragile su failure transient di un singolo servizio.
      // Post-fix: `Promise.allSettled` + handling per-servizio con
      // fallback canonico. I dati available restano, partial-failure
      // visibili via toast.warning aggregato.
      const results = await Promise.allSettled([
        listProjects(),
        listConnections(),
        listApiConnections(),
        getFiles(),
        listSchedules(),
        listFeatureConnections(),
        listEseguibili(),
        listDataSources(),
        getLatestRunByProject(),
      ]);

      // Cancel guard: se nel frattempo un altro reload e' stato avviato
      // (myId != reloadIdRef.current) OPPURE il componente e' smontato,
      // i risultati di questa invocazione sono stale → scarta.
      if (!mountedRef.current || reloadIdRef.current !== myId) return;

      const [
        projectsRes,
        connectionsRes,
        apiConnRes,
        filesRes,
        schedulesRes,
        arcgisRes,
        eseguibiliRes,
        dataSourcesRes,
        runStatusRes,
      ] = results;

      const projectsData = projectsRes.status === 'fulfilled' ? projectsRes.value : { projects: [] };
      const connectionsData = connectionsRes.status === 'fulfilled' ? connectionsRes.value : { connections: [] };
      const apiConnData = apiConnRes.status === 'fulfilled' ? apiConnRes.value : { api_connections: [] };
      const filesData = filesRes.status === 'fulfilled' ? filesRes.value : { files: [] };
      const schedulesData = schedulesRes.status === 'fulfilled' ? schedulesRes.value : { schedules: [], total: 0 };
      const arcgisData = arcgisRes.status === 'fulfilled' ? arcgisRes.value : { connections: [] };
      const eseguibiliData = eseguibiliRes.status === 'fulfilled' ? eseguibiliRes.value : [];
      const dataSourcesData = dataSourcesRes.status === 'fulfilled' ? dataSourcesRes.value : [];
      const runStatusData = runStatusRes.status === 'fulfilled' ? runStatusRes.value : { statuses: {} };

      // Segnaliamo all'utente le fetch fallite (toast.warning aggregato).
      // `stato run` è omesso di proposito: è un arricchimento non-critico per i
      // filtri della dashboard, un suo fallimento non merita un warning.
      const failedServices = results
        .slice(0, 8)
        .map((r, i) => r.status === 'rejected'
          ? ['progetti', 'connessioni', 'API', 'file', 'schedule', 'ArcGIS', 'eseguibili', 'datasources'][i]
          : null)
        .filter(Boolean);
      if (failedServices.length > 0) {
        toastRef.current.warning(`Alcuni dati non caricati: ${failedServices.join(', ')}`);
      }

      const projectsList = projectsData.projects || [];
      setProjects(projectsList);
      setConnections(connectionsData.connections || []);
      setArcgisConnections(arcgisData.connections || arcgisData || []);
      setApiConnections(apiConnData.api_connections || []);
      setFiles(filesData.files || []);
      setSchedules(schedulesData.schedules || []);
      setEseguibili(Array.isArray(eseguibiliData) ? eseguibiliData : (eseguibiliData?.items || []));
      setDataSources(dataSourcesData?.datasources || []);
      setRunStatusByProject(runStatusData?.statuses || {});

      const sourcesMap = {};
      const fileSourcesMap = {};
      projectsList.forEach(p => {
        if (p.project_type === 'manipola') {
          if (p.source_project_ids)  sourcesMap[p.id]     = p.source_project_ids;
          if (p.file_source_ids)     fileSourcesMap[p.id] = p.file_source_ids;
        }
      });
      setProjectSources(sourcesMap);
      setProjectFileSources(fileSourcesMap);
    } catch (err) {
      console.error('Errore caricamento dati:', err);
      if (mountedRef.current && reloadIdRef.current === myId) {
        toastRef.current.error('Errore caricamento dati');
      }
    } finally {
      if (mountedRef.current && reloadIdRef.current === myId) {
        setLoading(false);
      }
    }
  }, [mountedRef]);

  useEffect(() => { reload(); }, [reload]);

  return {
    projects,
    connections,
    arcgisConnections,
    apiConnections,
    files,
    schedules,
    dataSources,
    eseguibili,
    projectSources,
    projectFileSources,
    runStatusByProject,
    loading,
    reload,
  };
}

export default useProjectListData;
