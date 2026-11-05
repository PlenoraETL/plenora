// @vitest-environment node
/**
 * P31 — le risposte dei server di Vite portano le intestazioni di sicurezza.
 *
 * Server veri (preview e sviluppo) su una pagina di prova: `vite preview`
 * serve le intestazioni di produzione (frontend/security-headers.nginx.conf,
 * generato dal backend) con la CSP bloccante; `vite` le serve con la CSP in
 * Report-Only e le sole aggiunte di sviluppo dichiarate nel plugin.
 */
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';
import { afterAll, beforeAll, describe, it, expect } from 'vitest';
import { createServer, preview } from 'vite';
import securityHeaders, {
  devSecurityHeaders, inlineScriptHashes, parseNginxHeaders, readSecurityHeaders,
} from '../../vite-plugins/securityHeaders.js';

const INLINE = 'window.__x = 1;';
let dir;

beforeAll(() => {
  dir = fs.mkdtempSync(path.join(os.tmpdir(), 'plenora-csp-'));
  fs.mkdirSync(path.join(dir, 'dist'));
  const html = `<!DOCTYPE html><html><head><script>${INLINE}</script></head><body>ok</body></html>`;
  fs.writeFileSync(path.join(dir, 'index.html'), html);
  fs.writeFileSync(path.join(dir, 'dist', 'index.html'), html);
});

afterAll(() => fs.rmSync(dir, { recursive: true, force: true }));

const directives = (csp) => Object.fromEntries(csp.split(';').map((d) => d.trim().split(/\s+/)).map(([n, ...s]) => [n, s]));

describe('intestazioni di sicurezza nei server di Vite (P31)', () => {
  it('il file letto e\' quello che nginx include: CSP bloccante senza unsafe-*', () => {
    const headers = readSecurityHeaders();
    expect(Object.keys(headers)).toEqual([
      'Content-Security-Policy', 'X-Content-Type-Options', 'Referrer-Policy', 'X-Frame-Options', 'Permissions-Policy',
    ]);
    expect(headers['Content-Security-Policy']).not.toMatch(/unsafe-/);
    expect(() => parseNginxHeaders('add_header X "y";')).toThrow(/riga non riconosciuta/);
  });

  it('vite preview: risposte con le intestazioni di produzione, identiche', async () => {
    const server = await preview({
      root: dir, configFile: false, logLevel: 'silent',
      plugins: [securityHeaders()],
      preview: { port: 0, host: '127.0.0.1' },
    });
    try {
      const base = server.resolvedUrls.local[0];
      for (const url of [base, `${base}qualunque/rotta`]) {
        const res = await fetch(url);
        expect(res.status).toBe(200);
        for (const [name, value] of Object.entries(readSecurityHeaders())) expect(res.headers.get(name)).toBe(value);
      }
    } finally {
      await server.close();
    }
  });

  it('vite (sviluppo): CSP in Report-Only con hash degli script inline e aggiunte dichiarate', async () => {
    const server = await createServer({
      root: dir, configFile: false, logLevel: 'silent',
      plugins: [securityHeaders({ apiUrl: 'http://localhost:5004' })],
      server: { port: 0, host: '127.0.0.1', hmr: false },
    });
    await server.listen();
    try {
      const res = await fetch(`${server.resolvedUrls.local[0]}`);
      const body = await res.text();
      expect(res.headers.get('Content-Security-Policy')).toBeNull();
      const csp = directives(res.headers.get('Content-Security-Policy-Report-Only'));
      const hash = `'sha256-${crypto.createHash('sha256').update(INLINE).digest('base64')}'`;
      expect(body).toContain(INLINE);
      expect(csp['script-src']).toEqual(["'self'", ...inlineScriptHashes(body)]);
      expect(csp['script-src']).toContain(hash);
      expect(csp['style-src']).toEqual(["'self'", "'unsafe-inline'"]);
      expect(csp['connect-src']).toEqual(["'self'", 'http://localhost:5004', 'ws:', 'wss:']);
      expect(res.headers.get('X-Content-Type-Options')).toBe('nosniff');
    } finally {
      await server.close();
    }
  });

  it('le aggiunte di sviluppo toccano solo script-src, style-src e connect-src', () => {
    const prod = directives(readSecurityHeaders()['Content-Security-Policy']);
    const dev = directives(devSecurityHeaders(readSecurityHeaders(), [])['Content-Security-Policy-Report-Only']);
    for (const name of Object.keys(prod)) {
      if (['script-src', 'style-src', 'connect-src'].includes(name)) continue;
      expect({ name, sources: dev[name] }).toEqual({ name, sources: prod[name] });
    }
  });
});
