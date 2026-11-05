/**
 * useEditorData.js — dati iniziali degli editor (Estrai, Manipola, Arricchisci).
 *
 * Fase 5 T2 (R4.8, R4.18, E2): sopra l'hook comune hooks/useResource.
 *
 *  - Avvio (bloccante, `loading`): progetto e connessioni IN PARALLELO; la
 *    connessione del progetto si risolve qui (anche ArcGIS) perche' il canvas
 *    di Estrai si costruisce con essa. Un errore del progetto e' `error`.
 *  - Catalogo tabelle/viste (Estrai, `withCatalog`): NON blocca l'apertura;
 *    ha i suoi `catalogLoading`/`catalogError`/`reloadCatalog`: un errore e'
 *    un errore con «Riprova», mai "Nessuna tabella trovata".
 *  - Sorgenti (Manipola/Arricchisci, `withSources`): progetti e file in
 *    parallelo, insieme all'avvio; `sourcesError` per quelli non caricati.
 *  - Connessioni non caricate: `connectionsError` (non una lista vuota muta).
 *  - Ogni risposta e' del progetto che l'ha chiesta (id di richiesta): un
 *    cambio di progetto nella stessa route non riceve dati del precedente.
 *
 * Valori: project, connections, loading, error, reload, availableSources,
 * availableFiles, sourcesError, catalog, views, catalogLoading, catalogError,
 * reloadCatalog, projectConnection, sourceType, connectionsError.
 */

import { useCallback, useEffect, useMemo, useRef } from 'react';
import { useToast } from './useToast';
import { useResource } from './useResource';
import { getProject, listProjects }    from '../api/projects';
import { listConnections, getConnectionTables } from '../api/connections';
import { listFeatureConnections }       from '../api/features';
import { getFiles }                     from '../api/files';
import { humanizeError, requestErrorMessage } from '../utils/errorMessages';

const EMPTY_OBJ = Object.freeze({});
const EMPTY_LIST = Object.freeze([]);

async function loadBoot(projectId, withCatalog) {
  const [projectData, connectionsRes] = await Promise.all([
    getProject(projectId),
    listConnections().then(d => ({ ok: d.connections || [] }), err => ({ err })),
  ]);
  const allConnections = connectionsRes.ok || [];
  let projectConnection = null;
  let sourceType = null;
  if (withCatalog) {
    // BE: `{ metadata: { default_connection_id, ... }, canvas, output_contract }`.
    const defaultConnectionId = projectData.metadata?.default_connection_id;
    let isArcgis = false;
    if (defaultConnectionId) {
      projectConnection = allConnections.find(c => c.id === defaultConnectionId) || null;
      // Se non trovata nelle DB, cerca nelle ArcGIS Feature Service.
      if (!projectConnection) {
        try {
          const arcgisData = await listFeatureConnections();
          const arcgisConn = (arcgisData.connections || arcgisData || []).find(c => c.id === defaultConnectionId);
          if (arcgisConn) { projectConnection = { ...arcgisConn, type: 'arcgis' }; isArcgis = true; }
        } catch (_) { /* connessione non risolta: il catalogo resta vuoto */ }
      }
    }
    sourceType = isArcgis ? 'featureService' : 'database';
  }
  return {
    project: projectData,
    connections: allConnections,
    connectionsError: connectionsRes.err ? requestErrorMessage(connectionsRes.err) : null,
    projectConnection,
    sourceType,
  };
}

