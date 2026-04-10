/**
 * useEditorData.js — Hook per il caricamento dati iniziale degli editor
 *
 * Centralizza la logica di bootstrap comune a EstraiEditor, ManipolaEditor
 * e ArricchisciEditor: caricamento progetto, connessioni, sorgenti e catalogo.
 *
 * Prima di questo hook ogni editor ridefiniva ~80 righe identiche:
 *   const [project, setProject]         = useState(null);
 *   const [loading, setLoading]         = useState(true);
 *   const [connections, setConnections] = useState([]);
 *   const loadProjectAndSources = async () => { ... };
 *   useEffect(() => { loadProjectAndSources(); }, [projectId]);
 *
 * Ora basta:
 *   const data = useEditorData(projectId, { withSources: true });
 *
 * ─── Opzioni disponibili ──────────────────────────────────────────────────────
 *
 *   withSources  (bool, default false)
 *     Carica listProjects() + getFiles() per popolare la sidebar sorgenti.
 *     Usato da: ManipolaEditor, ArricchisciEditor.
 *     sourceTypes: array di project_type da includere (default tutti)
 *
 *   withCatalog  (bool, default false)
 *     Carica il catalogo tabelle/viste della connessione di default del progetto.
 *     Se la connessione è ArcGIS carica i feature layer invece delle tabelle.
 *     Usato da: EstraiEditor.
 *
 * ─── Valori restituiti ────────────────────────────────────────────────────────
 *
 *   Sempre disponibili:
 *     project       {Object|null}   — dati del progetto
 *     connections   {Array}         — connessioni DB disponibili
 *     loading       {boolean}       — true durante il caricamento iniziale
 *     error         {string|null}   — messaggio di errore se il caricamento fallisce
 *     reload        {Function}      — forza un ricaricamento completo
 *
 *   Con withSources=true:
 *     availableSources  {Array}  — lista progetti sorgente filtrati per tipo
 *     availableFiles    {Array}  — lista file caricati
 *
 *   Con withCatalog=true:
 *     catalog           {Object}      — { tableName: [columns] }
 *     views             {Object}      — { viewName: [columns] }
 *     projectConnection {Object|null} — connessione DB/ArcGIS del progetto
 *     sourceType        {string|null} — 'database' | 'featureService'
 *
 * ─── Uso ─────────────────────────────────────────────────────────────────────
 *
 * // EstraiEditor
 * const {
 *   project, connections, loading, error, reload,
 *   catalog, views, projectConnection, sourceType
 * } = useEditorData(projectId, { withCatalog: true });
 *
 * // ManipolaEditor
 * const {
 *   project, connections, loading, error, reload,
 *   availableSources, availableFiles
 * } = useEditorData(projectId, {
 *   withSources: true,
 *   sourceTypes: ['estrai', 'manipola', 'arricchisci']
 * });
 *
 * // ArricchisciEditor
 * const {
 *   project, connections, loading, error, reload,
 *   availableSources, availableFiles
 * } = useEditorData(projectId, {
 *   withSources: true,
 *   sourceTypes: ['estrai', 'manipola']
 * });
 */

import { useState, useEffect, useCallback, useRef } from 'react';
import { getProject, listProjects }    from '../api/projects';
import { listConnections, getConnectionTables } from '../api/connections';
import { listFeatureConnections }       from '../api/features';
import { getFiles }                     from '../api/files';

/**
 * Hook per il caricamento dati iniziale degli editor URBY-ETL.
 *
 * @param {string}  projectId
 * @param {Object}  options
 * @param {boolean} options.withSources   - Carica sorgenti (progetti + file)
 * @param {boolean} options.withCatalog   - Carica catalogo tabelle/viste (solo Estrai)
 * @param {Array}   options.sourceTypes   - Tipi di progetto da includere come sorgenti
 *                                         (default: ['estrai', 'manipola', 'arricchisci'])
 * @returns {Object} Dati caricati + stato loading/error + reload
 */
