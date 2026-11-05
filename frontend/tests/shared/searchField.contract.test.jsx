/**
 * Contract (Fase 5, voce P28 «ricerca Invio contro digitazione»): UN solo
 * componente di ricerca.
 *
 * Regola: ogni ricerca delle pagine e dei pannelli usa
 * components/common/SearchField con hooks/useSearch: filtra mentre scrivi
 * dopo la pausa dichiarata (Invio applica subito), Esc svuota, × cancella,
 * etichetta accessibile, conteggio «N di M», stato vuoto «Nessun risultato
 * per "x"» con «Cancella ricerca»; lato server la stessa pausa e una
 * richiesta alla volta (useResource). In sola lettura resta attiva.
 *
 * Statico: nessun input di ricerca fatto a mano (type="search", segnaposto
 * o etichetta «Cerca…»/«Filtra…», icona 🔍/⌕ nel segnaposto) fuori da
 * SearchField, salvo le eccezioni dichiarate qui sotto col motivo.
 */
import fs from 'node:fs';
import path from 'node:path';
import React, { useRef } from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';
import SearchField, { SearchEmpty } from '../../src/components/common/SearchField';
import { useSearch } from '../../src/hooks/useSearch';
import { useResource } from '../../src/hooks/useResource';
import { useReadOnlyLock } from '../../src/components/common/readOnlySafe';

const SRC = path.resolve(import.meta.dirname, '../../src');
const walk = (dir) => fs.readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
  const full = path.join(dir, e.name);
  if (e.isDirectory()) return walk(full);
  return /\.jsx?$/.test(e.name) ? [full] : [];
});
const rel = (f) => path.relative(SRC, f).split(path.sep).join('/');
const stripComments = (c) => c.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:'"`])\/\/.*$/gm, '$1');
const FILES = walk(SRC).map((f) => ({ file: rel(f), code: stripComments(fs.readFileSync(f, 'utf-8')) }));

// Input di ricerca fatti a mano ammessi, col motivo.
const DECLARED_EXCEPTIONS = {
  'components/common/SearchField.jsx': 'il componente stesso',
  'components/common/CommandPalette.jsx':
    'selettore da tastiera (Ctrl+K): frecce e Invio scelgono nella lista filtrata, il filtro deve essere sincrono col tasto; Esc chiude la tavolozza',
  'components/canvas/NodePalette.jsx':
    'selettore da tastiera delle operazioni: stesso motivo della CommandPalette (frecce e Invio sulla lista filtrata a ogni tasto)',
};

/** Attributi dei tag <input ...> (le graffe annidate contano). */
function inputTags(code) {
  const out = [];
  const re = /<input(?=[\s>/])/g;
  let m;
  while ((m = re.exec(code))) {
    let j = m.index + 6;
    let depth = 0;
    let q = null;
    for (; j < code.length; j += 1) {
      const ch = code[j];
      if (q) { if (ch === q) q = null; continue; }
      if (ch === '"' || (depth > 0 && (ch === "'" || ch === '`'))) { q = ch; continue; }
      if (ch === '{') depth += 1;
      else if (ch === '}') depth -= 1;
      else if (ch === '>' && depth === 0) break;
    }
    out.push(code.slice(m.index, j + 1));
  }
  return out;
}

