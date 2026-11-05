import fs from 'node:fs';
import path from 'node:path';
import { describe, it, expect } from 'vitest';
import { serializeCanvas } from '../../src/utils/canvasSerializer';
import { persistedNodeData } from '../../src/utils/nodeTypeRegistry';
import { isSecretHeader } from '../../src/utils/restSecrets';

/**
 * Contract R10.8 (Fase 5 T1).
 *  - I segreti REST non si salvano nel canvas: le credenziali sono un
 *    riferimento al connettore API (`rest_auth_api_id`); un'intestazione
 *    segreta o con nome di credenziale non passa dai canali di
 *    persistenza (salvataggio/versioni = persistedNodeData, copia locale =
 *    useLocalDraft).
 *  - I devtools degli store sono spenti in produzione.
 */

const srcRoot = path.resolve(import.meta.dirname, '../../src');
const read = (rel) => fs.readFileSync(path.join(srcRoot, rel), 'utf-8');

function collectSources(dir) {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const fullPath = path.join(dir, entry.name);
    if (entry.isDirectory()) return collectSources(fullPath);
    return /\.(jsx?|tsx?)$/.test(entry.name) ? [fullPath] : [];
  });
}

const caricaNode = {
  id: 'c1',
  type: 'carica',
  position: { x: 0, y: 0 },
  data: {
    dest_type: 'rest',
    rest_url: 'https://api.example.com/items',
    rest_auth_api_id: 'api-1',
    rest_headers: [
      { name: 'X-Legacy', value: 'shh', secret: true },
      { name: 'Authorization', value: 'Bearer abc' },
      { name: 'x-api-key', value: 'k' },
      { name: 'X-Client', value: 'plenora' },
    ],
  },
};

describe('segreti REST mai nel canvas', () => {
  it('il salvataggio conserva il riferimento al connettore e toglie le credenziali', () => {
    const data = persistedNodeData(caricaNode);
    expect(data.rest_auth_api_id).toBe('api-1');
    expect(data.rest_headers).toEqual([{ name: 'X-Client', value: 'plenora' }]);

    const json = JSON.stringify(serializeCanvas([caricaNode], [], { mode: 'manipola' }));
    for (const secret of ['shh', 'Bearer abc', '"k"']) expect(json).not.toContain(secret);
  });

  it('riconosce le intestazioni con nome di credenziale', () => {
    for (const name of ['Authorization', 'Proxy-Authorization', 'Cookie', 'X-API-Key', 'api_key', 'X-Auth-Token', 'Client-Secret']) {
      expect(isSecretHeader({ name, value: 'x' })).toBe(true);
    }
    for (const name of ['X-Client', 'Content-Type', 'Accept']) expect(isSecretHeader({ name, value: 'x' })).toBe(false);
  });

  it('anche la copia locale (useLocalDraft) passa dalla stessa regola', () => {
    expect(read('hooks/useLocalDraft.js')).toMatch(/persistableRestHeaders\(/);
  });

  it('nessun componente marca un\'intestazione come segreta (niente "Segreto" nel canvas)', () => {
    const offenders = collectSources(path.join(srcRoot, 'components'))
      .filter((file) => /\bsecret\s*:\s*(true|false)|'secret'/.test(fs.readFileSync(file, 'utf-8')))
      .map((file) => path.relative(srcRoot, file));
    expect(offenders).toEqual([]);
  });
});

describe('devtools solo in sviluppo', () => {
  it('ogni devtools(...) di zustand e\' spento in produzione', () => {
    const offenders = [];
    for (const file of collectSources(srcRoot)) {
      const content = fs.readFileSync(file, 'utf-8');
      if (!/\bdevtools\s*\(/.test(content)) continue;
      // FRONTEND_ENV.isDev = import.meta.env.DEV (config/runtimeEnv, unico confine delle env).
      if (!/enabled:\s*FRONTEND_ENV\.isDev\b/.test(content)) offenders.push(path.relative(srcRoot, file));
    }
    expect(offenders).toEqual([]);
  });

  it('nessun altro devtools di librerie di stato (react-query non e\' una dipendenza)', () => {
    const pkg = JSON.parse(fs.readFileSync(path.resolve(srcRoot, '../package.json'), 'utf-8'));
    const deps = { ...pkg.dependencies };
    expect(Object.keys(deps).filter((name) => /react-query|redux-devtools|devtools/.test(name))).toEqual([]);
  });
});
