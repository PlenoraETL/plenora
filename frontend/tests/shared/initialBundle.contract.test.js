/**
 * Contract del chunk iniziale e dei caricamenti al bisogno (UI-4: V1, V2,
 * V3b, V3c, V13), per enumerazione sul grafo degli import statici di src.
 *
 *  1. Il chunk iniziale (chiusura statica di src/index.jsx: quello che si
 *     scarica gia' su /login e sulle pagine di sistema) non contiene
 *     nessuna libreria di INITIAL_FORBIDDEN ne' lo store del canvas.
 *  2. Le librerie di LAZY_ONLY si importano staticamente solo dal loro
 *     modulo proprietario, e quel modulo nessuno lo importa staticamente:
 *     si scarica solo con `import()` quando serve (Riordina, import di una
 *     specifica, errori dopo il primo render).
 *  3. Sentry parte dopo il primo render, quando il browser e' libero.
 *  4. I caricatori degli editor sono una tabella sola (App.jsx e il
 *     prefetch della dashboard); i font precaricati esistono.
 * Un import statico nuovo che riporta una libreria pesante nel chunk
 * iniziale fa fallire il test con la catena di import che ce la porta.
 */
import fs from 'node:fs';
import path from 'node:path';
import { describe, it, expect } from 'vitest';
import { packageOf, staticClosure, staticImports } from '../helpers/importGraph.js';
import { SRC, FRONTEND, sourceFiles, stripComments } from '../helpers/sourceScan.js';
import { PRELOADED_FONTS, fontPreloadTags } from '../../vite-plugins/fontPreload.js';
import { EDITOR_LOADERS } from '../../src/utils/editorChunks';

/** Mai nel chunk iniziale: libreria -> motivo. */
const INITIAL_FORBIDDEN = {
  '@xyflow/react': 'canvas degli editor (vendor-reactflow, ~60 kB gzip): solo nei chunk degli editor',
  zustand: 'arriva solo con lo store del canvas e delle colonne',
  '@dagrejs/dagre': 'serve solo a «Riordina»',
  'js-yaml': 'serve solo all\'import di una specifica OpenAPI in YAML',
  '@sentry/react': '~150 kB gzip: si scarica dopo il primo render',
  leaflet: 'mappa dell\'anteprima, nel suo chunk',
};
/** Moduli di src mai nel chunk iniziale: file -> motivo. */
const INITIAL_FORBIDDEN_FILES = {
  'store/useCanvasStore.js': 'lo store del canvas porta @xyflow (V1): il guard usa utils/leaveGuard',
  'components/canvas/autoLayout.js': 'dagre',
};
/** Librerie caricate solo con import(): libreria -> moduli che possono importarla staticamente. */
const LAZY_ONLY = {
  '@dagrejs/dagre': ['components/canvas/autoLayout.js'],
  'js-yaml': [],
  '@sentry/react': [],
};

const FILES = sourceFiles(/\.jsx?$/);

describe('chunk iniziale: niente librerie pesanti', () => {
  const { files, packages } = staticClosure('index.jsx');

  it('il grafo e\' letto davvero (ingresso, App, login)', () => {
    expect(files.has('App.jsx')).toBe(true);
    expect(files.has('pages/LoginPage.jsx')).toBe(true);
    expect(packages.has('react-router-dom')).toBe(true);
  });

  it.each(Object.entries(INITIAL_FORBIDDEN))('%s non e\' nel chunk iniziale (%s)', (pkg) => {
    expect({ pkg, chain: packages.get(pkg)?.join(' > ') ?? null }).toEqual({ pkg, chain: null });
  });

  it.each(Object.entries(INITIAL_FORBIDDEN_FILES))('%s non e\' nel chunk iniziale (%s)', (file) => {
    expect({ file, chain: files.get(file)?.join(' > ') ?? null }).toEqual({ file, chain: null });
  });
});

