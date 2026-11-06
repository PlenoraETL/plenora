/**
 * VirtualList — UNICA lista lunga dell'interfaccia (UI-2, 06/11/2026, V9).
 *
 * Fino a `threshold` elementi disegna tutto nel contenitore di sempre
 * (`className`: stessa griglia, stesso CSS, stessi test). Oltre, disegna
 * solo le righe visibili con react-window dentro un riquadro alto al piu'
 * `maxHeight` che scorre da solo:
 *  - lista semplice: una riga per elemento;
 *  - griglia di schede (`minColumnWidth`): tante colonne quante ne stanno
 *    nella larghezza (come `repeat(auto-fill, minmax(...))`), una riga per
 *    gruppo di schede;
 *  - altezza delle righe MISURATA (ResizeObserver): schede che si aprono
 *    (log, esecuzioni) o testi lunghi non vengono tagliati; prima della
 *    misura vale `estimatedItemHeight`.
 * Le liste lunghe dichiarate stanno in LONG_LISTS (test di enumerazione
 * tests/shared/virtualLists.contract): una lista nuova dei tipi elencati che
 * disegna `.map` senza passare di qui fa fallire il test.
 */
import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { VariableSizeList } from 'react-window';
import './VirtualList.css';

const VIRTUAL_THRESHOLD = 60;

// Le liste che possono crescere senza limite e passano da VirtualList.
export const LONG_LISTS = Object.freeze({
  'components/nodes/TableNode.jsx': 'colonne della tabella',
  'pages/ProjectList.jsx': 'progetti di ogni sezione',
  'pages/FileManager.jsx': 'file caricati',
  'pages/ScheduleList.jsx': 'schedulazioni',
});

const useIsoLayoutEffect = typeof window === 'undefined' ? useEffect : useLayoutEffect;

function useWidth(ref, enabled) {
  const [width, setWidth] = useState(0);
  useIsoLayoutEffect(() => {
    const el = ref.current;
    if (!enabled || !el) return undefined;
    const read = () => setWidth(el.clientWidth || 0);
    read();
    if (typeof ResizeObserver !== 'function') return undefined;
    const ro = new ResizeObserver(read);
    ro.observe(el);
    return () => ro.disconnect();
  }, [enabled]);
  return width;
}

/** Riga misurata: comunica la sua altezza a chi la dispone. */
function MeasuredRow({ index, style, onSize, children }) {
  const ref = useRef(null);
  useIsoLayoutEffect(() => {
    const el = ref.current;
    if (!el) return undefined;
    const report = () => onSize(index, el.offsetHeight);
    report();
    if (typeof ResizeObserver !== 'function') return undefined;
    const ro = new ResizeObserver(report);
    ro.observe(el);
    return () => ro.disconnect();
  }, [index, onSize]);
  return (
    <div style={style}>
      <div ref={ref}>{children}</div>
    </div>
  );
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
  label,
}) {
  const list = items || [];
  const virtual = list.length > threshold;
  const containerRef = useRef(null);
  const listRef = useRef(null);
  const sizesRef = useRef(new Map());
  const width = useWidth(containerRef, virtual);
  const columns = minColumnWidth > 0 && width > 0
    ? Math.max(1, Math.floor((width + gap) / (minColumnWidth + gap)))
    : 1;
  const rows = useMemo(() => {
    if (!virtual) return [];
    const out = [];
    for (let i = 0; i < list.length; i += columns) out.push(list.slice(i, i + columns));
    return out;
  }, [list, columns, virtual]);

  // Cambiando colonne le righe sono altre: le misure ripartono.
  useEffect(() => { sizesRef.current = new Map(); listRef.current?.resetAfterIndex(0, true); }, [columns]);

  const rowEstimate = estimatedItemHeight + gap;
  const sizeOf = useCallback(index => sizesRef.current.get(index) ?? rowEstimate, [rowEstimate]);
  const onSize = useCallback((index, height) => {
    const next = height + gap;
    if (!height || sizesRef.current.get(index) === next) return;
    sizesRef.current.set(index, next);
    listRef.current?.resetAfterIndex(index, true);
  }, [gap]);

  if (!virtual) {
    const plain = list.map((item, i) => <React.Fragment key={itemKey(item, i)}>{renderItem(item, i)}</React.Fragment>);
    // Senza classe: gli elementi entrano nel contenitore di chi chiama.
    return className ? <div className={className}>{plain}</div> : <>{plain}</>;
  }

  const total = rows.reduce((sum, _, i) => sum + sizeOf(i), 0);
  const gridStyle = minColumnWidth > 0
    ? { display: 'grid', gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))`, gap: `${gap}px` }
    : undefined;
  return (
    <div ref={containerRef} className="vlist" aria-label={label}>
      <VariableSizeList
        ref={listRef}
        height={Math.min(maxHeight, total)}
        width="100%"
        itemCount={rows.length}
        itemSize={sizeOf}
        estimatedItemSize={rowEstimate}
        overscanCount={3}
        itemKey={index => itemKey(rows[index][0], index * columns)}
      >
        {({ index, style }) => (
          <MeasuredRow index={index} style={style} onSize={onSize}>
            <div className={rowClassName} style={gridStyle}>
              {rows[index].map((item, j) => (
                <React.Fragment key={itemKey(item, index * columns + j)}>
                  {renderItem(item, index * columns + j)}
                </React.Fragment>
              ))}
            </div>
          </MeasuredRow>
        )}
      </VariableSizeList>
    </div>
  );
}
