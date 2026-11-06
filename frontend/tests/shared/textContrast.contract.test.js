/**
 * Contract di leggibilita' del testo (UI-4, U25), per enumerazione su
 * tutti i CSS e gli stili in linea di src.
 *
 *  1. Ogni token --plen-* usato come colore del testo (`color:`) ha
 *     contrasto AA (>= 4,5:1) su ogni superficie del tema, scuro e chiaro.
 *     Il contrasto si calcola dai valori in styles/variables.css (formula
 *     WCAG 2.x della luminanza relativa): cambiare un colore senza
 *     rispettare la soglia fa fallire il test.
 *  2. Le palette delle pagine (--fm-*, --pl-*, --tk-* ...) non hanno colori
 *     propri per testo, superfici, bordi e stati: sono alias dei --plen-*
 *     (un solo posto per i colori, che il punto 1 controlla).
 *  3. Nessun testo sotto gli 11 px (CSS in px e rem, stili in linea),
 *     salvo le eccezioni dichiarate col motivo. Le dimensioni in em sono
 *     relative al genitore e non si controllano qui.
 */
import fs from 'node:fs';
import path from 'node:path';
import { describe, it, expect } from 'vitest';
import { SRC, sourceFiles, lineOf } from '../helpers/sourceScan.js';

const VARIABLES = fs.readFileSync(path.join(SRC, 'styles/variables.css'), 'utf-8');

/** Token del blocco con questo selettore: { nome: valore }. */
function tokens(selectorRe) {
  const m = VARIABLES.match(new RegExp(`${selectorRe.source}\\s*\\{([^}]*)\\}`));
  if (!m) throw new Error(`blocco non trovato: ${selectorRe}`);
  const clean = m[1].replace(/\/\*[\s\S]*?\*\//g, '');
  return Object.fromEntries([...clean.matchAll(/(--plen-[\w-]+)\s*:\s*([^;]+);/g)].map(x => [x[1], x[2].trim()]));
}
const DARK = tokens(/:root,\s*\[data-theme='dark'\]/);
// Il tema chiaro ridefinisce solo quello che cambia.
const LIGHT = { ...DARK, ...tokens(/\n\[data-theme='light'\]/) };
const THEMES = { scuro: DARK, chiaro: LIGHT };

// ── Colori e contrasto (WCAG 2.x) ─────────────────────────────────────────
function rgba(value) {
  const v = value.trim();
  let m = /^#([0-9a-f]{6})$/i.exec(v);
  if (m) return [0, 2, 4].map(i => parseInt(m[1].slice(i, i + 2), 16)).concat(1);
  m = /^#([0-9a-f]{3})$/i.exec(v);
  if (m) return [...m[1]].map(c => parseInt(c + c, 16)).concat(1);
  m = /^rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([\d.]+)\s*)?\)$/i.exec(v);
  if (m) return [Number(m[1]), Number(m[2]), Number(m[3]), m[4] == null ? 1 : Number(m[4])];
  throw new Error(`colore non riconosciuto: ${value}`);
}
/** Colore con trasparenza sopra uno sfondo opaco. */
const over = ([r, g, b, a], [R, G, B]) => [r * a + R * (1 - a), g * a + G * (1 - a), b * a + B * (1 - a), 1];
const channel = (c) => { const s = c / 255; return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4; };
const luminance = ([r, g, b]) => 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
export function contrast(fg, bg) {
  const [a, b] = [luminance(fg), luminance(bg)].sort((x, y) => y - x);
  return (a + 0.05) / (b + 0.05);
}

const AA = 4.5;
// Superfici su cui sta il testo. I testi principali anche sulla piu' scura
// delle superfici (righe selezionate, intestazioni); gli stati e gli
// accenti sulle tre di base.
const ALL_SURFACES = ['--plen-bg', '--plen-surface', '--plen-surface-2', '--plen-surface-3'];
const BASE_SURFACES = ['--plen-bg', '--plen-surface', '--plen-surface-2'];
/** Token ammessi come colore del testo -> superfici su cui devono reggere. */
const TEXT_COLORS = {
  '--plen-text': ALL_SURFACES,
  '--plen-text-sub': ALL_SURFACES,
  '--plen-text-dim': ALL_SURFACES,
  '--plen-ok': BASE_SURFACES,
  '--plen-warn': BASE_SURFACES,
  '--plen-err': BASE_SURFACES,
  '--plen-blue': BASE_SURFACES,
  '--plen-estrai': BASE_SURFACES,
  '--plen-manipola': BASE_SURFACES,
  '--plen-arricchisci': BASE_SURFACES,
};
// Gli stati anche sul loro sfondo tenue (avvisi, badge).
const ON_OWN_BG = { '--plen-ok': '--plen-ok-bg', '--plen-warn': '--plen-warn-bg', '--plen-err': '--plen-err-bg', '--plen-blue': '--plen-blue-bg' };

const CSS = sourceFiles(/\.css$/);
const CODE = sourceFiles(/\.(jsx?|css)$/);