describe('librerie caricate al bisogno', () => {
  const importersOf = (target) => FILES
    .filter(f => staticImports(f.code).some(spec => (spec.startsWith('.')
      ? path.resolve(path.dirname(f.abs), spec).replace(/\.(jsx?)$/, '') === path.join(SRC, target).replace(/\.(jsx?)$/, '')
      : false)))
    .map(f => f.file);

  it.each(Object.entries(LAZY_ONLY))('%s: import statico solo dal modulo proprietario, che si carica con import()', (pkg, owners) => {
    const direct = FILES.filter(f => staticImports(f.code).some(s => !s.startsWith('.') && packageOf(s) === pkg)).map(f => f.file);
    expect(direct.filter(f => !owners.includes(f))).toEqual([]);
    for (const owner of owners) expect({ owner, staticImporters: importersOf(owner) }).toEqual({ owner, staticImporters: [] });
    // Il pacchetto e' davvero usato (con import()) da qualche parte.
    const dynamic = FILES.filter(f => new RegExp(`import\\(\\s*['"]${pkg.replace('/', '\\/')}['"]`).test(stripComments(f.code))
      || owners.some(o => new RegExp(`import\\(\\s*['"][./]+[\\w/]*${path.basename(o, '.js')}['"]`).test(stripComments(f.code))));
    expect(dynamic.length).toBeGreaterThan(0);
  });

  it('Sentry si avvia dopo il primo render, quando il browser e\' libero', () => {
    const code = stripComments(fs.readFileSync(path.join(SRC, 'index.jsx'), 'utf-8'));
    const render = code.indexOf('.render(<App />)');
    const start = code.indexOf('whenIdle(startErrorTracking)');
    expect(render).toBeGreaterThan(0);
    expect(start).toBeGreaterThan(render);
    expect(code.match(/import\('@sentry\/react'\)/g)).toHaveLength(1);
    expect(code).toMatch(/function startErrorTracking\(\)\s*\{\s*import\('@sentry\/react'\)/);
  });
});

describe('editor precaricati e font', () => {
  it('ogni editor si carica dalla tabella unica (App.jsx e prefetch della dashboard)', () => {
    const app = stripComments(fs.readFileSync(path.join(SRC, 'App.jsx'), 'utf-8'));
    for (const type of Object.keys(EDITOR_LOADERS)) expect(app).toContain(`React.lazy(EDITOR_LOADERS.${type})`);
    // Nessun altro import() degli editor fuori dalla tabella.
    const others = FILES.filter(f => f.file !== 'utils/editorChunks.js')
      .filter(f => /import\(\s*['"][./]+(?:pages\/)?(?:Estrai|Manipola|Arricchisci)Editor['"]/.test(stripComments(f.code)))
      .map(f => f.file);
    expect(others).toEqual([]);
    const card = fs.readFileSync(path.join(SRC, 'components/projects/ProjectCard.jsx'), 'utf-8');
    expect(card).toMatch(/onMouseEnter=\{\(\) => prefetchProjectEditor\(project\.project_type\)\}/);
    expect(card).toMatch(/onFocus=\{\(\) => prefetchProjectEditor\(project\.project_type\)\}/);
  });

  it('i font precaricati esistono e il plugin e\' nella configurazione', () => {
    // Nomi come nella build: con l'hash di Vite.
    const fonts = fs.readdirSync(path.join(SRC, 'assets/fonts')).map(f => `assets/${f.replace(/\.woff2$/,'-AbC123.woff2')}`);
    const tags = fontPreloadTags(fonts);
    expect(tags).toHaveLength(PRELOADED_FONTS.length);
    for (const t of tags) {
      expect(t.attrs).toMatchObject({ rel: 'preload', as: 'font', type: 'font/woff2', crossorigin: '' });
      // Stesso origin: un percorso, mai un'origine (CSP font-src 'self').
      expect(t.attrs.href).toMatch(/^\/assets\/[\w.-]+\.woff2$/);
    }
    expect(() => fontPreloadTags(['assets/altro.woff2'])).toThrow(/non trovato/);
    expect(fs.readFileSync(path.join(FRONTEND, 'vite.config.js'), 'utf-8')).toMatch(/plugins: \[[^\]]*fontPreload\(\)/);
  });
});
