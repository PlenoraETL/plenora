/**
 * EditorPanels — barra laterale richiudibile e pannello del passo
 * ridimensionabile dei tre editor (UI-4, U26). A 1366×768 la barra (260 px)
 * e il pannello (400 px) lasciavano al canvas meno di metà schermo.
 *
 *  - SidePanelFrame: avvolge la barra delle sorgenti; ««» la chiude a una
 *    striscia con «»» per riaprirla. Il contenuto resta montato (ricerca e
 *    sezioni aperte non si perdono).
 *  - PanelResizer: maniglia tra canvas e pannello del passo; trascinandola
 *    (o con ← → quando ha il fuoco) cambia la larghezza del pannello, tra
 *    PANEL_WIDTH.min e PANEL_WIDTH.max. Senza pannello aperto non si vede
 *    (CSS: e' l'ultimo figlio del contenitore).
 * Le scelte restano in questo browser (hooks/useStoredState).
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useStoredState, LAYOUT_KEYS } from '../../hooks/useStoredState';
import './EditorPanels.css';

const PANEL_WIDTH = Object.freeze({ min: 300, max: 720, initial: 400, step: 20 });

const asBoolean = (v) => v === true;
const clampWidth = (v) => {
  const n = Number(v);
  if (!Number.isFinite(n)) return PANEL_WIDTH.initial;
  return Math.min(PANEL_WIDTH.max, Math.max(PANEL_WIDTH.min, Math.round(n)));
};

export function SidePanelFrame({ children }) {
  const [collapsed, setCollapsed] = useStoredState(LAYOUT_KEYS.editorSidebarCollapsed, false, asBoolean);
  return (
    <div className={`editor-side-frame${collapsed ? ' editor-side-frame--collapsed' : ''}`}>
      <div className="editor-side-frame__content" hidden={collapsed}>{children}</div>
      <button
        type="button"
        className="editor-side-frame__toggle"
        onClick={() => setCollapsed(c => !c)}
        aria-expanded={!collapsed}
        title={collapsed ? 'Mostra le sorgenti' : 'Nascondi le sorgenti (più spazio al canvas)'}
        aria-label={collapsed ? 'Mostra le sorgenti' : 'Nascondi le sorgenti'}
      >
        {collapsed ? '»' : '«'}
      </button>
    </div>
  );
}

/*
 * Trascinamento (UI conferma C-K8): UN gesto = UNA scrittura in memoria del
 * browser. Durante il gesto la larghezza vive solo nello stato della pagina
 * (`dragWidth`); si ricorda alla fine, qualunque sia la fine: rilascio
 * (`pointerup`), gesto interrotto dal sistema (`pointercancel`: tocco preso
 * dallo scorrimento, finestra che perde il fuoco) o cattura persa
 * (`lostpointercapture`). Il puntatore e' catturato dalla maniglia
 * (`setPointerCapture`): i movimenti arrivano anche fuori dalla maniglia, e
 * smontandola a meta' gesto non resta nessun ascoltatore.
 */
export function PanelResizer() {
  const [width, setWidth] = useStoredState(LAYOUT_KEYS.editorPanelWidth, PANEL_WIDTH.initial, clampWidth);
  const [dragWidth, setDragWidth] = useState(null);
  const ref = useRef(null);
  const gestureRef = useRef(null); // { pointerId, startX, startW, last }
  const shown = dragWidth ?? width;

  // La larghezza arriva al pannello come variabile CSS sul contenitore.
  useEffect(() => {
    const host = ref.current?.parentElement;
    host?.style.setProperty('--node-inspector-width', `${shown}px`);
  }, [shown]);

  const onPointerDown = useCallback((e) => {
    e.preventDefault();
    gestureRef.current = { pointerId: e.pointerId, startX: e.clientX, startW: width, last: width };
    try { e.currentTarget.setPointerCapture?.(e.pointerId); } catch { /* puntatore gia' rilasciato */ }
    setDragWidth(width);
  }, [width]);

  const onPointerMove = useCallback((e) => {
    const g = gestureRef.current;
    if (!g || (g.pointerId != null && e.pointerId != null && e.pointerId !== g.pointerId)) return;
    g.last = clampWidth(g.startW + (g.startX - e.clientX));
    setDragWidth(g.last);
  }, []);

  const endGesture = useCallback(() => {
    const g = gestureRef.current;
    if (!g) return;
    gestureRef.current = null;
    setDragWidth(null);
    if (g.last !== g.startW) setWidth(g.last);
  }, [setWidth]);

  const onKeyDown = (e) => {
    if (e.key === 'ArrowLeft') { e.preventDefault(); setWidth(w => w + PANEL_WIDTH.step); }
    else if (e.key === 'ArrowRight') { e.preventDefault(); setWidth(w => w - PANEL_WIDTH.step); }
  };

  return (
    <div
      ref={ref}
      className="editor-panel-resizer"
      role="separator"
      aria-orientation="vertical"
      aria-label="Larghezza del pannello del passo"
      aria-valuemin={PANEL_WIDTH.min}
      aria-valuemax={PANEL_WIDTH.max}
      aria-valuenow={shown}
      tabIndex={0}
      title="Trascina per allargare o stringere il pannello"
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={endGesture}
      onPointerCancel={endGesture}
      onLostPointerCapture={endGesture}
      onKeyDown={onKeyDown}
    />
  );
}
