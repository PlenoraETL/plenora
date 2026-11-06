import fs from 'node:fs';
import path from 'node:path';
import { describe, it, expect, vi } from 'vitest';

import { downloadProgress, uploadProgress } from '../../src/utils/transferProgress';
import { formatSeconds } from '../../src/utils/format';

/**
 * UI-3 (06/11/2026), U17: avanzamento di ogni caricamento e scaricamento
 * di file, durate sempre da utils/format. Enumerazione su tutti i file.
 */
const SRC = path.resolve(import.meta.dirname, '../../src');
const walk = (dir) => fs.readdirSync(dir, { withFileTypes: true }).flatMap(e => {
  const p = path.join(dir, e.name);
  return e.isDirectory() ? walk(p) : (/\.jsx?$/.test(e.name) ? [p] : []);
});
const code = (t) => t.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:'"`])\/\/.*$/gm, '$1');
const rel = (f) => path.relative(SRC, f).split(path.sep).join('/');

/** Corpo di ogni funzione esportata di un modulo api (fino alla prossima). */
function exportedBodies(text) {
  const starts = [...text.matchAll(/^export (?:const|async function|function) (\w+)/gm)];
  return starts.map((m, i) => ({ name: m[1], body: text.slice(m.index, starts[i + 1]?.index ?? text.length) }));
}

describe('U17: every file transfer of src/api reports its progress', () => {
  const fns = walk(path.join(SRC, 'api')).flatMap(f => exportedBodies(code(fs.readFileSync(f, 'utf-8'))).map(fn => ({ file: rel(f), ...fn })));
  const uploads = fns.filter(fn => /new FormData\(/.test(fn.body));
  const downloads = fns.filter(fn => /responseType:\s*'blob'/.test(fn.body));

  it('finds the transfers (the enumeration is not empty)', () => {
    expect(uploads.length).toBeGreaterThanOrEqual(5);
    expect(downloads.length).toBeGreaterThanOrEqual(7);
  });

  it('each one accepts onProgress and passes it to axios', () => {
    const bad = [
      ...uploads.filter(fn => !/onProgress/.test(fn.body) || !/uploadProgress\(onProgress\)/.test(fn.body)),
      ...downloads.filter(fn => !/onProgress/.test(fn.body) || !/downloadProgress\(onProgress\)/.test(fn.body)),
    ].map(fn => `${fn.file}:${fn.name}`);
    expect(bad).toEqual([]);
  });

  it('the axios hooks give percent when the size is known, bytes only otherwise', () => {
    const seen = vi.fn();
    uploadProgress(seen).onUploadProgress({ loaded: 50, total: 200 });
    downloadProgress(seen).onDownloadProgress({ loaded: 10 });
    expect(seen.mock.calls).toEqual([[{ loaded: 50, total: 200, percent: 25 }], [{ loaded: 10, total: null, percent: null }]]);
    expect(uploadProgress(null)).toEqual({});
  });
});

describe('U17: durations only from utils/format', () => {
  it('no raw seconds («12.3s») and no local duration formatter outside utils/format', () => {
    const offenders = [];
    for (const f of walk(SRC)) {
      const r = rel(f);
      if (r === 'utils/format.js') continue;
      const src = code(fs.readFileSync(f, 'utf-8'));
      if (/toFixed\(\d\)\s*\}?\s*s\b|toFixed\(\d\)\s*\+\s*'s'|elapsed_time\}s/.test(src)) offenders.push(`${r}: secondi grezzi`);
      if (/function duration\(|const duration = \(seconds\) => \{/.test(src)) offenders.push(`${r}: formattatore locale`);
    }
    expect(offenders).toEqual([]);
    expect(formatSeconds(2.4)).toMatch(/^2[,.]4s$/);
    expect(formatSeconds(75)).toBe('1m 15s');
  });
});
