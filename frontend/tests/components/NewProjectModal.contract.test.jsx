import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const { createProjectMock, navigateMock, toastMock } = vi.hoisted(() => ({
  createProjectMock: vi.fn(),
  navigateMock: vi.fn(),
  toastMock: { success: vi.fn(), warning: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

vi.mock('../../src/api', () => ({ createProject: createProjectMock }));
vi.mock('react-router-dom', () => ({ useNavigate: () => navigateMock }));
vi.mock('../../src/hooks/useToast', () => ({ useToast: () => toastMock }));

import NewProjectModal from '../../src/components/projects/NewProjectModal';
import { PROJECT_EDITOR_ROUTES } from '../../src/utils/projectRoutes';

/**
 * UI-3 (06/11/2026), U10: «Crea progetto» porta nell'editor del progetto
 * nuovo e lo dice; Invio in un campo crea come il pulsante.
 */
beforeEach(() => { vi.clearAllMocks(); });

describe('creating a project', () => {
  it.each(Object.keys(PROJECT_EDITOR_ROUTES))('%s: Enter creates, the editor opens, a message says so', async (type) => {
    createProjectMock.mockResolvedValue({ metadata: { id: 'new-1', project_type: type } });
    const onCreated = vi.fn();
    render(<NewProjectModal isOpen initialType={type} connections={[{ id: 'c1', name: 'DB', database: 'x' }]} onClose={() => {}} onCreated={onCreated} />);
    if (type === 'estrai') fireEvent.change(screen.getByLabelText(/Connessione/), { target: { value: 'c1' } });
    const name = screen.getByPlaceholderText(/Catasto Campania/);
    fireEvent.change(name, { target: { value: '  Nuovo ' } });
    fireEvent.submit(name.closest('form'));
    await waitFor(() => expect(navigateMock).toHaveBeenCalledWith(PROJECT_EDITOR_ROUTES[type].replace(':projectId', 'new-1')));
    expect(createProjectMock).toHaveBeenCalledWith('Nuovo', '', type, type === 'estrai' ? 'c1' : '');
    expect(onCreated).toHaveBeenCalled();
    expect(toastMock.success).toHaveBeenCalledWith(expect.stringContaining('Nuovo'));
  });
});
