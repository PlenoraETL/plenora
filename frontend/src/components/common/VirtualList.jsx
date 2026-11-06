/**
 * VirtualList — UNICA lista lunga dell'interfaccia (UI-2, 06/11/2026, V9;
 * conferma A-K6, 06/11/2026).
 *
 * Fino a `threshold` elementi disegna tutto nel contenitore di sempre
 * (`className`: stessa griglia, stesso CSS, stessi test). Oltre, disegna
 * solo le righe vicine alla parte visibile:
 *  - lista semplice: una riga per elemento;
 *  - griglia di schede (`minColumnWidth`): tante colonne quante ne stanno
 *    nella larghezza (come `repeat(auto-fill, minmax(...))`), una riga per
 *    gruppo di schede.
 *
 * Regole (una per caso, conferma A-K6):
 *  - SCORRE LA PAGINA, non un riquadro (`scroll="page"`, il default): la
 *    lista e' nel flusso del documento, con due spaziatori alti quanto le
 *    righe non disegnate; la parte visibile si calcola dalla posizione della
 *    lista nel contenitore che scorre (il primo antenato con overflow, o la
 *    finestra). Niente doppio scorrimento, e niente contenitore con
 *    overflow che taglia i menu (⋯) delle schede;
 *  - nei nodi del canvas (`inCanvasNode`) la lista ha il SUO riquadro alto
 *    al piu' `maxHeight`, con le classi `nowheel nodrag` di React Flow: la
 *    rotella scorre le righe (non zooma) e trascinare la barra non sposta il
 *    nodo;
 *  - altezze MISURATE e ricordate PER CHIAVE (`itemKey`), non per
 *    posizione: filtrare o riordinare non attribuisce a una riga l'altezza
 *    di un'altra; prima della misura vale `estimatedItemHeight`;
 *  - la riga che ha il fuoco resta disegnata anche fuori vista, e sopra e
 *    sotto la parte visibile ci sono righe in piu' (OVERSCAN_PX): Tab e
 *    Shift+Tab raggiungono la riga dopo (il browser la porta in vista e la
 *    finestra delle righe la segue);
 *  - Ctrl+F / Cmd+F sulla pagina: la lista si disegna intera (la ricerca del
 *    browser trova anche le righe lontane) finche' resta montata.
 * Le liste lunghe dichiarate stanno in LONG_LISTS (test di enumerazione
 * tests/shared/virtualLists.contract): una lista nuova dei tipi elencati che
 * disegna `.map` senza passare di qui fa fallire il test.
 */
import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { matchesShortcut } from '../../utils/shortcuts';
import { isShortcutBlocked } from '../../utils/dialogStack';
import './VirtualList.css';

const VIRTUAL_THRESHOLD = 60;
// Righe in piu' sopra e sotto la parte visibile (Tab, scorrimento veloce).
const OVERSCAN_PX = 600;

// Le liste che possono crescere senza limite e passano da VirtualList.
export const LONG_LISTS = Object.freeze({
  'components/nodes/TableNode.jsx': 'colonne della tabella',
  'pages/ProjectList.jsx': 'progetti di ogni sezione',
  'pages/FileManager.jsx': 'file caricati',
  'pages/ScheduleList.jsx': 'schedulazioni',
});

const useIsoLayoutEffect = typeof window === 'undefined' ? useEffect : useLayoutEffect;

/** Il primo antenato che scorre in verticale (null = la finestra). */
function scrollParentOf(el) {
  for (let node = el?.parentElement; node && node !== document.body && node !== document.documentElement; node = node.parentElement) {
    const { overflowY } = window.getComputedStyle(node);
    if (/(auto|scroll|overlay)/.test(overflowY)) return node;
  }
  return null;
}

/** Riga misurata: comunica la sua altezza (per chiave) a chi la dispone. */
function MeasuredRow({ rowKey, onSize, className, style, children }) {
  const ref = useRef(null);
  useIsoLayoutEffect(() => {
    const el = ref.current;
    if (!el) return undefined;
    const report = () => onSize(rowKey, el.offsetHeight);
    report();
    if (typeof ResizeObserver !== 'function') return undefined;
    const ro = new ResizeObserver(report);
    ro.observe(el);
    return () => ro.disconnect();
  }, [rowKey, onSize]);
  return <div ref={ref} className={className} style={style} data-vlist-row={rowKey}>{children}</div>;
}

