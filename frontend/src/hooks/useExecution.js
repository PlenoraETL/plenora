import { useState, useCallback } from 'react';
import { executeProjectQuery, getDownloadUrl } from '../api/client';

/**
 * Hook per gestire la logica di esecuzione dei progetti
 * Elimina duplicazione di stati e handler tra EstraiEditor, ManipolaEditor, ArricchisciEditor
 * 
 * @param {string} projectId - ID del progetto
 * @param {Object} options - Opzioni di configurazione
 * @param {Function} options.onBeforeExecute - Callback da eseguire prima dell'esecuzione (es. salvataggio canvas)
 * @param {Function} options.validateCanvas - Funzione di validazione canvas (opzionale)
 * @returns {Object} Stati e handler per l'esecuzione
 * 
 * Uso:
 * ```jsx
 * const { 
 *   showModal, result, isExecuting,
 *   handleExecute, closeModal, downloadUrl 
 * } = useExecution(projectId, {
 *   onBeforeExecute: handleSaveCanvas,
 *   validateCanvas: () => { ... }
 * });
 * ```
 */
export function useExecution(projectId, options = {}) {
  const { onBeforeExecute = null, validateCanvas = null } = options;
  
  const [showModal, setShowModal] = useState(false);
  const [result, setResult] = useState(null);
  const [isExecuting, setIsExecuting] = useState(false);

  /**
   * URL per il download del file generato
   */
  const downloadUrl = getDownloadUrl(projectId);

  /**
   * Esegue il progetto
   * @param {Object} executeOptions - Opzioni per l'esecuzione
   * @param {boolean} executeOptions.force_recalculate - Forza ricalcolo completo
   * @param {string} executeOptions.format - Formato output (opzionale)
   * @param {string} executeOptions.connection_id - ID connessione (per progetti SQL)
   */
  const handleExecute = useCallback(async (executeOptions = {}) => {
    setShowModal(true);
    setIsExecuting(true);
    setResult(null);
    
    try {
      // Validazione canvas (opzionale)
      if (validateCanvas) {
        const validationResult = validateCanvas();
        if (validationResult && !validationResult.valid) {
          throw new Error(
            validationResult.errors?.join('\n') || 'Canvas non valido'
          );
        }
      }
      
      // Esegui callback pre-esecuzione (es. salvataggio canvas)
      if (onBeforeExecute) {
        await onBeforeExecute();
      }
      
      // Esegui progetto
      const response = await executeProjectQuery(projectId, executeOptions);
      
      setResult(response);
      
    } catch (error) {
      console.error('[useExecution] Errore:', error);
      setResult({
        success: false,
        message: error.response?.data?.detail || error.message || 'Errore durante l\'esecuzione'
      });
    } finally {
      setIsExecuting(false);
    }
  }, [projectId, onBeforeExecute, validateCanvas]);

  /**
   * Chiude il modal
   * @param {boolean} resetResult - Se true, resetta anche il risultato
   */
  const closeModal = useCallback((resetResult = false) => {
    setShowModal(false);
    if (resetResult) {
      setResult(null);
    }
  }, []);

  /**
   * Resetta completamente lo stato
   */
  const reset = useCallback(() => {
    setShowModal(false);
    setResult(null);
    setIsExecuting(false);
  }, []);

  return {
    // Stati
    showModal,
    result,
    isExecuting,
    downloadUrl,
    // Handler
    handleExecute,
    closeModal,
    reset,
    // Setter per casi speciali
    setShowModal,
    setResult
  };
}

export default useExecution;
