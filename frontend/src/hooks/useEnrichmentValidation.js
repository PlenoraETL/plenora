/**
 * useEnrichmentValidation.js — Hook per validazione e stima enrichment
 *
 * Prima di avviare un arricchimento, valida il mapping parametri e stima
 * il numero di chiamate API e il tempo necessario.
 *
 * Ritorna: { validating, validation, estimate, validate, reset }
 */
import { useState, useCallback, useRef } from 'react';
import { validateEnrichment, estimateEnrichment } from '../api';
import { useMountedRef } from './useMountedRef';

/**
 * Hook per validazione e stima enrichment
 * 
 * Uso:
 * const validation = useEnrichmentValidation();
 * 
 * // Prima di eseguire:
 * const result = await validation.validateAndEstimate(projectId, apiId, paramMapping);
 * if (!result.validation.valid) {
 *   // Mostra errori
 *   return;
 * }
 */
export function useEnrichmentValidation() {
  // Stati validazione
  const [validation, setValidation] = useState(null);
  const [isValidating, setIsValidating] = useState(false);
  const [validationError, setValidationError] = useState(null);
  
  // Stati stima
  const [estimation, setEstimation] = useState(null);
  const [isEstimating, setIsEstimating] = useState(false);
  const [estimationError, setEstimationError] = useState(null);

  // Mounted guard: previene setState post-unmount durante la
  // validation/estimation (l'utente puo' chiudere il modale o navigare
  // via mentre la richiesta e' in volo).
  const mountedRef = useMountedRef();

  // P1 (2026-05-26): requestId guard. mountedRef protegge solo da
  // setState post-unmount; non protegge da chiamate concorrenti sullo
  // stesso componente montato (es. utente chiude/riapre il modal: due
  // validate() in volo, la stale puo' vincere). Pattern stesso di
  // useSchemaInference: incrementiamo `validateRequestRef` su ogni
  // call; gli setter rispondono solo se requestId == ref corrente.
  const validateRequestRef = useRef(0);
  const estimateRequestRef = useRef(0);

  /**
   * Valida la configurazione enrichment
   */
  const validate = useCallback(async (projectId, apiId, paramMapping, fileSourceId = null, staticColumns = null, staticParameters = null) => {
    validateRequestRef.current += 1;
    const requestId = validateRequestRef.current;
    setIsValidating(true);
    setValidationError(null);

    try {
      const result = await validateEnrichment(projectId, apiId, paramMapping, fileSourceId, staticColumns, staticParameters);
      if (mountedRef.current && requestId === validateRequestRef.current) {
        setValidation(result);
      }
      return result;
    } catch (error) {
      const errorMsg = error.message || 'Errore durante la validazione';
      if (mountedRef.current && requestId === validateRequestRef.current) {
        setValidationError(errorMsg);
        setValidation({ valid: false, errors: [errorMsg] });
      }
      return { valid: false, errors: [errorMsg] };
    } finally {
      if (mountedRef.current && requestId === validateRequestRef.current) {
        setIsValidating(false);
      }
    }
  }, [mountedRef]);

  /**
   * Stima tempo esecuzione
   */
  const estimate = useCallback(async (projectId, apiId, paramMapping, fileSourceId = null) => {
    estimateRequestRef.current += 1;
    const requestId = estimateRequestRef.current;
    setIsEstimating(true);
    setEstimationError(null);

    try {
      const result = await estimateEnrichment(projectId, apiId, paramMapping, fileSourceId);
      if (mountedRef.current && requestId === estimateRequestRef.current) {
        setEstimation(result);
      }
      return result;
    } catch (error) {
      const errorMsg = error.message || 'Errore durante la stima';
      if (mountedRef.current && requestId === estimateRequestRef.current) {
        setEstimationError(errorMsg);
        setEstimation(null);
      }
      return null;
    } finally {
      if (mountedRef.current && requestId === estimateRequestRef.current) {
        setIsEstimating(false);
      }
    }
  }, [mountedRef]);

  /**
   * Valida E stima in un'unica chiamata
   */
  const validateAndEstimate = useCallback(async (projectId, apiId, paramMapping, fileSourceId = null, staticColumns = null, staticParameters = null) => {
    // Prima valida
    const validResult = await validate(projectId, apiId, paramMapping, fileSourceId, staticColumns, staticParameters);

    // Se valido, stima
    if (validResult.valid) {
      const estimateResult = await estimate(projectId, apiId, paramMapping, fileSourceId);
      return { validation: validResult, estimation: estimateResult };
    }

    return { validation: validResult, estimation: null };
  }, [validate, estimate]);

  /**
   * Reset stati
   */
  const reset = useCallback(() => {
    setValidation(null);
    setValidationError(null);
    setEstimation(null);
    setEstimationError(null);
  }, []);

  /**
   * Formatta tempo in modo leggibile
   */
  const formatTime = useCallback((seconds) => {
    if (!seconds) return '-';
    if (seconds < 60) return `${Math.round(seconds)} secondi`;
    if (seconds < 3600) {
      const mins = Math.floor(seconds / 60);
      const secs = Math.round(seconds % 60);
      return `${mins} min ${secs} sec`;
    }
    const hours = Math.floor(seconds / 3600);
    const mins = Math.floor((seconds % 3600) / 60);
    return `${hours}h ${mins}m`;
  }, []);

  return {
    // Funzioni
    validate,
    estimate,
    validateAndEstimate,
    reset,
    formatTime,
    
    // Stati validazione
    validation,
    isValidating,
    validationError,
    
    // Stati stima
    estimation,
    isEstimating,
    estimationError,
    
    // Shortcut
    isValid: validation?.valid ?? null,
    hasWarnings: (validation?.warnings?.length || 0) > 0,
    estimatedTime: estimation?.estimated_time_formatted || null,
    estimatedSeconds: estimation?.estimated_time_seconds || 0,
    totalRows: estimation?.total_rows || validation?.total_rows || 0,
    deduplicationSavings: estimation?.deduplication_savings_percent || 0
  };
}

export default useEnrichmentValidation;