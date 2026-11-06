/**
 * UI-2 (06/11/2026, C16): scrivere in un nodo «Input di progetto» di tipo
 * connessione non rilancia GET /connection-sets: la chiave della risorsa non
 * contiene gli input.
 */
import { renderHook, waitFor } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';

const { loadSets } = vi.hoisted(() => ({ loadSets: vi.fn(async () => [{ id: 'cs1', name: 'Set', members: [] }]) }));
vi.mock('../../src/utils/connectionSets', () => ({ loadSharedConnectionSets: loadSets }));

import useRuntimeInputDefinitions from '../../src/hooks/useRuntimeInputDefinitions';

const inputNode = label => ({
  id: 'in', type: 'project_input',
  data: { input_kind: 'connection', input_key: 'conn', label, connection_set_id: 'cs1' },
});

describe('useRuntimeInputDefinitions: set di connessioni', () => {
  it('una richiesta anche scrivendo 5 caratteri nel nodo di input', async () => {
    const { result, rerender } = renderHook(({ nodes }) => useRuntimeInputDefinitions(nodes), { initialProps: { nodes: [inputNode('')] } });
    await waitFor(() => expect(loadSets).toHaveBeenCalledTimes(1));
    for (const label of ['C', 'Co', 'Con', 'Conn', 'Conne']) rerender({ nodes: [inputNode(label)] });
    await new Promise(r => setTimeout(r, 50));
    expect(loadSets).toHaveBeenCalledTimes(1);
    expect(result.current[0].label).toBe('Conne');
  });
});
