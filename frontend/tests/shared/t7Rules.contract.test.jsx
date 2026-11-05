/**
 * Fase 5 T7 — regole per costruzione su formati, CSS, codice morto, layout
 * delle pagine e accessibilità. Ogni blocco enumera TUTTO src/ (eccezioni
 * dichiarate, col motivo; una dichiarazione superata fa fallire il test).
 *
 *  1. Formati: un solo modulo (utils/format). Vietati fuori da lì
 *     toLocaleString/toLocaleDateString/toLocaleTimeString, Intl.DateTimeFormat
 *     e Intl.NumberFormat, divisioni per 1024 e testi «… KB/MB/GB», tagli
 *     dell'ISO (`.slice(11, …)`, `split('T')`). Orari del server = UTC anche
 *     senza fuso. Etichette degli enum = tabelle del backend.
 *  2. CSS: nessuna variabile usata e mai definita; nessuno sfondo chiaro
 *     fisso fuori dalle regole del tema chiaro; nessuna classe senza uso.
 *  3. Codice morto: nessun export inutilizzato (né da src né dai test) e
 *     nessun export usato solo dai test che non sia dichiarato.
 *  4. Pagine: PageLayout unico (sidebar, titolo, azioni, tema), niente
 *     «Indietro» nella storia, stati vuoti solo da EmptyState, azioni
 *     distruttive nel menu «⋯».
 *  5. Accessibilità: niente onClick su elementi non interattivi senza ruolo
 *     e tastiera; etichette legate ai campi in UserManager; MFA con focus.
 */
import fs from 'node:fs';
import path from 'node:path';
import React from 'react';
import { describe, it, expect, afterEach, chai } from 'vitest';
chai.config.truncateThreshold = 0;
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import { FRONTEND, sourceFiles, stripComments, lineOf } from '../helpers/sourceScan.js';
import { cssUsage } from '../helpers/cssUsage.js';
import { exportUsage } from '../helpers/exportUsage.js';
import { cssVariables } from '../helpers/sourceScan.js';
import {
  parseServerDate, formatDateTime, formatTime, formatBytes, formatNumber,
  formatDuration, formatElapsed, enumLabel, optionLabel, ENUM_LABELS,
} from '../../src/utils/format';
import ActionMenu from '../../src/components/common/ActionMenu';
import EmptyState from '../../src/components/common/EmptyState';

afterEach(() => cleanup());

const JS = sourceFiles(/\.(jsx?)$/).map(f => ({ ...f, clean: stripComments(f.code) }));
const CSS = sourceFiles(/\.css$/);
const hits = (re, files = JS, except = new Set()) => {
  const out = [];
  for (const { file, clean } of files) {
    if (except.has(file)) continue;
    for (const m of clean.matchAll(re)) out.push(`${file}:${lineOf(clean, m.index)}`);
  }
  return out;
};

