/**
 * UI-2 (06/11/2026, V9): le liste lunghe passano tutte da UNA lista
 * virtualizzata (components/common/VirtualList, react-window).
 *
 *  (a) enumerazione: ogni file di LONG_LISTS usa VirtualList, e ogni file di
 *      src che la usa e' dichiarato in LONG_LISTS (l'elenco resta esatto);
 *      nei file dichiarati nessuna lista resa con `.map` sull'elenco che
 *      VirtualList riceve;
 *  (b) funzionamento: sotto la soglia tutto nel contenitore di sempre; oltre,
 *      solo le righe visibili (anche a griglia), con la stessa resa per
 *      elemento.
 */
import fs from 'node:fs';
import path from 'node:path';
import React from 'react';
import { render, screen, cleanup } from '@testing-library/react';
import { describe, it, expect, afterEach } from 'vitest';
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
