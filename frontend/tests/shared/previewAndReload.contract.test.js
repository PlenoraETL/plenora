import fs from 'node:fs';
import path from 'node:path';
import { describe, it, expect, vi } from 'vitest';
import { formatNumber, formatPreviewValue, PREVIEW_ROW_LIMIT } from '../../src/utils/previewFormat';
import { installChunkReload } from '../../src/utils/chunkReload';

/** Contract: formato delle anteprime e ripresa dopo un deploy. */
describe('preview format', () => {
  it('rounds binary decimals, leaves integers and text alone', () => {
    expect(formatNumber(58.333333333336)).toBe('58,3333');
    expect(formatNumber(2026)).toBe('2026');
    expect(formatNumber(1234567)).toBe('1234567');
    expect(formatPreviewValue('092_01283')).toBe('092_01283');
    expect(formatPreviewValue(null)).toBe('—');
    expect(formatPreviewValue({ a: 1 })).toBe('{"a":1}');
  });

  it('step and modal previews ask for the same rows', () => {
    const src = (p) => fs.readFileSync(path.resolve(import.meta.dirname, '../../src', p), 'utf-8');
    expect(src('components/canvas/StepPreview.jsx')).toMatch(/run\(PREVIEW_ROW_LIMIT/);
    expect(src('hooks/usePreview.js')).toMatch(/limit = PREVIEW_ROW_LIMIT/);
    expect(PREVIEW_ROW_LIMIT).toBe(100);
  });
});

function fakeWindow() {
  const listeners = {};
  const store = {};
  return {
    listeners,
    addEventListener: (t, fn) => { listeners[t] = fn; },
    removeEventListener: (t) => { delete listeners[t]; },
    sessionStorage: { getItem: k => store[k] ?? null, setItem: (k, v) => { store[k] = v; } },
    location: { reload: vi.fn() },
  };
}

describe('chunk reload after a deploy', () => {
  it('reloads once on a failed dynamic import, then lets the error through', () => {
    const win = fakeWindow();
    installChunkReload(win);
    const first = { preventDefault: vi.fn() };
    win.listeners['vite:preloadError'](first);
    expect(first.preventDefault).toHaveBeenCalled();
    expect(win.location.reload).toHaveBeenCalledTimes(1);

    const again = { preventDefault: vi.fn() };
    win.listeners['vite:preloadError'](again);
    expect(again.preventDefault).not.toHaveBeenCalled();
    expect(win.location.reload).toHaveBeenCalledTimes(1);
  });

  it('never reloads when the "already reloaded" mark cannot be stored (no loop)', () => {
    const win = fakeWindow();
    win.sessionStorage = { getItem: () => { throw new Error('blocked'); }, setItem: () => { throw new Error('blocked'); } };
    installChunkReload(win);
    for (let i = 0; i < 3; i += 1) {
      const ev = { preventDefault: vi.fn() };
      win.listeners['vite:preloadError'](ev);
      expect(ev.preventDefault).not.toHaveBeenCalled();
    }
    expect(win.location.reload).not.toHaveBeenCalled();
  });

  it('is installed at startup', () => {
    const src = fs.readFileSync(path.resolve(import.meta.dirname, '../../src/index.jsx'), 'utf-8');
    expect(src).toMatch(/installChunkReload\(\)/);
  });
});
