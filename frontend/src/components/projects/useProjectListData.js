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

import { useState, useEffect, useCallback } from 'react';
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
import { useToast } from '../../hooks/useToast';

export function useProjectListData() {
  const toast = useToast();
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
  const [loading, setLoading] = useState(true);

  const reload = useCallback(async () => {
    try {
      setLoading(true);
      const [
        projectsData,
        connectionsData,
        apiConnData,
        filesData,
        schedulesData,
        arcgisData,
        eseguibiliData,
        dataSourcesData,
      ] = await Promise.all([
        listProjects(),
        listConnections(),
        listApiConnections(),
        getFiles().catch(() => ({ files: [] })),
        listSchedules().catch(() => ({ schedules: [], total: 0 })),
        listFeatureConnections().catch(() => ({ connections: [] })),
        listEseguibili().catch(() => []),
        listDataSources().catch(() => []),
      ]);

      const projectsList = projectsData.projects || [];
      setProjects(projectsList);
      setConnections(connectionsData.connections || []);
      setArcgisConnections(arcgisData.connections || arcgisData || []);
      setApiConnections(apiConnData.api_connections || []);
      setFiles(filesData.files || []);
      setSchedules(schedulesData.schedules || []);
      setEseguibili(Array.isArray(eseguibiliData) ? eseguibiliData : (eseguibiliData?.items || []));
      setDataSources(dataSourcesData?.datasources || []);

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
      toast.error('Errore caricamento dati');
    } finally {
      setLoading(false);
    }
  }, [toast]);

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
    loading,
    reload,
  };
}

export default useProjectListData;
