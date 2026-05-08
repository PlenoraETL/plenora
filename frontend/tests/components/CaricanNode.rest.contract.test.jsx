import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import CaricanNode from '../../src/components/nodes/CaricanNode';

const {
  updateNodeDataMock,
  deleteNodeMock,
  getCaricaSchemasMock,
  getCaricaTablesMock,
  getCaricaColumnsMock,
  useCanvasStoreMock,
  canvasStateRef,
} = vi.hoisted(() => ({
  updateNodeDataMock: vi.fn(),
  deleteNodeMock: vi.fn(),
  getCaricaSchemasMock: vi.fn(),
  getCaricaTablesMock: vi.fn(),
  getCaricaColumnsMock: vi.fn(),
  useCanvasStoreMock: vi.fn(),
  canvasStateRef: { current: { nodes: [], edges: [] } },
}));

vi.mock('reactflow', () => globalThis.__frontendTestHelpers.createReactFlowMock({ canvasStateRef, showRoot: false }));

vi.mock('../../src/store/useCanvasStore', () => ({
  default: useCanvasStoreMock,
}));

vi.mock('../../src/api/carica', () => ({
  getCaricaSchemas: getCaricaSchemasMock,
  getCaricaTables: getCaricaTablesMock,
  getCaricaColumns: getCaricaColumnsMock,
  postCaricaAutoMapping: vi.fn(),
  postCaricaCheckConflicts: vi.fn(),
}));

function renderNode(data = {}) {
  const state = {
    connections: [{ id: 'db-1', name: 'Main DB', type: 'postgres' }],
    nodes: [],
    edges: [],
    projectId: 'project-1',
    updateNodeData: updateNodeDataMock,
    deleteNode: deleteNodeMock,
  };
  canvasStateRef.current = state;
  useCanvasStoreMock.mockImplementation((selector) => selector(state));
  useCanvasStoreMock.getState = () => state;

  return render(
    <CaricanNode
      id="carica-1"
      data={{
        availableColumns: [{ name: 'city', alias: 'City' }, { name: 'score' }],
        ...data,
      }}
    />,
  );
}

describe('CaricanNode REST destination controls', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getCaricaSchemasMock.mockResolvedValue({ schemas: ['public'] });
    getCaricaTablesMock.mockResolvedValue({ tables: ['cities'] });
    getCaricaColumnsMock.mockResolvedValue({ columns: [] });
  });

  it('renders REST controls and keeps DB fields hidden for REST destinations', () => {
    renderNode({
      dest_type: 'rest',
      rest_url: 'https://api.example.com/items',
      rest_method: 'PATCH',
      rest_batch_mode: 'per_record',
      rest_batch_size: 25,
      rest_timeout_seconds: 45,
      rest_headers: [{ name: 'Authorization', value: 'Bearer secret', secret: true }],
      rest_body_mapping: [{ source: 'city', target: 'payload.city', required: true }],
    });

    expect(screen.getByText('CARICA su REST')).toBeInTheDocument();
    expect(screen.getByDisplayValue('https://api.example.com/items')).toBeInTheDocument();
    expect(screen.getByDisplayValue('PATCH')).toBeInTheDocument();
    expect(screen.queryByText(/Connessione Database/i)).not.toBeInTheDocument();

    fireEvent.click(screen.getByText(/Body Mapping/i));
    expect(screen.getByDisplayValue('payload.city')).toBeInTheDocument();

    fireEvent.click(screen.getByText(/Headers e batch/i));
    expect(screen.getByDisplayValue('25')).toBeInTheDocument();
    expect(screen.getByDisplayValue('45')).toBeInTheDocument();
    expect(screen.getByDisplayValue('Authorization')).toBeInTheDocument();
  });

  it('updates REST destination fields through the canvas store', () => {
    renderNode({ dest_type: 'rest' });

    fireEvent.change(screen.getByPlaceholderText('https://api.example.com/items'), {
      target: { value: 'https://api.example.com/new-items' },
    });
    fireEvent.change(screen.getByDisplayValue('POST'), {
      target: { value: 'PUT' },
    });

    expect(updateNodeDataMock).toHaveBeenCalledWith('carica-1', {
      rest_url: 'https://api.example.com/new-items',
    });
    expect(updateNodeDataMock).toHaveBeenCalledWith('carica-1', {
      rest_method: 'PUT',
    });
  });

  it('switches an existing DB node to REST without keeping DB destination fields active', () => {
    renderNode({
      connection_id: '',
      schema_name: 'public',
      table_name: 'cities',
    });

    fireEvent.click(screen.getByRole('button', { name: 'REST' }));

    expect(updateNodeDataMock).toHaveBeenCalledWith('carica-1', expect.objectContaining({
      dest_type: 'rest',
      connection_id: '',
      schema_name: '',
      table_name: '',
      match_columns: [],
      column_mappings: [],
    }));
  });
});
