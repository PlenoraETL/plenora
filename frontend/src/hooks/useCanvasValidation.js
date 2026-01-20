import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { validateCanvas } from '../utils/canvasValidation';

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
    
    const result = validateCanvas(currentNodes, currentEdges);
    setValidationResult(result);
    
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

  // Crea una stringa di "fingerprint" per rilevare cambiamenti reali
  const nodesFingerprint = useMemo(() => {
    return nodes.map(n => n.id).sort().join(',');
  }, [nodes]);
  
  const edgesFingerprint = useMemo(() => {
    return edges.map(e => `${e.source}-${e.target}`).sort().join(',');
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
    disconnectedNodeIds: validationResult.disconnectedNodeIds,
    validate,
    clearErrors,
    getNodeStyle
  }), [validationResult, validate, clearErrors, getNodeStyle]);

  return result;
}

export default useCanvasValidation;