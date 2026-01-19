import { useState, useEffect, useCallback, useMemo } from 'react';
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

  // Validazione manuale
  const validate = useCallback(() => {
    const result = validateCanvas(nodes, edges);
    setValidationResult(result);
    
    if (onValidationChange) {
      onValidationChange(result);
    }
    
    return result;
  }, [nodes, edges, onValidationChange]);

  // Pulisci errori
  const clearErrors = useCallback(() => {
    setValidationResult({
      valid: true,
      errors: [],
      disconnectedNodeIds: []
    });
  }, []);

  // Validazione automatica con debounce
  useEffect(() => {
    if (!validateOnChange || nodes.length === 0) return;

    const timeoutId = setTimeout(() => {
      validate();
    }, debounceMs);

    return () => clearTimeout(timeoutId);
  }, [nodes, edges, validateOnChange, debounceMs, validate]);

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
