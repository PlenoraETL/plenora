// @vitest-environment node
/**
 * P31 — nessuna risorsa esterna a runtime, salvo le dichiarate.
 *
 * Ogni URL http(s) di index.html, public/theme-init.js e src (JS, JSX, CSS) e'
 * in questa tabella con il suo motivo; un URL nuovo fa fallire il test finche'
 * non viene dichiarato (e se e' una risorsa caricata dal browser, finche' la
 * CSP non lo ammette). Una voce che non compare piu' va tolta.
 */
import fs from 'node:fs';
import path from 'node:path';
import { describe, it, expect } from 'vitest';
import { TILE_URLS } from '../../src/config/mapTiles';
import { readSecurityHeaders } from '../../vite-plugins/securityHeaders.js';

const ROOT = path.resolve(__dirname, '../..');

/** host -> { kind, files } ; kind: tile (risorsa caricata, deve stare nella CSP) o testo/navigazione. */
const DECLARED = {
  // Unica risorsa esterna caricata a runtime: le tile della mappa (img-src).
  '{s}.basemaps.cartocdn.com': { kind: 'tile', files: ['src/config/mapTiles.js'] },
  '{s}.tile.openstreetmap.org': { kind: 'tile', files: ['src/config/mapTiles.js'] },
  'server.arcgisonline.com': { kind: 'tile', files: ['src/config/mapTiles.js'] },
  // Namespace XML degli SVG (attributo xmlns, anche dentro un data: URI): nessuna richiesta.
  'www.w3.org': { kind: 'namespace', files: ['src/components/common/PlenoraLogo.jsx', 'src/styles/ProjectList.css'] },
  // Esempi nei placeholder e negli aiuti dei campi: testo, mai richiesto.
  'analisi.re-val.it': { kind: 'example', files: ['src/components/arricchisci/OpenAPIImportModal.jsx'] },
  'api.example.com': { kind: 'example', files: ['src/components/api-conn/wizard-steps/Step1BasicInfo.jsx', 'src/components/arricchisci/OpenAPIImportModal.jsx', 'src/components/nodes/carica/RestDestinationSection.jsx'] },
  'auth.example.com': { kind: 'example', files: ['src/components/api-conn/wizard-steps/Step3Auth.jsx'] },
  'myapp.example.com': { kind: 'example', files: ['src/components/api-conn/wizard-steps/Step3Auth.jsx'] },
  'server.tld': { kind: 'example', files: ['src/components/api-conn/wizard-steps/Step3Auth.jsx'] },
  'service.test': { kind: 'example', files: ['src/components/api-conn/wizard-steps/Step1BasicInfo.jsx'] },
  'contoso.sharepoint.com': { kind: 'example', files: ['src/components/arricchisci/DataSourceConfigModal.jsx'] },
  'sharepoint.azienda.local': { kind: 'example', files: ['src/components/arricchisci/DataSourceConfigModal.jsx'] },
  'tenant.sharepoint.com': { kind: 'example', files: ['src/utils/dataSourceForm.js'] },
  'server.arcgis.com': { kind: 'example', files: ['src/components/panels/FeatureServicePanel.jsx'] },
  'server': { kind: 'example', files: ['src/pages/ConnectionManager.jsx'] },
  'www.arcgis.com': { kind: 'example', files: ['src/pages/ConnectionManager.jsx'] },
};

function walk(dir, out = []) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) walk(full, out);
    else if (/\.(jsx?|css|html)$/.test(entry.name)) out.push(full);
  }
  return out;
}

function scannedFiles() {
  return [path.join(ROOT, 'index.html'), path.join(ROOT, 'public', 'theme-init.js'), ...walk(path.join(ROOT, 'src'))]
    .map((f) => path.relative(ROOT, f).split(path.sep).join('/'));
}

function foundUrls() {
  const found = {};
  for (const file of scannedFiles()) {
    const text = fs.readFileSync(path.join(ROOT, file), 'utf-8');
    for (const m of text.matchAll(/https?:\/\/([A-Za-z0-9{}*_-][A-Za-z0-9.{}*_-]*)/g)) {
      (found[m[1]] ||= new Set()).add(file);
    }
  }
  return Object.fromEntries(Object.entries(found).map(([host, files]) => [host, [...files].sort()]));
}

describe('URL esterni del frontend (P31)', () => {
  it('ogni URL esterno e\' dichiarato, e ogni dichiarazione e\' ancora vera', () => {
    const declared = Object.fromEntries(Object.entries(DECLARED).map(([h, d]) => [h, [...d.files].sort()]));
    expect(foundUrls()).toEqual(declared);
  });

  it('index.html e i CSS non caricano nulla da fuori (font, fogli di stile, immagini)', () => {
    const html = fs.readFileSync(path.join(ROOT, 'index.html'), 'utf-8');
    expect(html).not.toMatch(/https?:\/\//);
    for (const file of scannedFiles().filter((f) => f.endsWith('.css'))) {
      const css = fs.readFileSync(path.join(ROOT, file), 'utf-8');
      expect({ file, imports: css.match(/@import\s+(url\()?\s*['"]?https?:/g) }).toEqual({ file, imports: null });
      expect({ file, urls: css.match(/url\(\s*['"]?https?:/g) }).toEqual({ file, urls: null });
    }
  });

  it('i font sono file dell\'app con licenza accanto', () => {
    const dir = path.join(ROOT, 'src/assets/fonts');
    const css = fs.readFileSync(path.join(dir, 'fonts.css'), 'utf-8');
    const families = [...css.matchAll(/font-family:\s*'([^']+)'/g)].map((m) => m[1]);
    expect(new Set(families)).toEqual(new Set(['Inter', 'Syne', 'DM Mono']));
    for (const m of css.matchAll(/url\('\.\/([^']+)'\)/g)) expect(fs.existsSync(path.join(dir, m[1]))).toBe(true);
    for (const lic of ['LICENSE-Inter.txt', 'LICENSE-Syne.txt', 'LICENSE-DMMono.txt']) {
      expect(fs.readFileSync(path.join(dir, lic), 'utf-8')).toMatch(/SIL Open Font License, Version 1\.1/);
    }
    expect(fs.readFileSync(path.join(ROOT, 'src/index.jsx'), 'utf-8')).toMatch(/import '\.\/assets\/fonts\/fonts\.css'/);
  });

  it('i server delle tile sono ammessi dalla CSP (img-src di FRONTEND_MAP_TILE_ORIGINS)', () => {
    const headers = readSecurityHeaders();
    const imgSrc = headers['Content-Security-Policy'].split(';').map((d) => d.trim().split(/\s+/))
      .find(([name]) => name === 'img-src').slice(1);
    const allowed = (host) => imgSrc.some((src) => {
      const m = /^https:\/\/(\*\.)?(.+)$/.exec(src);
      if (!m) return false;
      return m[1] ? host.endsWith(`.${m[2]}`) : host === m[2];
    });
    const tileHosts = Object.values(TILE_URLS).map((u) => new URL(u.replace('{s}', 'a')).host);
    expect(tileHosts.length).toBe(Object.keys(DECLARED).filter((h) => DECLARED[h].kind === 'tile').length);
    for (const host of tileHosts) expect({ host, allowed: allowed(host) }).toEqual({ host, allowed: true });
  });
});
