/**
 * Contract delle icone per significato e dei tipi di progetto (UI-4, U24),
 * per enumerazione sulle tabelle dei nomi visibili.
 *
 *  1. Un'icona = un significato: i valori di ICON (utils/icons) sono tutti
 *     diversi.
 *  2. Le tabelle dei nomi visibili (glossario degli editor, navigazione,
 *     tipi di progetto) prendono l'icona da ICON: nessuna scritta a mano.
 *     Nei loro file e nei componenti che le mostrano non ci sono emoji
 *     letterali.
 *  3. Un significato = un'icona: due voci delle tabelle con la stessa icona
 *     sono lo stesso significato, dichiarato in SAME_MEANING (es. il passo
 *     Data Source e la pagina Data Source). Un doppione nuovo fa fallire.
 *  4. Tipi di progetto: una sola tabella (nome, icona, descrizione), le
 *     stesse chiavi delle rotte degli editor; nessun «SQL» o tipo in
 *     maiuscolo scritto a mano al posto del nome.
 */
import fs from 'node:fs';
import path from 'node:path';
import { describe, it, expect } from 'vitest';
import { ICON } from '../../src/utils/icons';
import { TERMS } from '../../src/utils/glossary';
import { NAV_ITEMS } from '../../src/utils/navItems';
import { PROJECT_TYPES, projectType, projectTypeChip } from '../../src/utils/projectTypes';
import { PROJECT_EDITOR_ROUTES } from '../../src/utils/projectRoutes';
import { SRC, sourceFiles, stripComments } from '../helpers/sourceScan.js';

const norm = (s) => String(s).replace(/️/g, '');
const EMOJI = /[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}\u{2B00}-\u{2BFF}\u{2300}-\u{23FF}]/u;

// Tutte le voci con un'icona delle tabelle dei nomi visibili.
const ENTRIES = [
  ...Object.entries(TERMS).map(([k, t]) => [`glossario:${k}`, t.icon]),
  ...NAV_ITEMS.map(i => [`navigazione:${i.key}`, i.icon]),
  ...Object.entries(PROJECT_TYPES).map(([k, t]) => [`tipo:${k}`, t.icon]),
];

/*
 * Voci che condividono un'icona perche' hanno lo stesso significato.
 * Chiave: significato in ICON; valore: le voci, esattamente.
 */
const SAME_MEANING = {
  join: ['glossario:multi_join', 'glossario:join'], // unione su chiavi, a due o piu' tabelle
  datasource: ['glossario:datasource', 'glossario:datasource_write', 'navigazione:datasources'],
  file: ['glossario:file_source', 'navigazione:files'],
  api: ['glossario:api_enrich', 'navigazione:apis'],
  connection: ['glossario:connections', 'navigazione:connections'],
  schedule: ['glossario:schedule', 'navigazione:schedules'],
  estrai: ['navigazione:estrai', 'tipo:estrai'],
  manipola: ['navigazione:manipola', 'tipo:manipola'],
  arricchisci: ['navigazione:arricchisci', 'tipo:arricchisci'],
};

// File delle tabelle e componenti che le mostrano: niente emoji a mano.
const NO_LITERAL_ICONS = [
  'utils/glossary.js', 'utils/navItems.js', 'utils/projectTypes.js',
  'components/AppSidebar.jsx', 'components/common/CommandPalette.jsx',
  'components/common/KeyboardShortcutsHelp.jsx', 'components/panels/ProjectsSidebar.jsx',
];

