/**
 * Sessione 2 P0 — useCaricarica.handleExecute mountedRef guards.
 *
 * Bug originale: dopo l'aggiunta di `_waitTaskTerminal` (sessione
 * precedente), `handleExecute` faceva 3 await sequenziali (onBeforeExecute,
 * executeProjectQuery, _waitTaskTerminal fino a 90s) senza
 * `mountedRef.current` guard. Se il componente si smontava durante
 * questi await, i setIsExecuting/setResult/setProgress giravano su
 * componente unmounted: React warning + memory leak.
 *
 * Post-fix: guard `if (!mountedRef.current) return;` dopo ogni await
 * + guard nel catch + mountedRef nelle deps di useCallback.
 *
 * Strategia di test: lint architetturale sul source code. Il test
 * functional dell'unmount durante long-await richiede orchestrazione
 * di Promise+act che produce noise React, fuori scope per un contract
 * di regressione. Il lint garantisce che i guard restano nel codice.
 */

import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));


describe('useCaricarica mountedRef guards architectural lint (sessione 2 P0)', () => {
  const source = readFileSync(
    resolve(__dirname, '../../src/hooks/useCaricarica.js'),
    'utf-8',
  );

  it('handleExecute ha mountedRef guard dopo onBeforeExecute', () => {
    const onBeforeIdx = source.indexOf('if (onBeforeExecute)');
    expect(onBeforeIdx).toBeGreaterThan(-1);
    const window = source.slice(onBeforeIdx, onBeforeIdx + 800);
    expect(window).toMatch(/if\s*\(\s*!\s*mountedRef\.current\s*\)\s*return/);
  });

  it('handleExecute ha mountedRef guard dopo executeProjectQuery', () => {
    const idx = source.indexOf('executeProjectQuery(projectId)');
    expect(idx).toBeGreaterThan(-1);
    const window = source.slice(idx, idx + 200);
    expect(window).toMatch(/if\s*\(\s*!\s*mountedRef\.current\s*\)\s*return/);
  });

  it('handleExecute ha mountedRef guard dopo _waitTaskTerminal', () => {
    // H-5: la call oggi passa un options object ({isCancelled}) — il
    // pattern matcha l'apertura della call, non la chiusura.
    const idx = source.indexOf('_waitTaskTerminal(executeResp.task_id');
    expect(idx).toBeGreaterThan(-1);
    const window = source.slice(idx, idx + 600);
    expect(window).toMatch(/if\s*\(\s*!\s*mountedRef\.current\s*\)\s*return/);
  });

  it('handleExecute ha mountedRef guard nel catch', () => {
    const idx = source.indexOf('} catch (error) {');
    expect(idx).toBeGreaterThan(-1);
    const window = source.slice(idx, idx + 500);
    expect(window).toMatch(/if\s*\(\s*!\s*mountedRef\.current\s*\)\s*return/);
  });

  it('mountedRef e nelle deps di useCallback per handleExecute', () => {
    const idx = source.indexOf('[projectId, onBeforeExecute, getCanvas, startPolling');
    expect(idx).toBeGreaterThan(-1);
    const window = source.slice(idx, idx + 200);
    expect(window).toContain('mountedRef');
  });
});