export function useEditorData(projectId, options = {}) {
  const {
    withSources  = false,
    withCatalog  = false,
    sourceTypes  = ['estrai', 'manipola', 'arricchisci'],
  } = options;
  const toast = useToast();
  const sourceTypesKey = Array.isArray(sourceTypes) ? sourceTypes.join(',') : '';

  const boot = useResource(() => loadBoot(projectId, withCatalog), {
    key: ['editor-boot', projectId || null, withCatalog],
    enabled: Boolean(projectId),
    errorMessage: (err) => humanizeError(err).message,
  });
  const bootData = boot.data;
  const projectConnection = bootData?.projectConnection || null;

  // #2 (trasparenza): rename di colonna auto-applicati ai project_source
  // (rinominati a monte) detti all'utente, una volta per progetto caricato.
  const notifiedRef = useRef(null);
  useEffect(() => {
    const migs = bootData?.project?.applied_migrations || [];
    if (!bootData || notifiedRef.current === bootData.project || migs.length === 0) return;
    notifiedRef.current = bootData.project;
    const renames = migs.flatMap((m) => m.renames || []);
    const preview = renames.slice(0, 3).map((r) => `${r.from}→${r.to}`).join(', ');
    const more = renames.length > 3 ? ` +${renames.length - 3}` : '';
    toast.info(`Aggiornati ${renames.length} riferimenti a colonne rinominate a monte: ${preview}${more}`, 10000);
  }, [bootData, toast]);

  // Connessioni non caricate: si dice (i selettori dei nodi resterebbero
  // vuoti senza motivo), una volta per caricamento.
  const connectionsError = bootData?.connectionsError || null;
  useEffect(() => {
    if (connectionsError) toast.warning(`Connessioni non caricate: ${connectionsError}`);
  }, [connectionsError, bootData, toast]);

  // Ricarica non riuscita con il progetto gia' aperto: l'errore si dice
  // (regola di hooks/useResource: con status 'error' l'errore e' sempre
  // visibile); senza progetto e' la pagina d'errore dell'editor (`error`).
  const bootStaleError = boot.stale ? boot.error : null;
  useEffect(() => {
    if (bootStaleError) toast.warning(`Progetto non ricaricato, resta la versione aperta: ${bootStaleError}`);
  }, [bootStaleError, toast]);

  const dbConnection = withCatalog && projectConnection && bootData?.sourceType === 'database' ? projectConnection : null;
  const catalogResource = useResource(async () => {
    const d = await getConnectionTables(dbConnection.id);
    return { tables: d.tables || {}, views: d.views || {} };
  }, {
    key: ['editor-catalog', projectId || null, dbConnection?.id || null],
    enabled: Boolean(dbConnection),
  });

  const sourcesResource = useResource(async () => {
    const types = sourceTypesKey ? sourceTypesKey.split(',') : [];
    const [projectsRes, filesRes] = await Promise.allSettled([listProjects(), getFiles()]);
    if (projectsRes.status === 'rejected' && filesRes.status === 'rejected') throw projectsRes.reason;
    const all = projectsRes.status === 'fulfilled' ? (projectsRes.value.projects || []) : [];
    return {
      // Il progetto aperto non e' mai una sorgente di se stesso (Fase 5, T6,
      // R3.19: trascinarlo creava un ciclo).
      sources: all.filter(p => types.includes(p.project_type || 'estrai') && String(p.id) !== String(projectId)),
      files: filesRes.status === 'fulfilled' ? (filesRes.value.files || []) : [],
      errors: [
        projectsRes.status === 'rejected' ? `Progetti non caricati: ${requestErrorMessage(projectsRes.reason)}` : null,
        filesRes.status === 'rejected' ? `File non caricati: ${requestErrorMessage(filesRes.reason)}` : null,
      ].filter(Boolean),
    };
  }, {
    key: ['editor-sources', projectId || null, sourceTypesKey],
    enabled: Boolean(projectId && withSources),
  });

  const { reload: reloadBoot } = boot;
  const { reload: reloadSources } = sourcesResource;
  const { reload: reloadCatalog } = catalogResource;
  const reload = useCallback(() => {
    reloadBoot();
    if (withSources) reloadSources();
    if (dbConnection) reloadCatalog();
  }, [reloadBoot, reloadSources, reloadCatalog, withSources, dbConnection]);

  const sourcesError = sourcesResource.status === 'error'
    ? sourcesResource.error
    : (sourcesResource.data?.errors || []).join(' · ') || null;

  return useMemo(() => ({
    // Sempre disponibili
    project: bootData?.project || null,
    connections: bootData?.connections || EMPTY_LIST,
    connectionsError: bootData?.connectionsError || null,
    loading: Boolean(projectId) && boot.initialLoading,
    error: boot.status === 'error' && !boot.hasData ? boot.error : null,
    reload,

    // Con withSources=true
    availableSources: sourcesResource.data?.sources || EMPTY_LIST,
    availableFiles: sourcesResource.data?.files || EMPTY_LIST,
    sourcesError,
    sourcesLoading: Boolean(withSources) && sourcesResource.initialLoading,
    reloadSources,

    // Con withCatalog=true
    catalog: catalogResource.data?.tables || EMPTY_OBJ,
    views: catalogResource.data?.views || EMPTY_OBJ,
    catalogLoading: catalogResource.initialLoading,
    catalogError: catalogResource.status === 'error' ? catalogResource.error : null,
    reloadCatalog,
    projectConnection,
    sourceType: bootData?.sourceType || null,
  }), [bootData, projectId, boot.initialLoading, boot.status, boot.hasData, boot.error, reload,
    sourcesResource.data, sourcesResource.initialLoading, withSources, sourcesError, reloadSources, catalogResource.data, catalogResource.initialLoading,
    catalogResource.status, catalogResource.error, reloadCatalog, projectConnection]);
}

export default useEditorData;
