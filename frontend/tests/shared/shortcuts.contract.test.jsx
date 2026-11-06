/**
 * Contract della guida delle scorciatoie (UI-4, U28), per enumerazione sui
 * gestori dei tasti di src e sulla tabella utils/shortcuts.
 *
 *  1. Ogni gestore GLOBALE dei tasti (addEventListener('keydown') su window
 *     o document) riconosce i tasti solo con matchesShortcut('id'): nessun
 *     confronto su `key` scritto a mano. Eccezioni dichiarate col motivo.
 *     I tasti dentro un singolo controllo (frecce di una lista, Invio in un
 *     campo: onKeyDown) non sono scorciatoie e non si contano.
 *  2. Gestita = nella guida: ogni id usato dai gestori esiste nella tabella;
 *     nella guida = gestita: ogni riga della tabella e' usata da un gestore.
 *  3. La guida mostra tutte le righe della tabella, e si apre con «?», con
 *     il pulsante «?» (pagine ed editor) e dalla palette.
 *  4. I tasti fanno quello che la guida dice (casi per ogni riga).
 */
import fs from 'node:fs';
import path from 'node:path';
import React from 'react';
import { render, screen, fireEvent, act, cleanup } from '@testing-library/react';
import { describe, it, expect, afterEach } from 'vitest';
import KeyboardShortcutsHelp from '../../src/components/common/KeyboardShortcutsHelp';
import ShortcutsButton from '../../src/components/common/ShortcutsButton';
import { SHORTCUTS, matchesShortcut } from '../../src/utils/shortcuts';
import { SRC, sourceFiles, stripComments, lineOf } from '../helpers/sourceScan.js';

