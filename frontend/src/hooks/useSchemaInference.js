import { useState, useCallback, useEffect, useRef } from 'react';

// API_BASE_URL - adatta in base alla tua configurazione
const API_BASE_URL = 'http://localhost:8000';

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

  /**
   * Carica tutti gli schemi per il progetto
   */
  const refreshSchemas = useCallback(async () => {
    if (!projectId || fetchingRef.current) return;
    
    fetchingRef.current = true;
    setLoading(true);
    setError(null);
    
    try {
      const response = await fetch(`${API_BASE_URL}/api/projects/${projectId}/schemas`);
      
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }
      
      const data = await response.json();
      setSchemas(data.schemas || {});
      lastProjectIdRef.current = projectId;
      
    } catch (err) {
      console.error('[useSchemaInference] Errore caricamento schemi:', err);
      setError(err.message);
    } finally {
      setLoading(false);
      fetchingRef.current = false;
    }
  }, [projectId]);

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
      const response = await fetch(
        `${API_BASE_URL}/api/projects/${projectId}/schemas/${nodeId}`
      );
      
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      
      const data = await response.json();
      
      // Aggiorna cache locale
      setSchemas(prev => ({
        ...prev,
        [nodeId]: data.columns || []
      }));
      
      return data.columns || [];
      
    } catch (err) {
      console.error(`[useSchemaInference] Errore caricamento schema nodo ${nodeId}:`, err);
      return [];
    }
  }, [projectId]);

  /**
   * Valida una connessione tra due nodi
   */
  const validateConnection = useCallback(async (sourceNodeId, targetNodeId, config = null) => {
    if (!projectId) return { valid: false, error: 'No project ID' };
    
    try {
      const response = await fetch(
        `${API_BASE_URL}/api/projects/${projectId}/validate-connection`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            source_node_id: sourceNodeId,
            target_node_id: targetNodeId,
            config
          })
        }
      );
      
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      
      return await response.json();
      
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
      const response = await fetch(
        `${API_BASE_URL}/api/projects/${projectId}/validate-all`
      );
      
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      
      return await response.json();
      
    } catch (err) {
      console.error('[useSchemaInference] Errore validazione connessioni:', err);
      return { valid: false, errors: [{ message: err.message }] };
    }
  }, [projectId]);

  // Carica schemi automaticamente quando cambia projectId
  useEffect(() => {
    if (projectId && projectId !== lastProjectIdRef.current) {
      refreshSchemas();
    }
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

/**
 * Hook per ottenere le colonne di input di un nodo transform.
 * Combina gli schemi di tutti i nodi collegati in ingresso.
 */
export function useInputColumns(nodeId, edges, schemas) {
  return useCallback(() => {
    if (!nodeId || !edges || !schemas) return [];
    
    // Trova tutti gli edge che entrano in questo nodo
    const incomingEdges = edges.filter(e => e.target === nodeId);
    
    // Raccogli colonne da tutti i nodi sorgente
    const allColumns = [];
    const seenNames = new Set();
    
    incomingEdges.forEach((edge, edgeIndex) => {
      const sourceSchema = schemas[edge.source] || [];
      
      sourceSchema.forEach(col => {
        // Evita duplicati (per nome)
        const uniqueKey = `${edge.source}_${col.name}`;
        if (!seenNames.has(uniqueKey)) {
          seenNames.add(uniqueKey);
          allColumns.push({
            ...col,
            _sourceEdge: edge.id,
            _sourceIndex: edgeIndex
          });
        }
      });
    });
    
    return allColumns;
  }, [nodeId, edges, schemas])();
}

export default useSchemaInference;