export default function VirtualList({
  items,
  itemKey,
  renderItem,
  className = '',
  rowClassName = className,
  minColumnWidth = 0,
  gap = 0,
  estimatedItemHeight = 48,
  maxHeight = 640,
  threshold = VIRTUAL_THRESHOLD,
  inCanvasNode = false,
  label,
}) {
  const list = items || [];
  const virtual = list.length > threshold;
  const boxed = inCanvasNode;
  const containerRef = useRef(null);
  // Altezze misurate per chiave di riga (A-K6: mai per posizione).
  const sizesRef = useRef(new Map());
  const [measureTick, setMeasureTick] = useState(0);
  const [view, setView] = useState({ top: 0, bottom: 0, width: 0 });
  const [renderAll, setRenderAll] = useState(false);
  const [focusedKey, setFocusedKey] = useState(null);

  const columns = minColumnWidth > 0 && view.width > 0
    ? Math.max(1, Math.floor((view.width + gap) / (minColumnWidth + gap)))
    : 1;
  const rows = useMemo(() => {
    if (!virtual) return [];
    const out = [];
    for (let i = 0; i < list.length; i += columns) {
      const group = list.slice(i, i + columns);
      // Chiave della riga: le chiavi dei suoi elementi (e quante colonne).
      out.push({ start: i, items: group, rowKey: `${columns}:${group.map((item, j) => itemKey(item, i + j)).join('|')}` });
    }
    return out;
  }, [list, columns, virtual, itemKey]);

  const rowEstimate = estimatedItemHeight + gap;
  const onSize = useCallback((rowKey, height) => {
    const next = height + gap;
    if (!height || sizesRef.current.get(rowKey) === next) return;
    sizesRef.current.set(rowKey, next);
    setMeasureTick(t => t + 1);
  }, [gap]);

  // Posizione della lista rispetto alla parte visibile del suo contenitore.
  useIsoLayoutEffect(() => {
    const el = containerRef.current;
    if (!virtual || !el) return undefined;
    const parent = boxed ? el : scrollParentOf(el);
    const target = parent || window;
    const read = () => {
      const rect = el.getBoundingClientRect();
      let top; let bottom;
      if (boxed) {
        top = el.scrollTop;
        bottom = el.scrollTop + (el.clientHeight || maxHeight);
      } else {
        const viewTop = parent ? parent.getBoundingClientRect().top : 0;
        const viewHeight = parent ? (parent.clientHeight || window.innerHeight) : window.innerHeight;
        top = Math.max(0, viewTop - rect.top);
        bottom = Math.max(0, viewTop + viewHeight - rect.top);
      }
      const width = el.clientWidth || 0;
      setView(v => (v.top === top && v.bottom === bottom && v.width === width ? v : { top, bottom, width }));
    };
    read();
    target.addEventListener('scroll', read, { passive: true });
    window.addEventListener('resize', read);
    let ro = null;
    if (typeof ResizeObserver === 'function') {
      ro = new ResizeObserver(read);
      ro.observe(el);
    }
    return () => {
      target.removeEventListener('scroll', read);
      window.removeEventListener('resize', read);
      ro?.disconnect();
    };
  }, [virtual, boxed, maxHeight]);

  // Ctrl+F / Cmd+F: la ricerca del browser deve trovare tutte le righe.
  useEffect(() => {
    if (!virtual || boxed || renderAll) return undefined;
    const onKey = (e) => {
      if (!isShortcutBlocked(e) && matchesShortcut('find_in_page', e)) setRenderAll(true);
    };
    document.addEventListener('keydown', onKey, true);
    return () => document.removeEventListener('keydown', onKey, true);
  }, [virtual, boxed, renderAll]);

  const onFocus = useCallback((e) => {
    const row = e.target?.closest?.('[data-vlist-row]');
    setFocusedKey(row ? row.getAttribute('data-vlist-row') : null);
  }, []);
  const onBlur = useCallback((e) => {
    if (!containerRef.current?.contains(e.relatedTarget)) setFocusedKey(null);
  }, []);

  // measureTick: le altezze misurate cambiano il calcolo.
  const layout = useMemo(() => {
    const offsets = new Array(rows.length + 1);
    offsets[0] = 0;
    rows.forEach((row, i) => { offsets[i + 1] = offsets[i] + (sizesRef.current.get(row.rowKey) ?? rowEstimate); });
    return offsets;
  }, [rows, rowEstimate, measureTick]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!virtual) {
    const plain = list.map((item, i) => <React.Fragment key={itemKey(item, i)}>{renderItem(item, i)}</React.Fragment>);
    // Senza classe: gli elementi entrano nel contenitore di chi chiama.
    return className ? <div className={className}>{plain}</div> : <>{plain}</>;
  }

  const total = layout[rows.length];
  let first = 0;
  let last = rows.length - 1;
  if (!renderAll) {
    const from = view.top - OVERSCAN_PX;
    const to = view.bottom + OVERSCAN_PX;
    first = 0;
    while (first < rows.length - 1 && layout[first + 1] <= from) first += 1;
    last = first;
    while (last < rows.length - 1 && layout[last + 1] < to) last += 1;
    // La riga col fuoco resta disegnata (Tab, lettori di schermo).
    const focusedIndex = focusedKey == null ? -1 : rows.findIndex(r => r.rowKey === focusedKey);
    if (focusedIndex >= 0) {
      first = Math.min(first, focusedIndex);
      last = Math.max(last, focusedIndex);
    }
  }

  const gridStyle = minColumnWidth > 0
    ? { display: 'grid', gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))`, gap: `${gap}px` }
    : undefined;
  const rowStyle = gap > 0 ? { ...gridStyle, marginBottom: `${gap}px` } : gridStyle;
  const boxClass = boxed ? ' vlist--box nowheel nodrag' : ' vlist--page';
  const boxStyle = boxed ? { maxHeight: `${maxHeight}px` } : undefined;
  return (
    <div
      ref={containerRef}
      className={`vlist${boxClass}`}
      style={boxStyle}
      role="group"
      aria-label={label}
      onFocus={onFocus}
      onBlur={onBlur}
    >
      <div className="vlist__spacer" aria-hidden="true" style={{ height: `${layout[first]}px` }} />
      {rows.slice(first, last + 1).map(row => (
        <MeasuredRow key={row.rowKey} rowKey={row.rowKey} onSize={onSize} className={rowClassName} style={rowStyle}>
          {row.items.map((item, j) => (
            <React.Fragment key={itemKey(item, row.start + j)}>
              {renderItem(item, row.start + j)}
            </React.Fragment>
          ))}
        </MeasuredRow>
      ))}
      <div className="vlist__spacer" aria-hidden="true" style={{ height: `${Math.max(0, total - layout[last + 1])}px` }} />
    </div>
  );
}
