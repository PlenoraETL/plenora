/**
 * UI-2 (06/11/2026, V9): le liste lunghe passano tutte da UNA lista
 * virtualizzata (components/common/VirtualList).
 *
 *  (a) enumerazione: ogni file di LONG_LISTS usa VirtualList, e ogni file di
 *      src che la usa e' dichiarato in LONG_LISTS (l'elenco resta esatto);
 *      nei file dichiarati nessuna lista resa con `.map` sull'elenco che
 *      VirtualList riceve;
 *  (b) funzionamento: sotto la soglia tutto nel contenitore di sempre; oltre,
 *      solo le righe visibili (anche a griglia), con la stessa resa per
 *      elemento;
 *  (c) conferma A-K6: nella pagina nessun riquadro che scorre (niente doppio
 *      scorrimento, menu non tagliati); nei nodi del canvas riquadro con
 *      `nowheel nodrag`; altezze per chiave; la riga col fuoco resta; Ctrl+F
 *      disegna tutto.
 */
import fs from 'node:fs';
import path from 'node:path';
import React from 'react';
import { render, screen, cleanup, act, fireEvent } from '@testing-library/react';
import { describe, it, expect, afterEach, vi } from 'vitest';
import VirtualList, { LONG_LISTS } from '../../src/components/common/VirtualList';
import { SRC, stripComments } from './canvasRenderScan';

const walk = (dir) => fs.readdirSync(dir, { withFileTypes: true }).flatMap(e => (e.isDirectory()
  ? walk(path.join(dir, e.name)) : [path.join(dir, e.name)]));
const rel = p => path.relative(SRC, p).split(path.sep).join('/');

describe('liste lunghe dichiarate (enumerazione)', () => {
  const users = walk(SRC).filter(p => /\.jsx?$/.test(p) && !p.endsWith(`VirtualList.jsx`))
    .filter(p => /<VirtualList\b/.test(fs.readFileSync(p, 'utf-8'))).map(rel).sort();

  it('ogni lista dichiarata usa VirtualList e nessun altro file la usa senza dichiararla', () => {
    expect(users).toEqual(Object.keys(LONG_LISTS).sort());
  });

  it.each(Object.keys(LONG_LISTS))('%s: l\'elenco passato a VirtualList non e\' anche reso con .map', (file) => {
    const text = stripComments(fs.readFileSync(path.join(SRC, file), 'utf-8'));
    const lists = [...text.matchAll(/<VirtualList[\s\S]*?\bitems=\{([A-Za-z_$][\w$]*)\}/g)].map(m => m[1]);
    expect(lists.length).toBeGreaterThan(0);
    for (const name of lists) expect({ file, name, mapped: new RegExp(`\\b${name}\\.map\\(`).test(text) }).toEqual({ file, name, mapped: false });
  });
});

describe('VirtualList', () => {
  afterEach(cleanup);
  const items = n => Array.from({ length: n }, (_, i) => ({ id: `i${i}`, label: `Elemento ${i}` }));
  const renderItem = item => <span data-testid="item">{item.label}</span>;

  it('sotto la soglia: tutto, nel contenitore con la classe di sempre', () => {
    const { container } = render(<VirtualList className="griglia" items={items(10)} itemKey={x => x.id} renderItem={renderItem} />);
    expect(container.querySelector('.griglia')).not.toBeNull();
    expect(screen.getAllByTestId('item')).toHaveLength(10);
  });

  it('senza classe: gli elementi entrano nel contenitore di chi chiama', () => {
    const { container } = render(<ul className="mia">{<VirtualList items={items(3)} itemKey={x => x.id} renderItem={renderItem} />}</ul>);
    expect(container.querySelector('.mia').children).toHaveLength(3);
  });

  it('oltre la soglia: solo le righe visibili, la prima per prima', () => {
    render(<VirtualList items={items(2000)} itemKey={x => x.id} renderItem={renderItem} estimatedItemHeight={40} maxHeight={400} />);
    const shown = screen.getAllByTestId('item');
    expect(shown.length).toBeGreaterThan(0);
    expect(shown.length).toBeLessThan(40);
    expect(shown[0].textContent).toBe('Elemento 0');
  });

  it('a griglia: righe di schede, sempre solo quelle visibili', () => {
    render(<VirtualList className="pl__cards-grid" minColumnWidth={285} gap={12} items={items(500)} itemKey={x => x.id} renderItem={renderItem} estimatedItemHeight={190} maxHeight={640} />);
    expect(screen.getAllByTestId('item').length).toBeLessThan(40);
  });
});

