/**
 * Sessione 9 Cluster A — frontend hooks unmount safety.
 *
 * Bug coperti:
 * - useExecution.startCaricaPolling: mountedRef guard pre-scheduling
 * - useImdfBundles.handleDeleteBundle: mountedRef guard post-await
 * - useCanvasCore: nessuna iscrizione a nodes/edges (UI-2, V4)
 */

import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));


function readSrc(relPath) {
  return readFileSync(resolve(__dirname, '../../src', relPath), 'utf-8');
}


describe('Sessione 9 cluster A hooks unmount safety', () => {
  // Fase 5 T2: i task di Carica usano il poller unico (utils/taskPoller: un
  // poll alla volta, stop allo smontaggio e a ogni nuovo avvio).
  it('useExecution: i task di Carica usano pollTask e si fermano allo smontaggio', () => {
    const src = readSrc('hooks/useExecution.js');
    const idx = src.indexOf('const startCaricaTasksPolling =');
    expect(idx).toBeGreaterThan(-1);
    const window = src.slice(idx, idx + 2500);
    expect(window).toContain('pollTask(task_id');
    expect(window).toContain('stopAllCaricaPolling()');
    expect(src).not.toContain('setInterval(');
    expect(src).toMatch(/caricaStops\.current\.forEach\(stop => stop\(\)\)/);
  });

  // UI-2 (V4): l'editor non e' piu' iscritto a nodes/edges (si ridisegnava a
  // ogni fotogramma di trascinamento); `dataNodes` (un derivato di `nodes`
  // per render) non esiste piu'. Il grafo lo disegna FlowCanvas.
  it('useCanvasCore non si iscrive a nodes/edges e non espone dataNodes', () => {
    const src = readSrc('hooks/useCanvasCore.js');
    expect(src).not.toContain('const dataNodes =');
    expect(src).not.toMatch(/useCanvasStore\(s => s\.(nodes|edges)\)/);
  });
});
