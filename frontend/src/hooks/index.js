/**
 * hooks/index.js — Export centralizzato di tutti i custom hooks
 *
 * Importa SEMPRE da qui, non direttamente dai singoli file.
 *
 * Hook disponibili:
 *   usePreview            → gestione anteprima dati (polling Celery)
 *   useExecution          → gestione esecuzione pipeline (polling Celery)
 *   useCaricarica         → gestione scrittura su DB/ArcGIS
 *   useCanvasCore         → stato nodi/edges condiviso (wrapper Zustand)
 *   useCanvasSave         → salvataggio draft uniforme tra editor
 *   useCanvasValidation   → validazione canvas in tempo reale
 *   useCanvasKeyboard     → shortcut tastiera (Delete, Ctrl+S, ...)
 *   useEdgeHandlers       → gestione connessioni tra nodi e modal join
 *   useColumnPropagation  → calcolo colonne lungo il grafo
 *   useSchemaInference    → inferenza schema colonne via backend
 *   useEnrichmentValidation → validazione/stima prima di arricchire
 *   useMultiModal         → gestione multiple modali con un unico stato
 *   useConfirmModal       → dialog di conferma promise-based
 *   useToast              → notifiche toast non bloccanti
 */

export { usePreview }                               from './usePreview';
export { useExecution }                             from './useExecution';
export { useCaricarica }                            from './useCaricarica';
export { useCanvasCore }                            from './useCanvasCore';
export { useCanvasSave }                            from './useCanvasSave';
export { useCanvasValidation }                      from './useCanvasValidation';
export { useCanvasKeyboard }                        from './useCanvasKeyboard';
export { useEdgeHandlers }                          from './useEdgeHandlers';
export { default as useColumnPropagation }          from './useColumnPropagation';
export { useSchemaInference }                       from './useSchemaInference';
export { useEnrichmentValidation }                  from './useEnrichmentValidation';
export { useMultiModal }                            from './useMultiModal';
export { useConfirmModal }                          from './useConfirmModal';
export { useToast }                                 from './useToast';
export { useEditorData }                            from './useEditorData';
export { useTaskPolling }                           from './useTaskPolling';
export { useUnsavedChangesGuard }                   from './useUnsavedChangesGuard';
export { useMountedRef }                            from './useMountedRef';
