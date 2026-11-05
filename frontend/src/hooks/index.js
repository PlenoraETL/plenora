/**
 * hooks/index.js — Export centralizzato di tutti i custom hooks
 *
 * Importa SEMPRE da qui, non direttamente dai singoli file.
 *
 * Hook disponibili:
 *   usePreview            → gestione anteprima dati (polling task)
 *   useExecution          → gestione esecuzione pipeline (polling task)
 *   useCanvasCore         → stato nodi/edges condiviso (wrapper Zustand)
 *   useCanvasSave         → salvataggio draft uniforme tra editor
 *   useCanvasKeyboard     → shortcut tastiera (Delete, Ctrl+S, ...)
 *   useEdgeHandlers       → creazione ed eliminazione degli archi di Estrai (join nel pannello)
 *   useColumnsOf          → colonne di un nodo dalla fonte unica (useColumns)
 *   useMultiModal         → gestione multiple modali con un unico stato
 *   useConfirmModal       → dialog di conferma promise-based
 *   useToast              → notifiche toast non bloccanti
 */

export { usePreview }                               from './usePreview';
export { useExecution }                             from './useExecution';
export { useCanvasCore }                            from './useCanvasCore';
export { useCanvasSave }                            from './useCanvasSave';
export { useCanvasKeyboard }                        from './useCanvasKeyboard';
export { useEdgeHandlers }                          from './useEdgeHandlers';
export { useColumnsOf, useColumnSync }             from './useColumns';
export { useMultiModal }                            from './useMultiModal';
export { useConfirmModal }                          from './useConfirmModal';
export { useToast }                                 from './useToast';
export { useEditorData }                            from './useEditorData';
export { useUnsavedChangesGuard }                   from './useUnsavedChangesGuard';
export { useMountedRef }                            from './useMountedRef';
export { useValidationHighlights }                  from './useValidationHighlights';
