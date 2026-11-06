/**
 * Contract dei termini di TUTTA l'app (UI-4, U22, U14, U32), per
 * enumerazione sul testo visibile di ogni file di src (testi JSX,
 * attributi mostrati, messaggi: tests/helpers/visibleText).
 *
 *  1. Nessun termine sostituito della tabella CONCEPTS (utils/glossary):
 *     «flusso», «catena», «pipeline» -> progetto; «step» -> passo; «run»,
 *     «task» -> esecuzione; «schedule» -> pianificazione; «upload»,
 *     «download», «cleanup», «preview», «template», «batch»... Gli editor
 *     hanno in piu' l'elenco lungo del test del glossario.
 *  2. «Output» solo maiuscolo (il passo finale); il file prodotto e' «il
 *     risultato».
 *  3. Le eccezioni sono dichiarate qui per file e parola, col motivo; una
 *     dichiarazione che non serve piu' fa fallire il test.
 */
import path from 'node:path';
import { describe, it, expect } from 'vitest';
import { ALLOWED_ENGLISH, CONCEPTS } from '../../src/utils/glossary';
import { visibleTexts } from '../helpers/visibleText.js';
import { SRC, sourceFiles } from '../helpers/sourceScan.js';

const FILES = sourceFiles(/\.jsx?$/).filter(f => f.file !== 'utils/glossary.js' && !f.file.startsWith('mocks/'));
const TEXTS = FILES.flatMap(f => visibleTexts(f.abs));

/*
 * Eccezioni: 'file' -> { parola: motivo }. Le pagine del superadmin
 * amministrano gli oggetti tecnici con il loro nome.
 */
const EXCEPTIONS = {
  'pages/AdminTaskRuntimePage.jsx': { task: 'i task di Dramatiq sono l\'oggetto che la pagina amministra (code, worker, lease)' },
  'pages/AdminRuntimeConfigPage.jsx': { task: 'contatore dei task di Dramatiq nella configurazione del server' },
  'pages/AdminSystemStatusPage.jsx': { task: 'stato dei task di Dramatiq su PostgreSQL' },
  'pages/AdminObjectStoragePage.jsx': { preview: 'nome della cartella «preview» nell\'archivio S3' },
  'utils/navItems.js': { task: 'descrizione della pagina del superadmin «Code e worker»' },
  'pages/AdminBackupsPage.jsx': { login: 'comando da terminale «aws sso login»' },
};

const escapeRe = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
const wordRe = (w, flags = 'iu') => new RegExp(`(?<![\\p{L}\\d_-])${escapeRe(w)}(?![\\p{L}\\d_-])`, flags);
const REPLACED = Object.entries(CONCEPTS).flatMap(([concept, c]) => c.replaces.map(w => [w, concept, wordRe(w)]));
const EXACT = Object.values(CONCEPTS).flatMap(c => c.exactCase || []);
// Parole tutte maiuscole: codice o nomi tecnici (SQL, UPSERT); segnaposto {x}; nomi tecnici ammessi.
const TECH = [/(?<![\p{L}\d_])\p{Lu}{2,}(?![\p{L}\d_])/gu, /\{[\w.]+\}/gu, /`[^`]*`/gu,
  ...ALLOWED_ENGLISH.technical.map(w => new RegExp(wordRe(w).source, 'giu'))];
const clean = (t) => TECH.reduce((s, re) => s.replace(re, ' '), t);

function offendersOf({ file, text }) {
  const t = clean(text);
  const found = REPLACED.filter(([, , re]) => re.test(t)).map(([w]) => w);
  for (const exact of EXACT) {
    for (const m of t.matchAll(new RegExp(wordRe(exact).source, 'giu'))) if (m[0] !== exact) found.push(m[0].toLowerCase());
  }
  return found.filter(w => !EXCEPTIONS[file]?.[w]);
}

describe('termini: un concetto, un termine, in tutta l\'app', () => {
  it('il perimetro e\' tutta l\'app (pagine, componenti, messaggi)', () => {
    expect(FILES.length).toBeGreaterThan(250);
    expect(TEXTS.length).toBeGreaterThan(3000);
    for (const f of ['pages/EsecutoreHome.jsx', 'components/AppSidebar.jsx', 'components/eseguibili/EseguibileExecuteWizard.jsx', 'pages/ScheduleList.jsx']) {
      expect(FILES.map(x => x.file)).toContain(f);
    }
  });

  it('nessun termine sostituito nel testo visibile', () => {
    const offenders = TEXTS
      .map(t => ({ ...t, words: offendersOf(t) }))
      .filter(t => t.words.length)
      .map(t => `${t.file}:${t.line}: [${t.words.join(', ')}] ${t.text.slice(0, 110)}`);
    expect(offenders).toEqual([]);
  });

  it('ogni eccezione dichiarata serve ancora', () => {
    const used = new Set();
    for (const t of TEXTS) {
      const c = clean(t.text);
      for (const w of Object.keys(EXCEPTIONS[t.file] || {})) if (wordRe(w).test(c)) used.add(`${t.file}:${w}`);
    }
    const declared = Object.entries(EXCEPTIONS).flatMap(([f, ws]) => Object.keys(ws).map(w => `${f}:${w}`));
    expect(declared.filter(d => !used.has(d))).toEqual([]);
  });

  it('il controllo riconosce i termini e rispetta le eccezioni', () => {
    const at = (text, file = 'x.jsx') => offendersOf({ file, text });
    expect(at('La catena si aspetta queste colonne')).toEqual(['catena']);
    expect(at('Task in coda...')).toEqual(['task']);
    expect(at('Formato output')).toEqual(['output']);
    expect(at('Collega il Join all\'Output')).toEqual([]);
    expect(at('Task attivi', 'pages/AdminTaskRuntimePage.jsx')).toEqual([]);
    expect(at('Modalità UPSERT su {table} via API REST')).toEqual([]);
    expect(path.basename(SRC)).toBe('src');
  });
});