const SEARCH_INPUT = /type=["{']+search|placeholder=[^\n]*(Cerca|Filtra|🔍|⌕)|aria-label=["{'`]+(Cerca|Filtra)/;

describe('statico: nessuna ricerca fatta a mano', () => {
  it('gli input di ricerca stanno solo in SearchField (eccezioni dichiarate)', () => {
    const offenders = [];
    for (const { file, code } of FILES) {
      if (DECLARED_EXCEPTIONS[file]) continue;
      for (const tag of inputTags(code)) {
        if (SEARCH_INPUT.test(tag)) offenders.push(`${file}: ${tag.replace(/\s+/g, ' ').slice(0, 100)}`);
      }
    }
    expect(offenders).toEqual([]);
  });

  it('le eccezioni dichiarate esistono ancora e hanno davvero un input di ricerca', () => {
    for (const file of Object.keys(DECLARED_EXCEPTIONS)) {
      const entry = FILES.find((f) => f.file === file);
      expect(entry, file).toBeTruthy();
      expect(inputTags(entry.code).some((t) => SEARCH_INPUT.test(t)), file).toBe(true);
    }
  });

  it('ogni SearchField ha lo stato di useSearch e un\'etichetta', () => {
    const uses = FILES.flatMap(({ file, code }) => [...code.matchAll(/<SearchField\b([\s\S]*?)\/>/g)].map((m) => ({ file, attrs: m[1] })));
    expect(uses.length).toBeGreaterThanOrEqual(20);
    const bad = uses.filter(({ attrs }) => !/\bsearch=\{/.test(attrs) || !/\blabel=/.test(attrs)).map(({ file }) => file);
    expect(bad).toEqual([]);
    // Chi usa SearchField usa anche useSearch (stesso file o un genitore che passa `search`).
    const withoutHook = [...new Set(uses.map((u) => u.file))]
      .filter((file) => !/useSearch\(/.test(FILES.find((f) => f.file === file).code))
      .filter((file) => file !== 'components/modals/PreviewTable.jsx'); // riceve `search` da PreviewModal
    expect(withoutHook).toEqual([]);
  });
});

function ClientList({ items, readOnly = false }) {
  const search = useSearch();
  const shown = search.filter(items, (it) => [it]);
  const ref = useRef(null);
  useReadOnlyLock(ref, readOnly);
  return (
    <div ref={ref}>
      <SearchField search={search} label="Cerca frutta" shown={shown.length} total={items.length} />
      <ul>{shown.map((it) => <li key={it}>{it}</li>)}</ul>
      <SearchEmpty search={search} shown={shown.length} />
      <button type="button">Modifica</button>
    </div>
  );
}

const ITEMS = ['Mela rossa', 'Pera', 'Banana', 'Melone giallo', 'Caffè'];
const listed = () => screen.queryAllByRole('listitem').map((li) => li.textContent);

describe('SearchField + useSearch (client)', () => {
  afterEach(() => vi.useRealTimers());

  it('filtra mentre scrivi dopo la pausa dichiarata, con «N di M»', () => {
    vi.useFakeTimers();
    let pause;
    function Probe() { pause = useSearch().pause; return null; }
    render(<><Probe /><ClientList items={ITEMS} /></>);
    const input = screen.getByRole('searchbox', { name: 'Cerca frutta' });
    fireEvent.change(input, { target: { value: 'mel' } });
    expect(listed()).toHaveLength(5);
    act(() => { vi.advanceTimersByTime(pause - 1); });
    expect(listed()).toHaveLength(5);
    act(() => { vi.advanceTimersByTime(1); });
    expect(listed()).toEqual(['Mela rossa', 'Melone giallo']);
    expect(screen.getByText('2 di 5')).toBeInTheDocument();
  });

  it('Invio applica subito; senza accenti e maiuscole; ogni parola deve comparire', () => {
    render(<ClientList items={ITEMS} />);
    const input = screen.getByRole('searchbox', { name: 'Cerca frutta' });
    fireEvent.change(input, { target: { value: 'CAFFE' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(listed()).toEqual(['Caffè']);
    fireEvent.change(input, { target: { value: 'giallo mel' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(listed()).toEqual(['Melone giallo']);
  });

  it('Esc svuota (e non chiude la finestra intorno); a campo vuoto Esc passa', () => {
    render(<ClientList items={ITEMS} />);
    const input = screen.getByRole('searchbox', { name: 'Cerca frutta' });
    fireEvent.change(input, { target: { value: 'pera' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(listed()).toEqual(['Pera']);
    const used = !fireEvent.keyDown(input, { key: 'Escape' });
    expect(used).toBe(true); // defaultPrevented: dialogStack non chiude
    expect(input.value).toBe('');
    expect(listed()).toHaveLength(5);
    const passed = fireEvent.keyDown(input, { key: 'Escape' });
    expect(passed).toBe(true);
  });

  it('× cancella; stato vuoto «Nessun risultato per "x"» con «Cancella ricerca»', () => {
    render(<ClientList items={ITEMS} />);
    const input = screen.getByRole('searchbox', { name: 'Cerca frutta' });
    fireEvent.change(input, { target: { value: 'kiwi' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(screen.getByText('Nessun risultato per "kiwi"')).toBeInTheDocument();
    expect(screen.getByText('0 di 5')).toBeInTheDocument();
    fireEvent.click(screen.getByText('Cancella ricerca', { selector: '.search-empty__clear' }));
    expect(input.value).toBe('');
    expect(listed()).toHaveLength(5);
    fireEvent.change(input, { target: { value: 'banana' } });
    fireEvent.click(screen.getByRole('button', { name: 'Cancella ricerca' }));
    expect(input.value).toBe('');
    expect(screen.queryByRole('button', { name: 'Cancella ricerca' })).toBeNull();
  });

  it('svuotare a mano applica subito (nessuna lista filtrata da un testo che non c\'e\' piu\')', () => {
    render(<ClientList items={ITEMS} />);
    const input = screen.getByRole('searchbox', { name: 'Cerca frutta' });
    fireEvent.change(input, { target: { value: 'pera' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    fireEvent.change(input, { target: { value: '' } });
    expect(listed()).toHaveLength(5);
  });

  it('in sola lettura la ricerca resta attiva, gli altri controlli no', () => {
    render(<ClientList items={ITEMS} readOnly />);
    const input = screen.getByRole('searchbox', { name: 'Cerca frutta' });
    expect(input).not.toBeDisabled();
    fireEvent.change(input, { target: { value: 'pera' } });
    expect(screen.getByRole('button', { name: 'Cancella ricerca' })).not.toBeDisabled();
    expect(screen.getByRole('button', { name: 'Modifica' })).toBeDisabled();
  });
});

describe('useSearch lato server: stessa pausa, una richiesta alla volta', () => {
  afterEach(() => vi.useRealTimers());

  it('digitando parte UNA richiesta, con il testo finale, dopo la pausa', async () => {
    vi.useFakeTimers();
    const loader = vi.fn(async (q) => [`risultato ${q}`]);
    let pause;
    function ServerList() {
      const search = useSearch();
      pause = search.pause;
      const res = useResource(() => loader(search.query), { key: ['logs', search.query], keepPreviousData: true });
      return (
        <>
          <SearchField search={search} label="Cerca nel messaggio" shown={res.data?.length ?? null} />
          <span data-testid="data">{(res.data || []).join(',')}</span>
        </>
      );
    }
    render(<ServerList />);
    await act(async () => { await Promise.resolve(); });
    expect(loader).toHaveBeenCalledTimes(1);
    expect(loader).toHaveBeenLastCalledWith('');
    const input = screen.getByRole('searchbox', { name: 'Cerca nel messaggio' });
    for (const text of ['c', 'co', 'con', 'conn']) {
      fireEvent.change(input, { target: { value: text } });
      act(() => { vi.advanceTimersByTime(pause / 2); });
    }
    expect(loader).toHaveBeenCalledTimes(1);
    await act(async () => { vi.advanceTimersByTime(pause); await Promise.resolve(); });
    expect(loader).toHaveBeenCalledTimes(2);
    expect(loader).toHaveBeenLastCalledWith('conn');
    expect(screen.getByTestId('data').textContent).toBe('risultato conn');
    expect(screen.getByText('1 risultato')).toBeInTheDocument();
  });
});
