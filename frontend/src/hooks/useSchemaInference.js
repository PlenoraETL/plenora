/**
 * useSchemaInference.js — Hook per inferire lo schema colonne dai nodi
 *
 * Interroga il backend per ottenere lo schema (colonne + tipi) di ogni
 * nodo sorgente nel canvas. Usato per popolare i select di colonne
 * nelle configurazioni delle trasformazioni.
 */
import { useState, useCallback, useEffect, useRef } from 'react';
import { getProjectSchemas, getNodeSchema, validateProjectConnection, validateAllProjectSources } from '../api';
import { useMountedRef } from './useMountedRef';

/**
 * Hook per gestire l'inferenza degli schemi delle colonne attraverso il grafo dei nodi.
 * 
 * Uso:
 * ```jsx
 * const { schemas, loading, error, refreshSchemas, getNodeSchema } = useSchemaInference(projectId);
 * 
 * // Ottieni schema di un nodo specifico
 * const columns = getNodeSchema('transform_1');
 * 
 * // Refresh manuale dopo modifiche al canvas
 * await refreshSchemas();
 * ```
 */
export function useSchemaInference(projectId) {
  const [schemas, setSchemas] = useState({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  // Ref per evitare chiamate duplicate
  const fetchingRef = useRef(false);
  const lastProjectIdRef = useRef(null);
  // Mounted guard: setState dopo `await` deve verificare il flag per
  // evitare leak su unmount (es. cambio progetto durante schema fetch).
  const mountedRef = useMountedRef();

  /**
   * Carica tutti gli schemi per il progetto
   */
  const refreshSchemas = useCallback(async () => {
    if (!projectId || fetchingRef.current) return;

    fetchingRef.current = true;
    setLoading(true);
    setError(null);

    try {
      const data = await getProjectSchemas(projectId);
      if (!mountedRef.current) return;
      setSchemas(data.schemas || {});
      lastProjectIdRef.current = projectId;

    } catch (err) {
      console.error('[useSchemaInference] Errore caricamento schemi:', err);
      if (!mountedRef.current) return;
      setError(err.message);
    } finally {
      if (mountedRef.current) {
        setLoading(false);
      }
      fetchingRef.current = false;
    }
  }, [projectId, mountedRef]);

  /**
   * Ottiene lo schema di un singolo nodo dalla cache locale.
   * Se non presente, restituisce array vuoto.
   */
  const getNodeSchema = useCallback((nodeId) => {
    return schemas[nodeId] || [];
  }, [schemas]);

  /**
   * Ottiene lo schema di un nodo dal server (bypass cache).
   * Utile per aggiornamenti incrementali.
   */
  const fetchNodeSchema = useCallback(async (nodeId) => {
    if (!projectId || !nodeId) return [];

    try {
      const data = await getNodeSchema(projectId, nodeId);
      if (!mountedRef.current) return data.columns || [];

      // Aggiorna cache locale solo se il componente e' ancora montato
      setSchemas(prev => ({
        ...prev,
        [nodeId]: data.columns || []
      }));

      return data.columns || [];

    } catch (err) {
      console.error(`[useSchemaInference] Errore caricamento schema nodo ${nodeId}:`, err);
      return [];
    }
  }, [projectId, mountedRef]);

  /**
   * Valida una connessione tra due nodi
   */
  const validateConnection = useCallback(async (sourceNodeId, targetNodeId, config = null) => {
    if (!projectId) return { valid: false, error: 'No project ID' };
    
    try {
      return await validateProjectConnection(projectId, {
            source_node_id: sourceNodeId,
            target_node_id: targetNodeId,
            config
          });
      
    } catch (err) {
      console.error('[useSchemaInference] Errore validazione connessione:', err);
      return { valid: false, error: err.message };
    }
  }, [projectId]);

  /**
   * Valida tutte le connessioni del progetto
   */
  const validateAllConnections = useCallback(async () => {
    if (!projectId) return { valid: true, errors: [] };
    
    try {
      return await validateAllProjectSources(projectId);
      
    } catch (err) {
      console.error('[useSchemaInference] Errore validazione connessioni:', err);
      return { valid: false, errors: [{ message: err.message }] };
    }
  }, [projectId]);

  // Carica schemi automaticamente quando cambia projectId.
  // Reset di `lastProjectIdRef` nel cleanup: una sequenza
  // A → B → A con stesso projectId, dopo che il primo mount era
  // arrivato a `lastProjectIdRef.current = "A"`, avrebbe skippato la
  // seconda fetch lasciando schemi stale. Resettando al cleanup il
  // ref non sopravvive ai remount → fetch sempre fresca.
  useEffect(() => {
    if (projectId && projectId !== lastProjectIdRef.current) {
      refreshSchemas();
    }
    return () => {
      lastProjectIdRef.current = null;
    };
  }, [projectId, refreshSchemas]);

  return {
    schemas,
    loading,
    error,
    refreshSchemas,
    getNodeSchema,
    fetchNodeSchema,
    validateConnection,
    validateAllConnections
  };
}

export default useSchemaInference;
