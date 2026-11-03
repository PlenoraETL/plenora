import React from 'react';

export function createReactFlowMock(options = {}) {
  const {
    canvasStateRef = { current: null },
    addEdge,
    showRoot = true,
  } = options;

  const readState = () => canvasStateRef.current || {};

  return {
    __esModule: true,
    // React Flow v12 (@xyflow/react): ReactFlow e' un export con nome.
    ReactFlow: ({ children }) => (
      showRoot ? React.createElement('div', { 'data-testid': 'reactflow' }, children) : children
    ),
    default: ({ children }) => (
      showRoot ? React.createElement('div', { 'data-testid': 'reactflow' }, children) : children
    ),
    Background: () => null,
    Controls: () => null,
    MiniMap: () => null,
    NodeResizer: () => null,
    // I test montano i nodi come se fossero sul canvas: NodeHandle disegna gli handle.
    useNodeId: () => 'test-node',
    applyNodeChanges: (changes, nodes) => nodes,
    applyEdgeChanges: (changes, edges) => edges,
    Handle: () => React.createElement('div', { 'data-testid': 'handle' }),
    Position: { Left: 'left', Right: 'right' },
    MarkerType: { ArrowClosed: 'ArrowClosed' },
    addEdge: addEdge || ((edge, edges) => [...edges, edge]),
    useReactFlow: () => ({
      screenToFlowPosition: ({ x, y }) => ({ x, y }),
      getEdges: () => readState().edges || [],
      getNodes: () => readState().nodes || [],
      setCenter: () => {},
    }),
    useStore: (selector) => selector({
      edges: readState().edges || [],
      nodes: readState().nodes || [],
    }),
    ReactFlowProvider: ({ children }) => React.createElement(React.Fragment, null, children),
  };
}
