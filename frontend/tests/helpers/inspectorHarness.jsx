/**
 * Banco comune dei test del pannello del passo: OGNI tipo di nodo di ogni
 * mappa `nodeTypes` dei tre editor (Estrai, Manipola, Arricchisci; ricavate
 * dai file degli editor) montato in NodeInspector.
 *
 * Lo usano InspectorShell.contract (guscio, sola lettura) e
 * undoActions.contract (azioni discrete dei pannelli). I `vi.mock` restano
 * nei file di test (sono per file): servono almeno @xyflow/react (con
 * useNodeId: () => null), api/http in attesa, useToast, RunHistoryModal e
 * transformRegistry (vedi InspectorShell.contract.test.jsx).
 */
import fs from 'node:fs';
import path from 'node:path';
import React from 'react';
import { render } from '@testing-library/react';
import { ConfirmProvider } from '../../src/context/ConfirmContext';
import useCanvasStore from '../../src/store/useCanvasStore';
import NodeInspector from '../../src/components/canvas/NodeInspector';
import TableNode from '../../src/components/nodes/TableNode';
import FeatureLayerNode from '../../src/components/nodes/FeatureLayerNode';
import DataSourceNode from '../../src/components/nodes/DataSourceNode';
import StaticValuesNode from '../../src/components/nodes/StaticValuesNode';
import ProjectInputNode from '../../src/components/nodes/ProjectInputNode';
import ProjectSourceNode from '../../src/components/nodes/ProjectSourceNode';
import FileSourceNode from '../../src/components/nodes/FileSourceNode';
import ResultNode from '../../src/components/nodes/ResultNode';
import CaricanNode from '../../src/components/nodes/CaricanNode';
import CaricanArcGISNode from '../../src/components/nodes/CaricanArcGISNode';
import AnnotationNode from '../../src/components/nodes/AnnotationNode';
import TransformNode from '../../src/components/transforms/TransformNode';
import MultiJoinNode from '../../src/components/transforms/MultiJoinNode';
import ApiEnrichNode from '../../src/components/panels/ApiEnrichNode';

// Nome del componente nel file dell'editor -> componente.
export const COMPONENTS = {
  TableNode, FeatureLayerNode, DataSourceNode, StaticValuesNode, ProjectInputNode,
  ProjectSourceNode, FileSourceNode, ResultNode, CaricanNode, CaricanArcGISNode,
  AnnotationNode, TransformNode, MultiJoinNode, ApiEnrichNode,
};

const EDITORS = [
  ['estrai', 'EstraiEditor.jsx'],
  ['manipola', 'ManipolaEditor.jsx'],
  ['arricchisci', 'ArricchisciEditor.jsx'],
];
const pages = path.resolve(import.meta.dirname, '../../src/pages');
export const editorMaps = EDITORS.map(([mode, file]) => {
  const src = fs.readFileSync(path.join(pages, file), 'utf-8');
  const body = src.match(/const nodeTypes = \{([\s\S]*?)\n\};/)[1].replace(/\/\/[^\n]*/g, '');
  const pairs = [...body.matchAll(/\b(\w+):\s*(\w+)/g)].map(m => [m[1], m[2]]);
  return { mode, file, pairs };
});

// Dati minimi per tipo: quanto basta perche' il modulo si monti.
export const SAMPLE = {
  transform: { transform_op: 'filter', config: {} },
  datasourceNode: { mode: 'source', columns: [] },
  datasource_node: { mode: 'source', columns: [] },
  resultNode: { fileName: 'output', format: 'csv', columns: [] },
  result: { fileName: 'output', format: 'csv', columns: [], isManipulate: true },
  multi_join: { config: { join_keys: [] }, columns: [] },
};

// Ogni tipo di ogni editor, note escluse (restano libere sul canvas).
export const CASES = editorMaps.flatMap(({ mode, file, pairs }) => pairs
  .filter(([type]) => type !== 'comment')
  .map(([type, component]) => ({ mode, file, type, component })));

/**
 * Monta il pannello del nodo `n1` del caso. `wrap(Full)` puo' avvolgere il
 * modulo (es. per leggere le props); `data` si aggiunge ai dati minimi.
 */
export function mountInspector({ mode, type, component }, { readOnly = false, wrap = null, data = {}, canvasStateRef = null, storePatch = null } = {}) {
  const store = useCanvasStore.getState();
  store.reset();
  store.init('p1', mode);
  store.setReadOnly(readOnly);
  store.loadGraph([{ id: 'n1', type, position: { x: 0, y: 0 }, selected: true, data: { ...(SAMPLE[type] || {}), ...data } }], []);
  if (storePatch) useCanvasStore.setState(storePatch);
  if (canvasStateRef) canvasStateRef.current = useCanvasStore.getState();
  const Full = wrap ? wrap(COMPONENTS[component]) : COMPONENTS[component];
  return render(<ConfirmProvider><NodeInspector fullNodeTypes={{ [type]: Full }} prefix={mode} /></ConfirmProvider>);
}
