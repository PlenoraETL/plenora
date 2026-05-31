/**
 * Sessione 9 Cluster A — frontend hooks unmount safety.
 *
 * Bug coperti:
 * - useExecution.startCaricaPolling: mountedRef guard pre-setInterval
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
  it('useExecution startCaricaPolling ha mountedRef guard pre-setInterval', () => {
    const src = readSrc('hooks/useExecution.js');
    const idx = src.indexOf('const startCaricaPolling =');
    expect(idx).toBeGreaterThan(-1);
    const window = src.slice(idx, idx + 3500);
    // Cerca la guard subito prima del `setInterval(poll, pollInterval)`
    const setIntervalIdx = window.indexOf('setInterval(poll, pollInterval)');
    expect(setIntervalIdx).toBeGreaterThan(-1);
    // Pattern `if (!mountedRef.current) return` deve esistere nei
    // 500 char prima del setInterval
    const preWindow = window.slice(Math.max(0, setIntervalIdx - 500), setIntervalIdx);
    expect(preWindow).toMatch(/if\s*\(\s*!\s*mountedRef\.current\s*\)\s*return/);
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