const FILES = sourceFiles(/\.jsx?$/).map(f => ({ ...f, clean: stripComments(f.code) }));
const GLOBAL_HANDLERS = FILES.filter(f => /\.addEventListener\(\s*['"]keydown['"]/.test(f.clean));

// Confronti sui tasti ammessi nei gestori globali, col motivo.
const DECLARED = {
  'utils/dialogStack.js': [/e\.key === 'Tab'/, 'Tab tiene il fuoco nella finestra in cima: accessibilita\', non una scorciatoia'],
};
const KEY_COMPARE = /\.key\b(?:\.toLowerCase\(\))?\s*(?:===|!==|==|!=)|\bkey\s*===\s*['"]|\.key\s*\)\s*\?|\[e(?:vent)?\.key\]/g;

afterEach(() => cleanup());

describe('gestori globali dei tasti: solo dalla tabella', () => {
  it('i gestori globali sono quelli attesi (il perimetro e\' letto)', () => {
    expect(GLOBAL_HANDLERS.map(f => f.file).sort()).toEqual(expect.arrayContaining([
      'components/common/CommandPalette.jsx', 'components/common/KeyboardShortcutsHelp.jsx',
      'components/editor/EditorShell.jsx', 'hooks/useCanvasKeyboard.js', 'utils/dialogStack.js',
    ]));
  });

  it('nessun confronto sui tasti scritto a mano nei gestori globali', () => {
    const offenders = [];
    for (const { file, clean } of GLOBAL_HANDLERS) {
      for (const m of clean.matchAll(KEY_COMPARE)) {
        const line = clean.split('\n')[lineOf(clean, m.index) - 1];
        if (DECLARED[file] && DECLARED[file][0].test(line)) continue;
        // I tasti delle liste dentro un controllo (onKeyDown/onListKey) non sono globali.
        if (/onKeyDown|onListKey|ArrowDown|ArrowUp|Home|End|Enter/.test(line)) continue;
        offenders.push(`${file}:${lineOf(clean, m.index)}: ${line.trim()}`);
      }
    }
    expect(offenders).toEqual([]);
  });

  it('gestita = nella guida, nella guida = gestita', () => {
    const ids = new Set(SHORTCUTS.map(s => s.id));
    const used = new Set();
    for (const { file, clean } of FILES) {
      if (file === 'utils/shortcuts.js') continue;
      for (const m of clean.matchAll(/matchesShortcut\(\s*'([\w-]+)'/g)) used.add(m[1]);
    }
    expect([...used].filter(id => !ids.has(id))).toEqual([]);
    expect([...ids].filter(id => !used.has(id))).toEqual([]);
    expect(new Set(SHORTCUTS.map(s => s.label)).size).toBe(SHORTCUTS.length);
  });
});

describe('i tasti fanno quello che dice la guida', () => {
  const k = (key, mods = {}) => ({ key, ctrlKey: false, metaKey: false, shiftKey: false, altKey: false, ...mods });
  const CASES = {
    palette: [[k('k', { ctrlKey: true }), true], [k('K', { metaKey: true }), true], [k('k'), false]],
    help: [[k('?', { shiftKey: true }), true], [k('/', { shiftKey: true }), true], [k('/'), false]],
    close: [[k('Escape'), true], [k('Enter'), false]],
    save: [[k('s', { ctrlKey: true }), true], [k('S', { metaKey: true }), true], [k('s'), false]],
    add_step: [[k('/'), true], [k('a'), true], [k('A'), true], [k('a', { ctrlKey: true }), false]],
    undo: [[k('z', { ctrlKey: true }), true], [k('z', { ctrlKey: true, shiftKey: true }), false]],
    redo: [[k('z', { ctrlKey: true, shiftKey: true }), true], [k('y', { ctrlKey: true }), true], [k('y'), false]],
    delete: [[k('Delete'), true], [k('Backspace'), true], [k('d'), false]],
    select_all: [[k('a', { ctrlKey: true }), true], [k('a'), false], [k('a', { ctrlKey: true, shiftKey: true }), false]],
    copy: [[k('c', { ctrlKey: true }), true], [k('C', { metaKey: true }), true], [k('c'), false]],
    paste: [[k('v', { ctrlKey: true }), true], [k('v'), false]],
    duplicate: [[k('d', { ctrlKey: true }), true], [k('d'), false]],
    search_canvas: [[k('f', { ctrlKey: true }), true], [k('f'), false]],
    connect: [[k('c'), true], [k('C'), true], [k('c', { ctrlKey: true }), false]],
    editor_guide: [[k('F1'), true], [k('1'), false]],
  };

  it('ogni riga della tabella ha i suoi casi', () => {
    expect(Object.keys(CASES).sort()).toEqual(SHORTCUTS.map(s => s.id).sort());
  });

  it.each(Object.entries(CASES))('%s', (id, cases) => {
    for (const [event, expected] of cases) expect({ id, event, hit: matchesShortcut(id, event) }).toEqual({ id, event, hit: expected });
  });

  it('un id non dichiarato e\' un errore di programmazione', () => {
    expect(() => matchesShortcut('inesistente', k('x'))).toThrow(/non dichiarata/);
  });
});

describe('la guida', () => {
  it('mostra ogni riga della tabella; si apre con ? e con il pulsante «?»', async () => {
    render(<><KeyboardShortcutsHelp /><ShortcutsButton /></>);
    expect(screen.queryByRole('dialog')).toBeNull();
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Scorciatoie da tastiera' })); });
    const dialog = screen.getByRole('dialog');
    for (const s of SHORTCUTS) {
      const row = dialog.querySelector(`[data-shortcut="${s.id}"]`);
      expect({ id: s.id, label: row?.textContent.includes(s.label), keys: s.keys.every(x => row?.textContent.includes(x)) })
        .toEqual({ id: s.id, label: true, keys: true });
    }
    await act(async () => { fireEvent.keyDown(document, { key: 'Escape' }); });
    expect(screen.queryByRole('dialog')).toBeNull();
    await act(async () => { fireEvent.keyDown(window, { key: '?', shiftKey: true }); });
    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });

  it('il pulsante «?» e\' nell\'intestazione delle pagine e nella barra degli editor', () => {
    for (const file of ['components/common/PageLayout.jsx', 'components/editor/EditorToolbar.jsx']) {
      expect({ file, ok: /<ShortcutsButton\b/.test(fs.readFileSync(path.join(SRC, file), 'utf-8')) }).toEqual({ file, ok: true });
    }
  });
});
