import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { vi, describe, it, expect, beforeEach, afterEach } from 'vitest';

import ImdfPublisherNode from '../../src/components/nodes/ImdfPublisherNode';

const {
  updateNodeDataMock,
  deleteNodeMock,
  setLastTaskResultMock,
  listFeatureConnectionsMock,
  listFoldersMock,
  publishImdfMock,
  validateImdfPublishMock,
  getTaskStatusMock,
  useCanvasStoreMock,
  canvasStateRef,
} = vi.hoisted(() => ({
  updateNodeDataMock: vi.fn(),
  deleteNodeMock: vi.fn(),
  setLastTaskResultMock: vi.fn(),
  listFeatureConnectionsMock: vi.fn(),
  listFoldersMock: vi.fn(),
  publishImdfMock: vi.fn(),
  validateImdfPublishMock: vi.fn(),
  getTaskStatusMock: vi.fn(),
  useCanvasStoreMock: vi.fn(),
  canvasStateRef: { current: { nodes: [], edges: [] } },
}));

let canvasState;
let setIntervalSpy;
let clearIntervalSpy;

vi.mock('reactflow', () => globalThis.__frontendTestHelpers.createReactFlowMock({ canvasStateRef, showRoot: false }));

vi.mock('../../src/store/useCanvasStore', () => ({
  default: useCanvasStoreMock,
}));

vi.mock('../../src/api/features', () => ({
  listFeatureConnections: listFeatureConnectionsMock,
  listFolders: listFoldersMock,
}));

vi.mock('../../src/api/imdf', () => ({
  publishImdf: publishImdfMock,
  validateImdfPublish: validateImdfPublishMock,
}));

vi.mock('../../src/api/tasks', () => ({
  getTaskStatus: getTaskStatusMock,
}));

vi.mock('../../src/components/modals/ImdfValidatePublishModal', () => ({
  default: ({ isOpen, onProceed, report }) =>
    isOpen ? (
      <div>
        <span>IMDF Validate Publish Modal</span>
        <span>{report?.ok ? 'report-ok' : 'report-missing'}</span>
        <button type="button" onClick={onProceed}>Proceed Publish</button>
      </div>
    ) : null,
}));

describe('ImdfPublisherNode contract', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setIntervalSpy = vi.spyOn(globalThis, 'setInterval').mockImplementation((callback) => {
      Promise.resolve().then(() => callback());
      return 1;
    });
    clearIntervalSpy = vi.spyOn(globalThis, 'clearInterval').mockImplementation(() => {});
    canvasState = {
      updateNodeData: updateNodeDataMock,
      deleteNode: deleteNodeMock,
      setLastTaskResult: setLastTaskResultMock,
      lastTaskResult: {
        imdf: {
          bundle_task_id: 'bundle-77',
          site_id: 'SITE-077',
        },
      },
    };
    canvasStateRef.current = canvasState;
    useCanvasStoreMock.mockImplementation((selector) => selector(canvasState));
    useCanvasStoreMock.getState = () => canvasState;

    listFeatureConnectionsMock.mockResolvedValue({
      connections: [{ id: 'arc-1', name: 'ArcGIS Main' }],
    });
    listFoldersMock.mockResolvedValue({
      folders: [{ id: 'fld-1', title: 'MainFolder' }],
    });
    validateImdfPublishMock.mockResolvedValue({
      ok: true,
      summary: { errors: 0, warnings: 0, layers_count: 1, total_features: 20 },
      layers: [],
    });
    publishImdfMock.mockResolvedValue({
      task_id: 'task-publish-1',
      message: 'queued',
    });
    getTaskStatusMock.mockResolvedValue({
      status: 'success',
      progress: 100,
      message: 'done',
      result: {
        imdf: {
          publish: {
            success: true,
            service_url: 'https://arcgis.example/service/1',
            mode: 'replace',
            layers: [{ name: 'buildings', feature_count: 10 }],
          },
        },
      },
    });
  });

  afterEach(() => {
    setIntervalSpy.mockRestore();
    clearIntervalSpy.mockRestore();
  });

  it('validates and publishes the selected IMDF bundle through the async task contract', async () => {
    const user = userEvent.setup();

    render(
      <ImdfPublisherNode
        id="pub-1"
        data={{
          imdf_arcgis_connection_id: 'arc-1',
          imdf_arcgis_folder: 'MainFolder',
          imdf_fs_name_template: 'imdf_SITE_077',
          imdf_apply_renderers: true,
          imdf_publish_mode: 'replace',
        }}
      />,
    );

    await waitFor(() => {
      expect(listFeatureConnectionsMock).toHaveBeenCalledTimes(1);
      expect(listFoldersMock).toHaveBeenCalledWith('arc-1');
    });

    expect(screen.getByText('SITE-077')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /valida/i }));

    await waitFor(() => {
      expect(validateImdfPublishMock).toHaveBeenCalledWith({
        bundle_task_id: 'bundle-77',
        connection_id: 'arc-1',
        fs_name: 'imdf_SITE_077',
        mode: 'replace',
        folder: 'MainFolder',
      });
    });

    expect(await screen.findByText(/imdf validate publish modal/i)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /proceed publish/i }));

    await waitFor(() => {
      expect(publishImdfMock).toHaveBeenCalledWith({
        bundle_task_id: 'bundle-77',
        connection_id: 'arc-1',
        fs_name: 'imdf_SITE_077',
        folder: 'MainFolder',
        mode: 'replace',
        apply_renderers: true,
      });
    });

    await waitFor(() => {
      expect(getTaskStatusMock).toHaveBeenCalledWith('task-publish-1');
      expect(setLastTaskResultMock).toHaveBeenCalledWith(
        expect.objectContaining({
          imdf: expect.objectContaining({
            publish: expect.objectContaining({
              success: true,
              service_url: 'https://arcgis.example/service/1',
            }),
          }),
        }),
      );
    });
  });
});
