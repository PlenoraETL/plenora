/**
 * Blocco A, regola 4 (F2): la mappa delle colonne porta la FIRMA del canvas
 * da cui e' stata calcolata, e il salvataggio (Salva, Esegui, Valida e le
 * anteprime passano tutti da handleSaveQuiet) allega le colonne derivate
 * SOLO se la firma della mappa e' quella del canvas che salva. Altrimenti
 * chiede subito la mappa per quella firma e la attende (tetto 10 s); se non
 * arriva o fallisce, non allega niente (il backend le ricalcola). Mai
 * colonne di una firma diversa.
 *
 * Enumerazione dei momenti: prima della prima risposta, durante una
 * richiesta (stessa firma e firma vecchia), subito dopo una modifica, dopo
 * un errore, dopo un ripristino dalla copia di sicurezza, dopo un Annulla,
 * richiesta che non arriva.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const { updateCanvasMock } = vi.hoisted(() => ({ updateCanvasMock: vi.fn() }));
vi.mock('../../src/api', () => ({ updateCanvas: updateCanvasMock, inferProjectSchemas: vi.fn() }));

import useCanvasStore from '../../src/store/useCanvasStore';
import useColumnStore, { SAVE_COLUMNS_TIMEOUT_MS, startColumnSync } from '../../src/store/useColumnStore';
import { columnSignature } from '../../src/utils/columnSource';

const NODES = [
  { id: 's', type: 'file_source', position: { x: 0, y: 0 }, data: { columns: [{ name: 'a' }] } },
  { id: 't', type: 'transform', position: { x: 0, y: 0 }, data: { transform_op: 'filter', config: { conditions: [] } } },
];
const EDGES = [{ id: 'x', source: 's', target: 't', targetHandle: 'input-0' }];

// Risposta "marcata" con il nome della firma: il test vede quale mappa e' finita nel salvataggio.
const response = tag => ({ schemas: { t: [{ id: `id_${tag}`, name: tag, type: 'str', source_name: tag }] }, schema_states: {} });
const savedColumnsOfT = () => {
  const payload = updateCanvasMock.mock.calls.at(-1)[1];
  return payload.nodes.find(n => n.id === 't').columns;
};
const keyNow = () => columnSignature(useCanvasStore.getState().nodes, useCanvasStore.getState().edges);
const edit = (value) => useCanvasStore.getState().updateNodeData('t', { config: { conditions: [], note: value } });

// Richieste controllate a mano: ogni chiamata resta in sospeso finche' il test non decide.
function controlledFetch() {
  const calls = [];
  const fetchSchemas = vi.fn((projectId, canvas) => new Promise((resolve, reject) => {
    calls.push({ canvas, resolve, reject });
  }));
  return { fetchSchemas, calls };
}

let stop = null;
const start = (fetchSchemas) => {
  stop = startColumnSync({ projectId: 'p1', canvasStore: useCanvasStore, fetchSchemas, delay: 250 });
};
const load = () => {
  const st = useCanvasStore.getState();
  st.init('p1', 'manipola');
  st.loadGraph(NODES, EDGES);
  // Canvas diverso dall'ultimo salvato (regola unica di store/saveState):
  // senza differenza il salvataggio non parte.
  useCanvasStore.setState({ isDirty: true, savedGraph: { nodes: [], edges: [] } });
};

beforeEach(() => {
  vi.useFakeTimers();
  updateCanvasMock.mockReset();
  updateCanvasMock.mockResolvedValue({ updated_at: 'now' });
  useCanvasStore.getState().reset();
});
afterEach(() => {
  stop?.();
  stop = null;
  vi.useRealTimers();
});

describe('salvataggio: colonne derivate solo della firma del canvas salvato', () => {
  it('prima della prima risposta: attende la richiesta gia\' in volo (una sola) e allega quella', async () => {
    const { fetchSchemas, calls } = controlledFetch();
    load();
    start(fetchSchemas);
    await vi.advanceTimersByTimeAsync(0);
    expect(calls).toHaveLength(1);
    const saving = useCanvasStore.getState().handleSaveQuiet();
    await vi.advanceTimersByTimeAsync(0);
    expect(updateCanvasMock).not.toHaveBeenCalled();
    calls[0].resolve(response('k1'));
    await saving;
    expect(fetchSchemas).toHaveBeenCalledTimes(1);
    expect(savedColumnsOfT()).toEqual([expect.objectContaining({ id: 'id_k1', name: 'k1' })]);
  });

  it('durante una richiesta per una firma VECCHIA: chiede la firma corrente e non allega mai la vecchia', async () => {
    const { fetchSchemas, calls } = controlledFetch();
    load();
    start(fetchSchemas);
    await vi.advanceTimersByTimeAsync(0);
    edit('k2');
    const saving = useCanvasStore.getState().handleSaveQuiet();
    await vi.advanceTimersByTimeAsync(0);
    expect(calls).toHaveLength(2);
    // La seconda richiesta e' per il canvas corrente (serializzato come lo legge il server).
    expect(JSON.stringify(calls[1].canvas)).toContain('"note":"k2"');
    expect(JSON.stringify(calls[0].canvas)).not.toContain('k2');
    calls[0].resolve(response('vecchia'));
    await vi.advanceTimersByTimeAsync(0);
    expect(updateCanvasMock).not.toHaveBeenCalled();
    calls[1].resolve(response('k2'));
    await saving;
    expect(savedColumnsOfT()).toEqual([expect.objectContaining({ id: 'id_k2' })]);
    // La mappa nello store porta la firma corrente.
    expect(useColumnStore.getState().key).toBe(keyNow());
  });

  it('subito dopo una modifica (entro il ritardo): niente mappa vecchia, si chiede e si allega la nuova', async () => {
    const fetchSchemas = vi.fn()
      .mockResolvedValueOnce(response('k1'))
      .mockResolvedValueOnce(response('k2'));
    load();
    start(fetchSchemas);
    await vi.advanceTimersByTimeAsync(0);
    expect(useColumnStore.getState().schemas.t[0].name).toBe('k1');
    edit('k2');
    await useCanvasStore.getState().handleSaveQuiet();
    expect(savedColumnsOfT()).toEqual([expect.objectContaining({ id: 'id_k2' })]);
    // Il ritardo poi non richiede di nuovo: la mappa e' gia' di questa firma.
    await vi.advanceTimersByTimeAsync(300);
    expect(fetchSchemas).toHaveBeenCalledTimes(2);
  });

  it('dopo un errore: richiede; se fallisce ancora, salva SENZA colonne derivate', async () => {
    const boom = Object.assign(new Error('boom'), { response: { status: 500, data: {} } });
    const fetchSchemas = vi.fn().mockRejectedValue(boom);
    load();
    start(fetchSchemas);
    await vi.advanceTimersByTimeAsync(0);
    expect(useColumnStore.getState().status).toBe('error');
    await useCanvasStore.getState().handleSaveQuiet();
    expect(fetchSchemas).toHaveBeenCalledTimes(2);
    expect(savedColumnsOfT()).toBeUndefined();
  });

  it('dopo un ripristino dalla copia di sicurezza: la mappa del canvas di prima non entra', async () => {
    const fetchSchemas = vi.fn()
      .mockResolvedValueOnce(response('prima'))
      .mockResolvedValueOnce(response('copia'));
    load();
    start(fetchSchemas);
    await vi.advanceTimersByTimeAsync(0);
    const copy = NODES.map(n => (n.id === 't' ? { ...n, data: { ...n.data, config: { conditions: [], note: 'copia' } } } : n));
    useCanvasStore.getState().loadUnsavedGraph(copy, EDGES);
    await useCanvasStore.getState().handleSaveQuiet();
    expect(savedColumnsOfT()).toEqual([expect.objectContaining({ id: 'id_copia' })]);
  });

  it('dopo un Annulla che torna alla firma della mappa: allega subito, nessuna richiesta', async () => {
    const fetchSchemas = vi.fn().mockResolvedValueOnce(response('k1'));
    load();
    start(fetchSchemas);
    await vi.advanceTimersByTimeAsync(0);
    edit('k2');
    useCanvasStore.getState().undo();
    useCanvasStore.setState({ isDirty: true });
    await useCanvasStore.getState().handleSaveQuiet();
    expect(fetchSchemas).toHaveBeenCalledTimes(1);
    expect(savedColumnsOfT()).toEqual([expect.objectContaining({ id: 'id_k1' })]);
  });

  it('richiesta che non arriva: dopo il tetto salva SENZA colonne derivate', async () => {
    const { fetchSchemas } = controlledFetch();
    load();
    start(fetchSchemas);
    await vi.advanceTimersByTimeAsync(0);
    const saving = useCanvasStore.getState().handleSaveQuiet();
    await vi.advanceTimersByTimeAsync(SAVE_COLUMNS_TIMEOUT_MS - 1);
    expect(updateCanvasMock).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    await saving;
    expect(savedColumnsOfT()).toBeUndefined();
  });

  it('senza sincronizzazione per il progetto (fuori dall\'editor): salva subito, senza colonne derivate', async () => {
    load();
    await useCanvasStore.getState().handleSaveQuiet();
    expect(savedColumnsOfT()).toBeUndefined();
  });
});
