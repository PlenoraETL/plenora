/**
 * Custom Hooks per DataFlow Manager
 * 
 * Questi hooks centralizzano logica comune tra i vari Editor
 * eliminando duplicazione di codice.
 */

// Preview hook - gestisce stati e logica anteprima dati
export { usePreview } from './usePreview';

// Execution hook - gestisce stati e logica esecuzione progetti
export { useExecution } from './useExecution';

// Canvas keyboard hooks - gestisce shortcut tastiera
export { 
  useCanvasKeyboard, 
  useCanvasSaveShortcut, 
  useCanvasShortcuts 
} from './useCanvasKeyboard';

// Re-export dello schema inference hook esistente
export { useSchemaInference, useInputColumns } from './useSchemaInference';
