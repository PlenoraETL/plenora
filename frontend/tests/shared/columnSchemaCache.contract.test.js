/**
 * UI-2 (06/11/2026, C12, C13): richieste di /schemas.
 *
 *  - cache delle mappe per FIRMA (piu' di una): Annulla, «scrivi e
 *    cancella» e la riapertura dello stesso progetto non richiedono;
 *  - una mappa vecchia (oltre SCHEMA_FRESH_MS) si usa subito e si richiede
 *    in sottofondo;
 *  - una richiesta superata da una firma nuova si annulla (signal), salvo
 *    che un salvataggio la aspetti;
 *  - C13: lo spostamento verticale che non cambia l'ordine dei lati non
 *    richiede; quello che lo cambia si';
 *  - la fine sessione svuota la cache.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { createColumnStore, startColumnSync } from '../../src/store/useColumnStore';
import useColumnStore from '../../src/store/useColumnStore';
import { createCanvasStore } from '../../src/store/useCanvasStore';
import { resetSession } from '../../src/utils/session';

const response = tag => ({ schemas: { e: [{ id: `i_${tag}`, name: tag, type: 'str', source_name: tag }] }, schema_states: {} });
const tagOf = (canvas) => canvas.nodes.find(n => n.id === 'e')?.output_mapping?.[0]?.column || 'base';

function setup({ columnStore = createColumnStore(), now } = {}) {
  const canvasStore = createCanvasStore();
  canvasStore.getState().init('p1', 'manipola');
  const fetchSchemas = vi.fn(async (pid, canvas) => response(tagOf(canvas)));
  const stop = startColumnSync({ projectId: 'p1', canvasStore, columnStore, fetchSchemas, delay: 200, ...(now ? { now } : {}) });
  return { canvasStore, columnStore, fetchSchemas, stop };
}
const load = (canvasStore, edges = [{ id: 'x', source: 's', target: 'e', targetHandle: 'input-0' }]) => canvasStore.getState().loadGraph(
  [{ id: 's', type: 'file_source', position: { x: 0, y: 0 }, data: { columns: [{ name: 'a' }] } },
    { id: 's2', type: 'file_source', position: { x: 0, y: 200 }, data: { columns: [{ name: 'b' }] } },
    { id: 'e', type: 'api_enrich', position: { x: 300, y: 0 }, data: { output_mapping: [] } }],
  edges,
);
const map = (canvasStore, column) => canvasStore.getState().updateNodeData('e', { output_mapping: [{ path: 'x', column }] });

afterEach(() => vi.useRealTimers());

describe('cache delle mappe per firma', () => {
  it('k1 -> k2 -> Annulla -> Ripeti: due richieste in tutto, le mappe tornano subito', async () => {
    vi.useFakeTimers();
    const { canvasStore, columnStore, fetchSchemas, stop } = setup();
    load(canvasStore);
    await vi.advanceTimersByTimeAsync(0);
    map(canvasStore, 'k2');
    await vi.advanceTimersByTimeAsync(250);
    expect(fetchSchemas).toHaveBeenCalledTimes(2);
    expect(columnStore.getState().schemas.e[0].name).toBe('k2');
    canvasStore.getState().undo();
    await vi.advanceTimersByTimeAsync(250);
    expect(columnStore.getState().schemas.e[0].name).toBe('base');
    canvasStore.getState().redo();
    await vi.advanceTimersByTimeAsync(250);
    expect(columnStore.getState().schemas.e[0].name).toBe('k2');
    expect(fetchSchemas).toHaveBeenCalledTimes(2);
    stop();
  });

  it('riaprire lo stesso progetto (nuova sincronizzazione, stesso store): nessuna richiesta', async () => {
    vi.useFakeTimers();
    const columnStore = createColumnStore();
    const first = setup({ columnStore });
    load(first.canvasStore);
    await vi.advanceTimersByTimeAsync(0);
    first.stop();
    const again = setup({ columnStore });
    load(again.canvasStore);
    await vi.advanceTimersByTimeAsync(0);
    expect(again.fetchSchemas).not.toHaveBeenCalled();
    expect(columnStore.getState()).toMatchObject({ status: 'ok' });
    expect(columnStore.getState().schemas.e[0].name).toBe('base');
    again.stop();
  });

  it('una mappa vecchia si usa subito e si richiede in sottofondo', async () => {
    vi.useFakeTimers();
    const columnStore = createColumnStore();
    let t = 0;
    const first = setup({ columnStore, now: () => t });
    load(first.canvasStore);
    await vi.advanceTimersByTimeAsync(0);
    first.stop();
    t = 10 * 60_000;
    const again = setup({ columnStore, now: () => t });
    load(again.canvasStore);
    await vi.advanceTimersByTimeAsync(0);
    expect(columnStore.getState().status).toBe('ok');
    expect(again.fetchSchemas).toHaveBeenCalledTimes(1);
    again.stop();
  });

  it('la fine sessione svuota la cache dello store dell\'app', async () => {
    vi.useFakeTimers();
    const first = setup({ columnStore: useColumnStore });
    load(first.canvasStore);
    await vi.advanceTimersByTimeAsync(0);
    first.stop();
    resetSession();
    const again = setup({ columnStore: useColumnStore });
    again.canvasStore.getState().setReadOnly(false);
    load(again.canvasStore);
    await vi.advanceTimersByTimeAsync(0);
    expect(again.fetchSchemas).toHaveBeenCalledTimes(1);
    again.stop();
  });
});

describe('richieste superate', () => {
  it('una firma nuova annulla la richiesta in volo che nessuno aspetta', async () => {
    vi.useFakeTimers();
    const signals = [];
    const canvasStore = createCanvasStore();
    canvasStore.getState().init('p1', 'manipola');
    const fetchSchemas = vi.fn((pid, canvas, opts) => { signals.push(opts?.signal); return new Promise(() => {}); });
    const stop = startColumnSync({ projectId: 'p1', canvasStore, columnStore: createColumnStore(), fetchSchemas, delay: 200 });
    load(canvasStore);
    await vi.advanceTimersByTimeAsync(0);
    map(canvasStore, 'k2');
    await vi.advanceTimersByTimeAsync(250);
    expect(fetchSchemas).toHaveBeenCalledTimes(2);
    expect(signals[0].aborted).toBe(true);
    expect(signals[1].aborted).toBe(false);
    stop();
    expect(signals[1].aborted).toBe(true);
  });

  it('una richiesta che un salvataggio aspetta non si annulla', async () => {
    vi.useFakeTimers();
    const signals = [];
    const columnStore = createColumnStore();
    const canvasStore = createCanvasStore();
    canvasStore.getState().init('p1', 'manipola');
    const fetchSchemas = vi.fn((pid, canvas, opts) => { signals.push(opts?.signal); return new Promise(() => {}); });
    const stop = startColumnSync({ projectId: 'p1', canvasStore, columnStore, fetchSchemas, delay: 200 });
    load(canvasStore);
    await vi.advanceTimersByTimeAsync(0);
    const { nodes, edges } = canvasStore.getState();
    columnStore.getState().ensure({ nodes, edges, mode: 'manipola' });
    map(canvasStore, 'k2');
    await vi.advanceTimersByTimeAsync(250);
    expect(signals[0].aborted).toBe(false);
    stop();
  });
});

describe('C13: spostamenti verticali', () => {
  const loose = [{ id: 'x', source: 's', target: 'e' }, { id: 'y', source: 's2', target: 'e' }];
  const moveY = (canvasStore, id, y) => canvasStore.getState().onNodesChange([{ id, type: 'position', position: { x: 0, y } }]);

  it('senza scavalcare: nessuna richiesta; scavalcando (ordine dei lati diverso): una', async () => {
    vi.useFakeTimers();
    const { canvasStore, fetchSchemas, stop } = setup();
    load(canvasStore, loose);
    await vi.advanceTimersByTimeAsync(0);
    expect(fetchSchemas).toHaveBeenCalledTimes(1);
    for (let y = 1; y < 150; y += 10) moveY(canvasStore, 's', y);
    await vi.advanceTimersByTimeAsync(250);
    expect(fetchSchemas).toHaveBeenCalledTimes(1);
    moveY(canvasStore, 's', 400);
    await vi.advanceTimersByTimeAsync(250);
    expect(fetchSchemas).toHaveBeenCalledTimes(2);
    stop();
  });
});
