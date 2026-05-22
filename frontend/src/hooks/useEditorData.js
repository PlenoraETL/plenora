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
 *
 * Il bootstrap e' un grafo di fetch in cascata (getProject → listConnections →
 * optionally loadCatalog/loadSources). Due race da evitare:
 *
 *   1. unmount durante un await → setState su componente smontato
 *   2. projectId switch durante un await → setState del progetto vecchio
 *      sovrascrive quello del progetto nuovo (B1)
 *
 * Entrambe risolte dallo stesso pattern: un flag `cancelled` scoped al
 * singolo run dell'effect di bootstrap. La cleanup function setta
 * cancelled=true, ogni await guardato all'uscita controlla e esce prima
 * di setState. Su projectId switch, il cleanup del vecchio run e' scollegato
 * da cancelled del nuovo run (closure fresh), quindi i due run non si
 * pestano.
 *
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
 *
 *   Sempre disponibili:
 *     project       {Object|null}   — dati del progetto
 *     connections   {Array}         — connessioni DB disponibili
 *     loading       {boolean}       — true durante il caricamento iniziale
 *     error         {string|null}   — messaggio di errore se il caricamento fallisce
 *     reload        {() => void}    — forza un ricaricamento completo
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
  // Pre-fix: `[sourceTypes?.join(',')]` quando `sourceTypes` era undefined
  // produceva dep `undefined` → React passava sempre `Object.is(undefined,
  // undefined) === true`, l'effect non firerava mai sul primo array
  // valido. Fix: fallback esplicito a '' rende la dep stabile e
  // l'effect prevedibile.
  const sourceTypesKey = (sourceTypes && Array.isArray(sourceTypes))
    ? sourceTypes.join(',')
    : '';
  useEffect(() => {
    sourceTypesRef.current = sourceTypes;
    // eslint-disable-next-line react-hooks/exhaustive-deps
    // Joinata stringa stabile: una nuova string identity firera' solo
    // quando il contenuto array cambia veramente.
  }, [sourceTypesKey]);
const [project,     setProject]     = useState(null);
  const [connections, setConnections] = useState([]);
  const [loading,     setLoading]     = useState(true);
  const [error,       setError]       = useState(null);
const [availableSources, setAvailableSources] = useState([]);
  const [availableFiles,   setAvailableFiles]   = useState([]);
const [catalog,           setCatalog]           = useState({});
  const [views,             setViews]             = useState({});
  const [projectConnection, setProjectConnection] = useState(null);
  const [sourceType,        setSourceType]        = useState(null);

  // Tick per reload: bump → useEffect rigirato, cleanup cancella il run precedente.
  const [reloadTick, setReloadTick] = useState(0);
useEffect(() => {
    if (!projectId) return;
    let cancelled = false;

    // Helper: catalogo tabelle/viste (EstraiEditor). Tutti i setState guardati.
    const loadCatalog = async (projectData, allConnections) => {
      let connection         = null;
      let isArcgisConnection = false;
      // BE risponde `{ metadata: { default_connection_id, ... }, canvas,
      // output_contract }`. Il path root `projectData.default_connection_id`
      // e' undefined (pre-fix il fallback `||` lo salvava per coincidenza).
      // Accesso canonico via metadata: rimuove il ramo fragile e
      // documenta l'invariante della response shape.
      const defaultConnectionId = projectData.metadata?.default_connection_id;

      if (defaultConnectionId) {
        connection = allConnections.find(c => c.id === defaultConnectionId) || null;

        // Se non trovata nelle DB, cerca nelle ArcGIS Feature Service
        if (!connection) {
          try {
            const arcgisData = await listFeatureConnections();
            if (cancelled) return;
            const arcgisConns = arcgisData.connections || arcgisData || [];
            const arcgisConn  = arcgisConns.find(c => c.id === defaultConnectionId);
            if (arcgisConn) {
              connection         = { ...arcgisConn, type: 'arcgis' };
              isArcgisConnection = true;
            }
          } catch {
            if (cancelled) return;
          }
        }

        if (cancelled) return;
        setProjectConnection(connection || null);
        setSourceType(isArcgisConnection ? 'featureService' : 'database');
      }

      // Tabelle/viste da connessione DB
      if (connection && !isArcgisConnection) {
        try {
          const d = await getConnectionTables(connection.id);
          if (cancelled) return;
          setCatalog(d.tables || {});
          setViews(d.views || {});
        } catch {
          if (cancelled) return;
          setCatalog({});
          setViews({});
        }
      } else if (!isArcgisConnection) {
        if (cancelled) return;
        setCatalog({});
        setViews({});
      }
      // ArcGIS: feature layer lazy-load in FeatureServicePanel, nulla da fare qui.
    };

    // Helper: sorgenti (Manipola/Arricchisci). Tutti i setState guardati.
    const loadSources = async () => {
      const types = sourceTypesRef.current;
      try {
        const projectsData = await listProjects();
        if (cancelled) return;
        const all = projectsData.projects || [];
        const filtered = all.filter(p => types.includes(p.project_type || 'estrai'));
        setAvailableSources(filtered);
      } catch {
        if (cancelled) return;
        setAvailableSources([]);
      }

      try {
        const filesData = await getFiles();
        if (cancelled) return;
        setAvailableFiles(filesData.files || []);
      } catch {
        if (cancelled) return;
        setAvailableFiles([]);
      }
    };

    // Run principale
    (async () => {
      try {
        setLoading(true);
        setError(null);

        // 1. Progetto (sempre)
        const projectData = await getProject(projectId);
        if (cancelled) return;
        setProject(projectData);

        // 2. Connessioni DB (sempre)
        let allConnections = [];
        try {
          const d = await listConnections();
          if (cancelled) return;
          allConnections = d.connections || [];
          setConnections(allConnections);
        } catch {
          if (cancelled) return;
          setConnections([]);
        }

        // 3. Catalogo tabelle (solo EstraiEditor)
        if (withCatalog) {
          await loadCatalog(projectData, allConnections);
          if (cancelled) return;
        }

        // 4. Sorgenti (Manipola + Arricchisci)
        if (withSources) {
          await loadSources();
          if (cancelled) return;
        }
      } catch (err) {
        if (cancelled) return;
        console.error('[useEditorData] Errore caricamento:', err);
        setError(err.message || 'Errore caricamento progetto');
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [projectId, withCatalog, withSources, reloadTick]);

  // reload: API esterna stabile () => void, internamente bump tick → effect ri-gira.
  const reload = useCallback(() => {
    setReloadTick(t => t + 1);
  }, []);

  return {
    // Sempre disponibili
    project,
    connections,
    loading,
    error,
    reload,

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
