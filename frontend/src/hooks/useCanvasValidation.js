/**
 * useCanvasValidation.js — Validazione canvas in tempo reale
 *
 * Osserva nodes/edges e valida continuamente il canvas:
 * grafo connesso, nodo risultato presente, nodi disconnessi, cicli.
 * Debounced per non ricalcolare a ogni micro-cambiamento.
 *
 * Ritorna: { isValid, errors, disconnectedNodeIds, validate }
 */
import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { validateEstraiCanvas } from '../utils/validateEstraiCanvas';

/**
 * useCanvasValidation - Hook per validazione canvas in tempo reale
 * 
 * @param {Array} nodes - Nodi React Flow
 * @param {Array} edges - Edges React Flow
 * @param {Object} options - Opzioni
 * @param {boolean} options.validateOnChange - Valida automaticamente (default: true)
 * @param {number} options.debounceMs - Debounce validazione (default: 300)
 * @param {Function} options.onValidationChange - Callback su cambio validazione
 * 
 * @returns {Object} { 
 *   isValid, 
 *   errors, 
 *   disconnectedNodeIds, 
 *   validate, 
 *   clearErrors,
 *   getNodeStyle 
 * }
 */
export function useCanvasValidation(nodes, edges, options = {}) {
  const {
    validateOnChange = true,
    debounceMs = 300,
    onValidationChange
  } = options;

  const [validationResult, setValidationResult] = useState({
    valid: true,
    errors: [],
    issues: [],
    disconnectedNodeIds: []
  });

  // Ref per avere sempre i valori aggiornati (evita stale closure)
  const nodesRef = useRef(nodes);
  const edgesRef = useRef(edges);
  
  useEffect(() => {
    nodesRef.current = nodes;
    edgesRef.current = edges;
  }, [nodes, edges]);

  // Validazione manuale - usa ref per evitare stale closure
  const validate = useCallback(() => {
    const currentNodes = nodesRef.current;
    const currentEdges = edgesRef.current;
    
    const result = validateEstraiCanvas(currentNodes, currentEdges);
    // Adatta formato: mappiamo issues → errors per retrocompatibilità
    const adapted = {
      valid: result.isValid,
      issues: result.issues,
      errors: result.issues.filter(i => i.severity === 'error').map(i => i.message),
      disconnectedNodeIds: result.issues.filter(i => i.nodeId).map(i => i.nodeId)
    };
    setValidationResult(adapted);
    
    if (onValidationChange) {
      onValidationChange(result);
    }
    
    return result;
  }, [onValidationChange]);

  // Pulisci errori
  const clearErrors = useCallback(() => {
    setValidationResult({
      valid: true,
      errors: [],
      disconnectedNodeIds: []
    });
  }, []);

  // ✅ FIX: Crea fingerprint che include i DATI rilevanti per la validazione
  const nodesFingerprint = useMemo(() => {
    return nodes.map(n => {
      // Per tableNode, includi: id, alias, colonne visibili con alias
      if (n.type === 'tableNode') {
        const visibleCols = (n.data?.columns || [])
          .filter(c => c.visible)
          .map(c => `${c.name}:${c.alias || ''}`)
          .join('|');
        return `${n.id}:${n.data?.alias || ''}:${visibleCols}`;
      }
      // Per resultNode, includi solo id
      return n.id;
    }).sort().join(',');
  }, [nodes]);
  
  const edgesFingerprint = useMemo(() => {
    return edges.map(e => {
      // Includi configurazione join: tipo, colonne, spatial
      const joinType = e.data?.join_type || 'left';
      const srcCol = e.data?.source_column || '';
      const tgtCol = e.data?.target_column || '';
      const joinConds = e.data?.join_conditions ? JSON.stringify(e.data.join_conditions) : '';
      const onFilters = e.data?.on_filters ? JSON.stringify(e.data.on_filters) : '';
      const isSpatial = e.data?.is_spatial ? 'S' : 'N';
      const spatialPred = e.data?.spatial_predicate || '';
      const srcGeom = e.data?.source_geom_column || '';
      const tgtGeom = e.data?.target_geom_column || '';
      
      return `${e.source}-${e.target}:${joinType}:${srcCol}:${tgtCol}:${joinConds}:${onFilters}:${isSpatial}:${spatialPred}:${srcGeom}:${tgtGeom}`;
    }).sort().join(',');
  }, [edges]);

  // Validazione automatica con debounce
  // Dipende da fingerprint invece che dagli array interi
  useEffect(() => {
    if (!validateOnChange || nodes.length === 0) return;

    const timeoutId = setTimeout(() => {
      validate();
    }, debounceMs);

    return () => clearTimeout(timeoutId);
  }, [nodesFingerprint, edgesFingerprint, validateOnChange, debounceMs, validate, nodes.length]);

  // Stile per nodi disconnessi
  const getNodeStyle = useCallback((nodeId) => {
    if (validationResult.disconnectedNodeIds.includes(nodeId)) {
      return {
        border: '2px solid #f44336',
        boxShadow: '0 0 10px rgba(244, 67, 54, 0.3)'
      };
    }
    return undefined;
  }, [validationResult.disconnectedNodeIds]);

  // Memoizza risultato
  const result = useMemo(() => ({
    isValid: validationResult.valid,
    errors: validationResult.errors,
    issues: validationResult.issues || [],
    disconnectedNodeIds: validationResult.disconnectedNodeIds,
    validate,
    clearErrors,
    getNodeStyle
  }), [validationResult, validate, clearErrors, getNodeStyle]);

  return result;
}

export default useCanvasValidation;