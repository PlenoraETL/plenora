/**
 * CanvasGraphContext — analisi del flusso del canvas (Fase 4, M6:
 * utils/canvasGraph.analyzeCanvas) calcolata UNA volta da EditorShell e
 * solo quando cambia la struttura (structureKey): spostare un nodo non la
 * ricalcola e non ridisegna tutti i nodi. La leggono i nodi compatti, il
 * pannello laterale e la vista Passi.
 */
import { createContext } from 'react';

const EMPTY = {
  key: '', reaching: new Set(), dangling: new Set(), inputless: new Set(), lanes: [], laneOf: new Map(), isEnd: () => false,
  ancestorsOf: () => new Set(), danglingEndsFrom: () => [],
};

export const CanvasGraphContext = createContext(EMPTY);
