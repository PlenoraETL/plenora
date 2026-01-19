import { useState, useCallback } from 'react';
import { previewProject } from '../api/client';

/**
 * Hook per gestire la logica di preview dei progetti
 * Elimina duplicazione di stati e handler tra EstraiEditor, ManipolaEditor, ArricchisciEditor
 * 
 * @param {string} projectId - ID del progetto
 * @param {Function} onBeforePreview - Callback opzionale da eseguire prima della preview (es. salvataggio canvas)
 * @returns {Object} Stati e handler per la preview
 * 
 * Uso:
 * ```jsx
 * const { 
 *   showModal, data, loading, error,
 *   handlePreview, closeModal 
 * } = usePreview(projectId, handleSaveCanvasQuiet);
 * ```
 */
export function usePreview(projectId, onBeforePreview = null) {
  const [showModal, setShowModal] = useState(false);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  /**
   * Esegue la preview del progetto
   * @param {number} limit - Numero massimo di righe (default 100)
   */
  const handlePreview = useCallback(async (limit = 100) => {
    setShowModal(true);
    setLoading(true);
    setError(null);
    setData(null);
    
    try {
      // Esegui callback pre-preview (es. salvataggio canvas)
      if (onBeforePreview) {
        await onBeforePreview();
      }
      
      // Chiama API preview
      const result = await previewProject(projectId, limit);
      
      if (result.success) {
        setData(result);
      } else {
        setError(result.message || 'Errore generazione preview');
      }
    } catch (err) {
      console.error('[usePreview] Errore:', err);
      setError(err.message || 'Errore durante la preview');
    } finally {
      setLoading(false);
    }
  }, [projectId, onBeforePreview]);

  /**
   * Chiude il modal e resetta lo stato
   */
  const closeModal = useCallback(() => {
    setShowModal(false);
  }, []);

  /**
   * Resetta completamente lo stato
   */
  const reset = useCallback(() => {
    setShowModal(false);
    setData(null);
    setLoading(false);
    setError(null);
  }, []);

  return {
    // Stati
    showModal,
    data,
    loading,
    error,
    // Handler
    handlePreview,
    closeModal,
    reset
  };
}

export default usePreview;