describe('contrasto AA dei colori del testo', () => {
  it('ogni token --plen-* usato come colore del testo e\' nella tabella', () => {
    const used = new Set();
    for (const { code } of CODE) {
      for (const m of code.matchAll(/(?<![-\w])color\s*:\s*['"]?var\((--plen-[\w-]+)/g)) used.add(m[1]);
    }
    expect(used.size).toBeGreaterThan(5);
    expect([...used].filter(t => !TEXT_COLORS[t]).sort()).toEqual([]);
  });

  const cases = Object.entries(THEMES).flatMap(([theme, t]) => Object.entries(TEXT_COLORS).flatMap(([fg, surfaces]) => [
    ...surfaces.map(bg => [theme, fg, bg, () => rgba(t[bg])]),
    ...(ON_OWN_BG[fg] ? [[theme, fg, ON_OWN_BG[fg], () => over(rgba(t[ON_OWN_BG[fg]]), rgba(t['--plen-surface']))]] : []),
  ]));

  it.each(cases)('tema %s: %s su %s >= 4,5:1', (theme, fg, bg, background) => {
    const ratio = contrast(rgba(THEMES[theme][fg]), background());
    expect({ theme, fg, bg, ok: ratio >= AA, ratio: Number(ratio.toFixed(2)) }).toMatchObject({ theme, fg, bg, ok: true });
  });

  it('la formula e\' quella WCAG (casi noti)', () => {
    expect(contrast(rgba('#000000'), rgba('#FFFFFF'))).toBeCloseTo(21, 5);
    expect(contrast(rgba('#767676'), rgba('#FFFFFF'))).toBeCloseTo(4.54, 2);
    expect(contrast(over(rgba('rgba(0, 0, 0, 0.5)'), rgba('#FFFFFF')), rgba('#FFFFFF'))).toBeGreaterThan(3);
  });
});

describe('palette delle pagine: alias dei token --plen-*', () => {
  const NAMES = ['bg', 'surface', 'surface-2', 'surface-3', 'border', 'border-hi', 'text', 'text-sub', 'text-dim',
    'blue', 'blue-mid', 'ok', 'warn', 'err', 'estrai', 'manipola', 'arricchisci'];
  const OWN = new Set(['styles/variables.css']);
  // Prefissi che non sono palette di pagina: i token stessi, i legacy
  // (--color-*, gia' alias in design-tokens-bridge.css) e la larghezza e lo
  // sfondo della sidebar legacy (alias anch'esso nel bridge).
  const NOT_PALETTE = new Set(['plen', 'color', 'sidebar']);
  const DECL = new RegExp(`(?<![\\w-])--([a-z0-9]+)-(${NAMES.join('|')})\\s*:\\s*([^;]+);`, 'g');

  it('nessuna pagina ridefinisce testo, superfici, bordi o stati con un colore proprio', () => {
    const offenders = [];
    for (const { file, code } of CSS.filter(f => !OWN.has(f.file))) {
      const clean = code.replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' '));
      for (const m of clean.matchAll(DECL)) {
        if (NOT_PALETTE.has(m[1])) continue;
        if (m[3].trim() !== `var(--plen-${m[2]})`) offenders.push(`${file}:${lineOf(clean, m.index)}: --${m[1]}-${m[2]}: ${m[3].trim()}`);
      }
    }
    expect(offenders).toEqual([]);
  });
});

describe('testo di almeno 11 px', () => {
  const MIN_PX = 11;
  /** Eccezioni: file:selettore -> motivo. Oggi nessuna. */
  const EXCEPTIONS = {};

  it('nessuna regola CSS con font-size sotto gli 11 px (px e rem)', () => {
    const offenders = [];
    for (const { file, code } of CSS) {
      const clean = code.replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' '));
      for (const m of clean.matchAll(/font(?:-size)?\s*:[^;{}]*?(?<![\w.-])(\d*\.?\d+)(px|rem)\b/g)) {
        const px = m[2] === 'rem' ? Number(m[1]) * 16 : Number(m[1]);
        const where = `${file}:${lineOf(clean, m.index)}`;
        if (px < MIN_PX && !EXCEPTIONS[where]) offenders.push(`${where}: ${m[0].trim()}`);
      }
    }
    expect(offenders).toEqual([]);
  });

  it('nessuno stile in linea con fontSize sotto gli 11 px', () => {
    const offenders = [];
    for (const { file, code } of sourceFiles(/\.jsx?$/)) {
      for (const m of code.matchAll(/fontSize\s*[:=]\s*\{?\s*['"]?(\d*\.?\d+)(px|rem)?['"]?/g)) {
        const px = m[2] === 'rem' ? Number(m[1]) * 16 : Number(m[1]);
        // Numeri senza unita' in React = px; '0.85' senza unita' non esiste.
        if (px < MIN_PX && !/em['"]/.test(code.slice(m.index, m.index + m[0].length + 3))) {
          offenders.push(`${file}:${lineOf(code, m.index)}: ${m[0]}`);
        }
      }
    }
    expect(offenders).toEqual([]);
  });
});