describe('VirtualList: pagina, canvas, misure, fuoco, ricerca (conferma A-K6)', () => {
  afterEach(() => { cleanup(); vi.restoreAllMocks(); });
  const items = n => Array.from({ length: n }, (_, i) => ({ id: `i${i}`, label: `Elemento ${i}` }));
  const renderItem = item => <button type="button" data-testid="item">{item.label}</button>;
  const spacers = (c) => [...c.querySelectorAll('.vlist__spacer')].map(el => parseFloat(el.style.height));

  it('nella pagina: nessun riquadro che scorre ne\' righe assolute (scorre la pagina, menu non tagliati)', () => {
    const { container } = render(<VirtualList items={items(500)} itemKey={x => x.id} renderItem={renderItem} estimatedItemHeight={40} />);
    const box = container.querySelector('.vlist');
    expect(box.className).toContain('vlist--page');
    expect(box.className).not.toMatch(/nowheel|nodrag|vlist--box/);
    expect(box.style.maxHeight).toBe('');
    expect(box.style.overflow + box.style.overflowY).toBe('');
    for (const el of box.querySelectorAll('*')) expect(el.style.position).not.toBe('absolute');
    const css = fs.readFileSync(path.join(SRC, 'components/common/VirtualList.css'), 'utf-8');
    expect(css).not.toMatch(/\.vlist\s*(>\s*div)?\s*\{[^}]*overflow/);
    // Le altezze non disegnate sono negli spaziatori.
    const [top, bottom] = spacers(container);
    expect(top).toBe(0);
    expect(bottom).toBeGreaterThan(0);
  });

  it('nel nodo del canvas: riquadro proprio con nowheel e nodrag (la rotella non zooma, la barra non sposta il nodo)', () => {
    const { container } = render(<VirtualList inCanvasNode items={items(500)} itemKey={x => x.id} renderItem={renderItem} estimatedItemHeight={26} maxHeight={420} />);
    const box = container.querySelector('.vlist');
    expect(box.className).toMatch(/\bnowheel\b/);
    expect(box.className).toMatch(/\bnodrag\b/);
    expect(box.style.maxHeight).toBe('420px');
    const table = stripComments(fs.readFileSync(path.join(SRC, 'components/nodes/TableNode.jsx'), 'utf-8'));
    expect(table).toMatch(/<VirtualList[\s\S]*?\binCanvasNode\b/);
    for (const file of Object.keys(LONG_LISTS).filter(f => f !== 'components/nodes/TableNode.jsx')) {
      const text = stripComments(fs.readFileSync(path.join(SRC, file), 'utf-8'));
      expect({ file, canvas: /\binCanvasNode\b/.test(text), box: /<VirtualList[^>]*maxHeight=/.test(text) }).toEqual({ file, canvas: false, box: false });
    }
  });

  it('altezze per CHIAVE: riordinando, la riga misurata alta resta sua', () => {
    const big = 'i0';
    vi.spyOn(HTMLElement.prototype, 'offsetHeight', 'get').mockImplementation(function height() {
      const key = this.getAttribute?.('data-vlist-row');
      if (!key) return 0;
      return key === `1:${big}` ? 340 : 40;
    });
    const list = items(500);
    const { container, rerender } = render(<VirtualList items={list} itemKey={x => x.id} renderItem={renderItem} estimatedItemHeight={40} />);
    const total = (c) => spacers(c).reduce((a, b) => a + b, 0) + [...c.querySelectorAll('[data-vlist-row]')].length * 40 + 300;
    expect(total(container)).toBe(500 * 40 + 300);
    // La riga alta va in fondo (non disegnata): l'altezza resta la sua, non passa alla prima.
    rerender(<VirtualList items={[...list].reverse()} itemKey={x => x.id} renderItem={renderItem} estimatedItemHeight={40} />);
    const rendered = [...container.querySelectorAll('[data-vlist-row]')].map(el => el.getAttribute('data-vlist-row'));
    expect(rendered).not.toContain(`1:${big}`);
    const [, bottom] = spacers(container);
    const after = 500 - rendered.length;
    expect(bottom).toBe((after - 1) * 40 + 340);
  });

  it('la riga col fuoco resta disegnata quando la lista scorre lontano (Tab, lettori di schermo)', () => {
    const { container } = render(<VirtualList items={items(500)} itemKey={x => x.id} renderItem={renderItem} estimatedItemHeight={40} />);
    const first = screen.getAllByTestId('item')[0];
    act(() => { first.focus(); });
    vi.spyOn(container.querySelector('.vlist'), 'getBoundingClientRect').mockReturnValue({ top: -15000, bottom: 5000, left: 0, right: 0, width: 0, height: 20000 });
    act(() => { window.dispatchEvent(new Event('scroll')); });
    const labels = screen.getAllByTestId('item').map(b => b.textContent);
    expect(labels).toContain('Elemento 0');
    expect(labels).toContain('Elemento 380');
    expect(document.activeElement.textContent).toBe('Elemento 0');
  });

  it('Ctrl+F: la lista si disegna intera (la ricerca del browser trova le righe lontane)', () => {
    render(<VirtualList items={items(300)} itemKey={x => x.id} renderItem={renderItem} estimatedItemHeight={40} />);
    expect(screen.getAllByTestId('item').length).toBeLessThan(60);
    act(() => { fireEvent.keyDown(document, { key: 'f', ctrlKey: true }); });
    expect(screen.getAllByTestId('item')).toHaveLength(300);
  });
});