describe('icone: un\'icona = un significato', () => {
  it('i valori di ICON sono tutti diversi', () => {
    const seen = new Map();
    const dup = [];
    for (const [meaning, icon] of Object.entries(ICON)) {
      const k = norm(icon);
      if (seen.has(k)) dup.push(`${icon}: ${seen.get(k)} e ${meaning}`);
      seen.set(k, meaning);
    }
    expect(dup).toEqual([]);
  });

  it('ogni voce delle tabelle usa un\'icona di ICON', () => {
    const values = new Set(Object.values(ICON).map(norm));
    expect(ENTRIES.length).toBeGreaterThan(40);
    expect(ENTRIES.filter(([, icon]) => !values.has(norm(icon))).map(([w, icon]) => `${w}: ${icon}`)).toEqual([]);
  });

  it.each(NO_LITERAL_ICONS)('%s: nessuna emoji scritta a mano (si usa ICON)', (file) => {
    const code = stripComments(fs.readFileSync(path.join(SRC, file), 'utf-8'));
    const lines = code.split('\n').map((l, i) => [i + 1, l]).filter(([, l]) => EMOJI.test(l));
    expect(lines.map(([n, l]) => `${file}:${n}: ${l.trim()}`)).toEqual([]);
  });
});

describe('icone: un significato = un\'icona', () => {
  it('le voci con la stessa icona sono lo stesso significato, dichiarato', () => {
    const byIcon = new Map();
    for (const [who, icon] of ENTRIES) {
      const k = norm(icon);
      if (!byIcon.has(k)) byIcon.set(k, []);
      byIcon.get(k).push(who);
    }
    const meaningOf = new Map(Object.entries(ICON).map(([m, i]) => [norm(i), m]));
    const shared = Object.fromEntries([...byIcon].filter(([, who]) => who.length > 1)
      .map(([icon, who]) => [meaningOf.get(icon), who.sort()]));
    const declared = Object.fromEntries(Object.entries(SAME_MEANING).map(([m, who]) => [m, [...who].sort()]));
    expect(shared).toEqual(declared);
  });

  it('le icone che avevano piu\' significati ne hanno uno solo (💾, 📁, 🔍)', () => {
    const owners = (icon) => ENTRIES.filter(([, i]) => norm(i) === norm(icon)).map(([w]) => w);
    expect(owners('💾')).toEqual(['glossario:save']);
    expect(owners('📁')).toEqual([]); // «cartella»: non e' un'entita' delle tabelle
    expect(owners('🔍')).toEqual([]); // «cerca»: non e' «Valida»
    expect(TERMS.validate.icon).toBe(ICON.validate);
    expect(TERMS.output.icon).not.toBe(TERMS.datasource.icon);
  });
});

describe('tipi di progetto: una sola tabella', () => {
  it('stesse chiavi delle rotte degli editor; nome, icona e descrizione', () => {
    expect(Object.keys(PROJECT_TYPES).sort()).toEqual(Object.keys(PROJECT_EDITOR_ROUTES).sort());
    for (const [k, t] of Object.entries(PROJECT_TYPES)) {
      expect({ k, ok: Boolean(t.label && t.icon && t.description) }).toEqual({ k, ok: true });
    }
    expect(new Set(Object.values(PROJECT_TYPES).map(t => t.label)).size).toBe(3);
    // Tipo assente o sconosciuto = Estrai (come le rotte e il backend).
    expect(projectType(undefined)).toBe(PROJECT_TYPES.estrai);
    expect(projectType('altro')).toBe(PROJECT_TYPES.estrai);
    expect(projectTypeChip('manipola')).toBe(`${ICON.manipola} Manipola`);
  });

  it('nessun nome del tipo scritto a mano: niente «SQL» ne\' tipo in maiuscolo', () => {
    const offenders = [];
    for (const { file, code } of sourceFiles(/\.jsx?$/)) {
      const clean = stripComments(code);
      if (/project_?[tT]ype\)?\s*(?:\|\|\s*'[^']*'\)?)?\??\.toUpperCase\(\)/.test(clean)) offenders.push(`${file}: tipo in maiuscolo`);
      if (/\[t\]\s*\|\|\s*'SQL'|'SQL'\s*\)\s*;?\s*$/m.test(clean)) offenders.push(`${file}: «SQL» come tipo di progetto`);
    }
    expect(offenders).toEqual([]);
  });
});
