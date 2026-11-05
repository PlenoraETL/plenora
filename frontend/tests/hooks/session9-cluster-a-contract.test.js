/**
 * Sessione 9 Cluster A — frontend hooks unmount safety.
 *
 * Bug coperti:
 * - useExecution.startCaricaPolling: mountedRef guard pre-scheduling
 * - useImdfBundles.handleDeleteBundle: mountedRef guard post-await
 * - useCanvasCore.dataNodes: useMemo(filter, [nodes])
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

  it('useCanvasCore dataNodes usa useMemo con dep [nodes]', () => {
    const src = readSrc('hooks/useCanvasCore.js');
    const idx = src.indexOf('const dataNodes =');
    expect(idx).toBeGreaterThan(-1);
    const window = src.slice(idx, idx + 500);
    expect(window).toContain('useMemo');
    // dep array [nodes] presente
    expect(window).toMatch(/\[nodes\]/);
  });
});