// ------------------------------------------------------------ 1. formati ---
describe('formati: un solo modulo', () => {
  const ONLY = new Set(['utils/format.js']);
  it('niente toLocale*/Intl.*Format fuori da utils/format', () => {
    expect(hits(/\.toLocale(?:Date|Time)?String\s*\(|Intl\.(?:DateTimeFormat|NumberFormat|RelativeTimeFormat)\b/g, JS, ONLY)).toEqual([]);
  });
  it('niente formattatori di dimensione fuori da utils/format', () => {
    expect(hits(/\/\s*\(?\s*1024\b|1024\s*\*\*|\}\s*(?:KB|MB|GB)\b|['"`]\s+(?:KB|MB|GB)/g, JS, ONLY)).toEqual([]);
  });
  it('niente tagli a mano dell\'ISO (ora UTC mostrata come locale)', () => {
    expect(hits(/\.slice\(\s*11\s*,|split\(\s*['"]T['"]\s*\)/g)).toEqual([]);
  });

  it('istanti del server: senza fuso = UTC, mai ora locale; stamp degli schedule UTC', () => {
    const utc = Date.UTC(2026, 10, 5, 10, 30, 0);
    expect(parseServerDate('2026-11-05T10:30:00').getTime()).toBe(utc);
    expect(parseServerDate('2026-11-05 10:30:00').getTime()).toBe(utc);
    expect(parseServerDate('2026-11-05T10:30:00.123456').getTime()).toBe(utc + 123);
    expect(parseServerDate('2026-11-05T10:30:00Z').getTime()).toBe(utc);
    expect(parseServerDate('2026-11-05T11:30:00+01:00').getTime()).toBe(utc);
    expect(parseServerDate('20261105_103000').getTime()).toBe(utc);
    expect(parseServerDate(utc).getTime()).toBe(utc);
    // Un giorno del calendario resta quel giorno (mezzanotte locale).
    const d = parseServerDate('2026-11-05');
    expect([d.getFullYear(), d.getMonth(), d.getDate(), d.getHours()]).toEqual([2026, 10, 5, 0]);
    expect(parseServerDate('')).toBeNull();
    expect(parseServerDate('non una data')).toBeNull();
  });

  it('data e ora con anno, nel fuso del browser; illeggibile = testo com\'è', () => {
    const local = new Date(Date.UTC(2026, 10, 5, 10, 30, 15));
    expect(formatDateTime('2026-11-05T10:30:15')).toBe(formatDateTime(local));
    expect(formatDateTime('2026-11-05T10:30:15')).toMatch(/2026/);
    expect(formatDateTime('2026-11-05T10:30:15', { seconds: true })).toMatch(/15/);
    expect(formatDateTime('2026-11-05T10:30:15', { zone: true }).length).toBeGreaterThan(formatDateTime('2026-11-05T10:30:15').length);
    expect(formatTime(local)).toMatch(/^\d{2}:\d{2}$/);
    expect(formatDateTime(null)).toBe('—');
    expect(formatDateTime('domani')).toBe('domani');
  });

  it('dimensioni: un solo formatBytes, con GB e TB', () => {
    expect(formatBytes(512)).toBe('512 B');
    expect(formatBytes(1536)).toBe('1,5 KB');
    expect(formatBytes(5 * 1024 * 1024)).toBe('5 MB');
    expect(formatBytes(1.25 * 1024 ** 3)).toBe('1,25 GB');
    expect(formatBytes(2 * 1024 ** 4)).toBe('2 TB');
    expect(formatBytes(0)).toBe('0 B');
    expect(formatBytes(null)).toBe('—');
    expect(formatBytes(0.5, { from: 'MB' })).toBe('512 KB');
    expect(formatBytes(20, { from: 'GB' })).toBe('20 GB');
  });

  it('numeri, durate, tempo trascorso', () => {
    expect(formatNumber(1234567)).toBe('1.234.567');
    expect(formatNumber(2.345)).toBe('2,35');
    expect(formatNumber(undefined)).toBe('—');
    expect(formatDuration(850)).toBe('850ms');
    expect(formatDuration(2400)).toBe('2,4s');
    expect(formatDuration(192000)).toBe('3m 12s');
    const now = Date.UTC(2026, 10, 5, 10, 31, 5);
    expect(formatElapsed('2026-11-05T10:30:00', now)).toBe('1m 5s');
  });

  it('enum dei ticket: le tabelle sono quelle del backend, nessun codice grezzo', () => {
    const svc = fs.readFileSync(path.resolve(FRONTEND, '../backend/app/core/tickets/service.py'), 'utf-8');
    const tuple = (name) => [...svc.match(new RegExp(`${name}[^=]*=\\s*\\(([^)]*)\\)`))[1].matchAll(/"([^"]+)"/g)].map(m => m[1]);
    expect(Object.keys(ENUM_LABELS.ticketStatus).sort()).toEqual(tuple('TICKET_STATUSES').sort());
    expect(Object.keys(ENUM_LABELS.ticketSeverity).sort()).toEqual(tuple('TICKET_SEVERITIES').sort());
    expect(Object.keys(ENUM_LABELS.ticketKind).sort()).toEqual(tuple('TICKET_KINDS').sort());
    expect(enumLabel('ticketStatus', 'in_progress')).toBe('In lavorazione');
    expect(enumLabel('ticketStatus', 'nuovo_stato')).toBe('nuovo_stato');
    expect(optionLabel([{ value: 'ui', label: 'Interfaccia' }], 'ui')).toBe('Interfaccia');
    // Le pagine dei ticket non mostrano i valori grezzi del meta.
    const pages = JS.filter(f => /^(pages\/Ticket|components\/TicketCreateModal)/.test(f.file));
    expect(hits(/>\{(?:s|ticket\.(?:status|severity|category|kind)|t\.category)\}</g, pages)).toEqual([]);
  });

  it('ruoli: la tabella delle etichette copre USER_ROLES', () => {
    const um = JS.find(f => f.file === 'pages/UserManager.jsx').clean;
    const roles = [...um.matchAll(/\{ value: '([a-z]+)', option:/g)].map(m => m[1]).sort();
    expect(Object.keys(ENUM_LABELS.role).sort()).toEqual(roles);
  });
});

// ----------------------------------------------------------------- 2. CSS ---
describe('CSS', () => {
  it('nessuna variabile CSS usata e mai definita', () => {
    const { defined, used } = cssVariables(sourceFiles(/\.(jsx?|css)$/));
    const missing = [...used.entries()].filter(([v]) => !defined.has(v)).map(([v, files]) => `${v} (${[...files].join(', ')})`);
    expect(missing).toEqual([]);
  });

  // Sfondi chiari fissi: in tema scuro restano chiari (P20). Fuori dalle
  // regole `[data-theme='light']` si usano i token.
  const LIGHT_BG_OK = {
    'components/modals/ScheduleWizard.css': 'pomello dell\'interruttore (bianco su binario colorato, entrambi i temi)',
    'styles/ScheduleList.css': 'pomello dell\'interruttore (bianco su binario colorato, entrambi i temi)',
  };
  it('nessuno sfondo chiaro fisso fuori dal tema chiaro', () => {
    const lum = (h) => {
      let x = h.slice(1);
      if (x.length <= 4) x = x.split('').slice(0, 3).map(c => c + c).join('');
      const [r, g, b] = [0, 2, 4].map(i => parseInt(x.slice(i, i + 2), 16) / 255);
      return 0.2126 * r + 0.7152 * g + 0.0722 * b;
    };
    const VAR = /var\(--[\w-]+\s*,(?:[^()]|\([^()]*\))*\)/g;
    const out = [];
    for (const { file, code } of CSS) {
      const clean = code.replace(/\/\*[\s\S]*?\*\//g, '');
      for (const [, sel, body] of clean.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
        if (/data-theme=['"]?light/.test(sel)) continue;
        for (const m of body.matchAll(/background(?:-color)?\s*:\s*([^;]+)/g)) {
          const v = m[1].replace(VAR, '');
          const light = [...v.matchAll(/#[0-9a-fA-F]{3,8}\b/g)].some(c => lum(c[0]) > 0.8) || /(^|[\s,])white\b/.test(v);
          if (light && !LIGHT_BG_OK[file]) out.push(`${file}: ${sel.trim().slice(0, 60)}`);
        }
      }
    }
    expect(out).toEqual([]);
  });

  // Classi di librerie (DOM che non scriviamo noi).
  const LIB_PREFIXES = ['react-flow', 'leaflet'];
  it('nessuna classe CSS senza uso', () => {
    const { unused } = cssUsage();
    const dead = unused
      .filter(([name]) => !LIB_PREFIXES.some(p => name.startsWith(p)))
      .map(([name, locs]) => `${name} @ ${locs[0]}`);
    expect(dead).toEqual([]);
  });
});

// --------------------------------------------------------- 3. codice morto ---
describe('codice morto', () => {
  // Export che src non usa e che esistono per i test, col motivo.
  const TEST_ONLY = {
    'components/transforms/panels/index.js:PANEL_PARAMS': 'tabella dei parametri dei pannelli: il test la confronta col catalogo delle operazioni',
    'components/transforms/ParameterInput.jsx:HANDLED_PARAM_TYPES': 'tipi di parametro del modulo generico: il test la confronta col catalogo',
    'utils/dialogStack.js:__dialogStackSize': 'sonda della pila delle finestre per i test',
    'utils/escapeHtml.js:escapeHtml': 'l\'unica funzione ammessa dalla guardia dei sink HTML (htmlSinks.contract)',
    'utils/glossary.js:ALLOWED_ENGLISH': 'parole inglesi ammesse: lette dal test del glossario',
    'utils/glossary.js:NODE_TYPE_TERMS': 'tipo di nodo -> entità del glossario: letta dal test del glossario',
    'utils/permissions.js:CAPABILITY_BACKEND': 'capability -> regola del backend: letta dal test dei permessi',
    'utils/session.js:registeredSessionResets': 'sonda del registro delle cache di sessione',
    'utils/taskOutcome.js:TASK_PHASES': 'fasi dei task: enumerate dal test dell\'esito',
  };
  const usage = exportUsage();

  it('nessun export morto (usato solo dai test o da nessuno) se non dichiarato', () => {
    expect(usage.dead.filter(id => !TEST_ONLY[id])).toEqual([]);
  });
  it('nessuna dichiarazione superata', () => {
    expect(Object.keys(TEST_ONLY).filter(id => !usage.dead.includes(id))).toEqual([]);
  });
  it('nessun export che nessuno importa (né src né test)', () => {
    expect(usage.untested.filter(id => id !== 'utils/format.js:ENUM_LABELS')).toEqual([]);
  });

  it('dipendenze e build senza resti (D6): maplibre-gl, deck.gl, ImdfPreviewModal, nome del pacchetto', () => {
    const pkg = JSON.parse(fs.readFileSync(path.join(FRONTEND, 'package.json'), 'utf-8'));
    expect(pkg.name).toBe('plenora-frontend');
    expect(Object.keys(pkg.dependencies)).not.toContain('maplibre-gl');
    const vite = fs.readFileSync(path.join(FRONTEND, 'vite.config.js'), 'utf-8');
    expect(vite).not.toMatch(/deck\\?\.gl[^ ]*\)|vendor-deckgl|vendor-maplibre|ImdfPreviewModal/);
    expect(fs.existsSync(path.join(FRONTEND, 'src/utils/graphUtils.js'))).toBe(false);
    expect(fs.existsSync(path.join(FRONTEND, 'src/api/openapiClient.js'))).toBe(false);
  });

  it('mock MSW del login con la forma del backend (D6c): profilo in radice', () => {
    const h = JS.find(f => f.file === 'mocks/handlers.js').clean;
    expect(h).toMatch(/user_id: 'u-mock'/);
    expect(h).toMatch(/access_token: 'mock-token', token_type: 'bearer', \.\.\.MOCK_USER/);
  });
});

// ------------------------------------------------------------ 4. pagine ---
describe('pagine: layout unico (G1), vuoto unico (G2), distruttive a parte (G3)', () => {
  const app = JS.find(f => f.file === 'App.jsx').clean;
  const EDITORS_AND_SPECIAL = {
    EstraiEditor: 'editor (EditorShell)', ManipolaEditor: 'editor (EditorShell)', ArricchisciEditor: 'editor (EditorShell)',
    LoginPage: 'prima dell\'accesso, senza sidebar', OpenProjectPage: 'rimando a un editor',
    EsecutoreHome: 'kiosk dell\'esecutore: niente sidebar né pagine di sistema', NotFoundPage: 'pagina 404',
  };
  const routed = [...new Set([...app.matchAll(/element=\{[^}]*?<(?:\w+Route>\s*)?(?:<PerProject>\s*)?<(\w+)\s*\/>/g)].map(m => m[1]))]
    .filter(n => !/Route$/.test(n) && n !== 'Navigate' && n !== 'ToLogin');

  it('trova le pagine instradate', () => {
    expect(routed.length).toBeGreaterThan(15);
  });

  it('ogni pagina instradata (non editor) usa PageLayout e non la sidebar a mano', () => {
    const bad = [];
    for (const name of routed) {
      if (EDITORS_AND_SPECIAL[name]) continue;
      const f = JS.find(x => x.file === `pages/${name}.jsx`);
      if (!f) { bad.push(`${name}: file non trovato`); continue; }
      if (!/import PageLayout from/.test(f.clean)) bad.push(`${name}: senza PageLayout`);
      if (/<AppSidebar\b/.test(f.clean)) bad.push(`${name}: AppSidebar a mano`);
    }
    expect(bad).toEqual([]);
  });

  it('la sidebar la monta solo PageLayout, senza contatori della pagina (L12)', () => {
    expect(hits(/<AppSidebar\b/g).map(h => h.split(':')[0])).toEqual(['components/common/PageLayout.jsx']);
    expect(JS.find(f => f.file === 'components/AppSidebar.jsx').clean).toMatch(/export default function AppSidebar\(\)/);
  });

  it('niente «Indietro» nella storia né «← Dashboard» / «← Indietro» nelle pagine (S1, S8)', () => {
    expect(hits(/navigate\(\s*-1\s*\)|goBack\(|← Dashboard|>← Indietro</g, JS.filter(f => f.file.startsWith('pages/') && !f.file.startsWith('pages/FileManager/')))).toEqual([]);
  });

  it('stati vuoti solo da EmptyState (G2): niente __empty-title/-icon/-text fatti a mano', () => {
    const scope = JS.filter(f => /^(pages|components\/(projects|connections|logviewer))\//.test(f.file));
    expect(hits(/className=["'`][^"'`]*__empty-(?:title|icon|text)\b/g, scope)).toEqual([]);
  });

  it('EmptyState: titolo, testo e azione; compatto in riga', () => {
    render(<EmptyState icon="📂" title="Nessun file" text="Carica il primo" action={<button type="button">Carica</button>} />);
    expect(screen.getByRole('status')).toHaveTextContent('Nessun file');
    expect(screen.getByRole('button', { name: 'Carica' })).toBeInTheDocument();
  });

  it('ActionMenu: le distruttive dopo una linea, in rosso, solo aprendo il menu (G3)', () => {
    let removed = 0;
    render(<ActionMenu label="Azioni" items={[{ label: 'Rinomina', onSelect: () => {} }, null, { label: 'Elimina', danger: true, onSelect: () => { removed += 1; } }]} />);
    expect(screen.queryByRole('menuitem', { name: 'Elimina' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Azioni' }));
    const items = screen.getAllByRole('menuitem');
    expect(items.map(i => i.textContent)).toEqual(['Rinomina', 'Elimina']);
    expect(screen.getByRole('separator')).toBeInTheDocument();
    expect(items[1].className).toMatch(/--danger/);
    fireEvent.keyDown(items[0], { key: 'Escape' });
    expect(screen.queryByRole('menu')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Azioni' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Elimina' }));
    expect(removed).toBe(1);
  });

  it('card della dashboard e degli eseguibili, Object Storage: Elimina mai a vista accanto alla primaria (DB1, S7)', () => {
    for (const file of ['components/projects/ProjectCard.jsx', 'components/projects/EseguibileCard.jsx', 'pages/EseguibiliList.jsx', 'pages/AdminObjectStoragePage.jsx']) {
      const code = JS.find(f => f.file === file).clean;
      expect({ file, menu: /<ActionMenu\b/.test(code) }).toEqual({ file, menu: true });
      expect({ file, bareDelete: /<button[^>]*>\s*🗑/.test(code) || /storage-admin__purge/.test(code) }).toEqual({ file, bareDelete: false });
    }
  });
});

// ---------------------------------------------------- 5. accessibilità ---
describe('accessibilità', () => {
  // Elementi non interattivi con onClick: ammessi solo con role e tastiera,
  // o se il click ferma solo la propagazione (contenitore dentro una card).
  it('niente onClick su div/span/tr/td/li/article/section senza ruolo e tastiera (a11y-1)', () => {
    const out = [];
    for (const { file, clean } of JS.filter(f => f.file.endsWith('.jsx'))) {
      for (const m of clean.matchAll(/<(div|span|tr|td|li|article|section|img|p)\b((?:[^<>{}]|\{(?:[^{}]|\{(?:[^{}]|\{[^{}]*\})*\})*\})*)>/g)) {
        const attrs = m[2];
        if (!/\bonClick=/.test(attrs)) continue;
        if (/\brole=/.test(attrs) && /\bonKeyDown=/.test(attrs)) continue;
        if (/onClick=\{\s*\(?\s*e\s*\)?\s*=>\s*e\.stopPropagation\(\)\s*\}/.test(attrs)) continue;
        out.push(`${file}:${lineOf(clean, m.index)} <${m[1]}>`);
      }
    }
    expect(out).toEqual([]);
  });

  it('UserManager: ogni etichetta è legata al suo campo, ogni modale è un <form> (a11y-2)', () => {
    const um = JS.find(f => f.file === 'pages/UserManager.jsx').clean;
    expect(um.match(/<label>/g) || []).toEqual([]);
    expect((um.match(/<form className="um__form" onSubmit=/g) || []).length).toBe(3);
  });

  it('login: il campo MFA prende il focus, il mostra-password è raggiungibile da tastiera (a11y-3, a11y-4)', () => {
    const lp = JS.find(f => f.file === 'pages/LoginPage.jsx').clean;
    expect(lp).not.toMatch(/tabIndex=\{-1\}/);
    expect(lp).toMatch(/id="lp-mfa"[\s\S]{0,900}autoFocus/);
  });
});
