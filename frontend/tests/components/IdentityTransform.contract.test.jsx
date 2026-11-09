/**
 * «Utente che esegue»: trasformazione nel pannello dell'input (09/11/2026).
 *
 *  1. Il campo «Trasformazione (facoltativa)» scrive `identity_transform`
 *     nel nodo (entra nel canvas salvato e quindi nel manifest pubblicato).
 *  2. Con trasformazione e nome utente di prova il pannello mostra
 *     l'anteprima calcolata dal SERVER («E0041 → 004_IS»); il client non
 *     calcola nulla. Senza l'una o l'altro: nessuna richiesta.
 *  3. Una trasformazione che non si applica mostra il motivo del server.
 *  4. Cambiare tipo toglie la trasformazione (vale solo per il tipo server).
 */
import React from 'react';
import { render, screen, fireEvent, cleanup, waitFor } from '@testing-library/react';
import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';

vi.mock('../../src/hooks/useTransformRegistry', () => ({ useTransformRegistry: () => ({ data: null, status: 'loading' }) }));
vi.mock('../../src/api/projects', async (importOriginal) => ({
  ...(await importOriginal()),
  previewIdentityTransform: vi.fn(),
}));

import { previewIdentityTransform } from '../../src/api/projects';
import ProjectInputNode from '../../src/components/nodes/ProjectInputNode';
import useCanvasStore from '../../src/store/useCanvasStore';
import { IDENTITY_EXAMPLE } from '../../src/utils/guides/utenteCheEsegue';

const RULE = IDENTITY_EXAMPLE.formula;

const mount = (data) => {
  const st = useCanvasStore.getState();
  st.reset();
  st.init('p1', 'estrai');
  const full = { input_kind: 'current_user', input_key: 'utente', label: 'Utente', ...data };
  st.loadGraph([{ id: 'u', type: 'project_input', position: { x: 0, y: 0 }, data: full }], []);
  return render(<ProjectInputNode id="u" data={full} />);
};

const nodeData = () => useCanvasStore.getState().nodes.find(n => n.id === 'u').data;

beforeEach(() => { previewIdentityTransform.mockReset(); });
afterEach(() => { cleanup(); });

describe('Trasformazione dell\'input «Utente che esegue»', () => {
  it('the field writes identity_transform in the node', () => {
    const { rerender } = mount({});
    const field = () => screen.getByLabelText(/Trasformazione \(facoltativa\)/);
    fireEvent.change(field(), { target: { value: `  ${RULE} ` } });
    fireEvent.blur(field());
    expect(nodeData().identity_transform).toBe(RULE);
    rerender(<ProjectInputNode id="u" data={nodeData()} />);
    fireEvent.change(field(), { target: { value: '' } });
    fireEvent.blur(field());
    expect(nodeData().identity_transform).toBeNull();
  });

  it('shows the preview computed by the server (E0041 → 004_IS)', async () => {
    previewIdentityTransform.mockResolvedValue({ success: true, value: '004_IS', message: null });
    mount({ identity_transform: RULE, preview_username: 'E0041' });
    const status = await screen.findByRole('status', { name: 'Anteprima della trasformazione' });
    expect(status).toHaveTextContent('E0041 → 004_IS');
    expect(previewIdentityTransform).toHaveBeenCalledWith(RULE, 'E0041', expect.objectContaining({ signal: expect.anything() }));
  });

  it.each([
    [{ identity_transform: RULE }],
    [{ preview_username: 'E0041' }],
    [{}],
  ])('no request without both the rule and the test username (%j)', (data) => {
    mount(data);
    expect(previewIdentityTransform).not.toHaveBeenCalled();
    expect(screen.queryByRole('status', { name: 'Anteprima della trasformazione' })).toBeNull();
  });

  it('shows why the rule does not apply', async () => {
    previewIdentityTransform.mockResolvedValue({ success: false, value: null, message: 'la regola da\' un testo vuoto' });
    mount({ identity_transform: 'utente.str.slice(9)', preview_username: 'E0041' });
    await waitFor(() => expect(screen.getByText(/testo vuoto/)).toBeInTheDocument());
  });

  it('changing the kind drops the transformation', async () => {
    previewIdentityTransform.mockResolvedValue({ success: true, value: '004_IS', message: null });
    mount({ identity_transform: RULE, preview_username: 'E0041' });
    await screen.findByRole('status', { name: 'Anteprima della trasformazione' });
    fireEvent.change(screen.getByLabelText(/^Tipo/), { target: { value: 'text' } });
    expect(nodeData().input_kind).toBe('text');
    expect(nodeData().identity_transform).toBeNull();
    expect(nodeData().preview_username).toBeNull();
  });
});