export function useEditorData(projectId, options = {}) {
  const {
    withSources  = false,
    withCatalog  = false,
    sourceTypes  = ['estrai', 'manipola', 'arricchisci'],
  } = options;

  // sourceTypes stabilizzato con ref: evita che un array literal nelle deps
  // di useCallback causi un nuovo riferimento ad ogni render → loop infinito.
  const sourceTypesRef = useRef(sourceTypes);
  useEffect(() => {
    sourceTypesRef.current = sourceTypes;
  }, [sourceTypes?.join(',')]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Ref di montaggio — evita setState su componente smontato ─────────────
  const mountedRef = useRef(true);
  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
  }, []);

  // ── Stato comune ─────────────────────────────────────────────────────────
  const [project,     setProject]     = useState(null);
  const [connections, setConnections] = useState([]);
  const [loading,     setLoading]     = useState(true);
  const [error,       setError]       = useState(null);

  // ── Stato sorgenti (withSources) ──────────────────────────────────────────
  const [availableSources, setAvailableSources] = useState([]);
  const [availableFiles,   setAvailableFiles]   = useState([]);

  // ── Stato catalogo (withCatalog) ──────────────────────────────────────────
  const [catalog,           setCatalog]           = useState({});
  const [views,             setViews]             = useState({});
  const [projectConnection, setProjectConnection] = useState(null);
  const [sourceType,        setSourceType]        = useState(null);

  // ── Funzione di caricamento ───────────────────────────────────────────────

  const load = useCallback(async () => {
    if (!projectId) return;
    try {
      setLoading(true);
      setError(null);

      // 1. Progetto (sempre)
      const projectData = await getProject(projectId);
      if (!mountedRef.current) return;
      setProject(projectData);

      // 2. Connessioni DB (sempre)
      let allConnections = [];
      try {
        const d = await listConnections();
        if (!mountedRef.current) return;
        allConnections = d.connections || [];
        setConnections(allConnections);
      } catch (err) {
        setConnections([]);
      }

      // 3. Catalogo tabelle (solo EstraiEditor)
      if (withCatalog) {
        await loadCatalog(projectData, allConnections);
      }

      // 4. Sorgenti (Manipola + Arricchisci)
      if (withSources) {
        await loadSources();
      }

    } catch (err) {
      if (!mountedRef.current) return;
      console.error('[useEditorData] Errore caricamento:', err);
      setError(err.message || 'Errore caricamento progetto');
    } finally {
      if (mountedRef.current) setLoading(false);
    }
  }, [projectId, withCatalog, withSources]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Caricamento catalogo (EstraiEditor) ──────────────────────────────────

  /**
   * Determina il tipo di sorgente (DB o ArcGIS) e carica le tabelle/viste
   * o i feature layer a seconda del tipo di connessione del progetto.
   */
  const loadCatalog = async (projectData, allConnections) => {
    let connection         = null;
    let isArcgisConnection = false;

    // default_connection_id è top-level nella response (non dentro metadata)
    const defaultConnectionId = projectData.default_connection_id || projectData.metadata?.default_connection_id;

    if (defaultConnectionId) {
      // Cerca prima nelle connessioni DB
      connection = allConnections.find(c => c.id === defaultConnectionId) || null;

      // Se non trovata, cerca nelle connessioni ArcGIS Feature Service
      if (!connection) {
        try {
          const arcgisData = await listFeatureConnections();
          const arcgisConns = arcgisData.connections || arcgisData || [];
          const arcgisConn  = arcgisConns.find(c => c.id === defaultConnectionId);
          if (arcgisConn) {
            connection         = { ...arcgisConn, type: 'arcgis' };
            isArcgisConnection = true;
          }
        } catch (err) {
        }
      }

      setProjectConnection(connection || null);
      setSourceType(isArcgisConnection ? 'featureService' : 'database');
    }

    // Carica tabelle dalla connessione specifica se DB
    if (connection && !isArcgisConnection) {
      try {
        const d = await getConnectionTables(connection.id);
        setCatalog(d.tables || {});
        setViews(d.views || {});
      } catch {
        setCatalog({});
        setViews({});
      }
    } else if (!isArcgisConnection) {
      // Nessuna connessione di default
      setCatalog({});
      setViews({});
    }
    // Se ArcGIS: le tabelle (feature layer) vengono caricate da FeatureServicePanel
    // in modo lazy — non serve caricarle qui
  };

  // ── Caricamento sorgenti (ManipolaEditor + ArricchisciEditor) ─────────────

  /**
   * Carica i progetti usabili come sorgente e i file disponibili.
   * I progetti vengono filtrati per tipo in base a sourceTypes.
   */
  const loadSources = async () => {
    const sourceTypes = sourceTypesRef.current;
    // Progetti sorgente
    try {
      const projectsData = await listProjects();
      const all = projectsData.projects || [];
      const filtered = all.filter(p =>
        sourceTypes.includes(p.project_type || 'estrai')
      );
      setAvailableSources(filtered);
    } catch (err) {
      setAvailableSources([]);
    }

    // File caricati
    try {
      const filesData = await getFiles();
      setAvailableFiles(filesData.files || []);
    } catch (err) {
      setAvailableFiles([]);
    }
  };

  // ── Effect di bootstrap ───────────────────────────────────────────────────

  useEffect(() => {
    load();
  }, [load]);

  // ── Return ────────────────────────────────────────────────────────────────

  return {
    // Sempre disponibili
    project,
    connections,
    loading,
    error,
    reload: load,

    // Con withSources=true
    availableSources,
    availableFiles,

    // Con withCatalog=true
    catalog,
    views,
    projectConnection,
    sourceType,
  };
}
