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
    default: ({ children }) => (
      showRoot ? React.createElement('div', { 'data-testid': 'reactflow' }, children) : children
    ),
    Background: () => null,
    Controls: () => null,
    MiniMap: () => null,
    Handle: () => React.createElement('div', { 'data-testid': 'handle' }),
    Position: { Left: 'left', Right: 'right' },
    MarkerType: { ArrowClosed: 'ArrowClosed' },
    addEdge: addEdge || ((edge, edges) => [...edges, edge]),
    useReactFlow: () => ({
      screenToFlowPosition: ({ x, y }) => ({ x, y }),
      getEdges: () => readState().edges || [],
      getNodes: () => readState().nodes || [],
    }),
    useStore: (selector) => selector({
      edges: readState().edges || [],
      nodes: readState().nodes || [],
    }),
    ReactFlowProvider: ({ children }) => React.createElement(React.Fragment, null, children),
  };
}
