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
import React, { useCallback, useEffect, useRef } from 'react';
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

export function PanelResizer() {
  const [width, setWidth] = useStoredState(LAYOUT_KEYS.editorPanelWidth, PANEL_WIDTH.initial, clampWidth);
  const ref = useRef(null);

  // La larghezza arriva al pannello come variabile CSS sul contenitore.
  useEffect(() => {
    const host = ref.current?.parentElement;
    host?.style.setProperty('--node-inspector-width', `${width}px`);
  }, [width]);

  const onPointerDown = useCallback((e) => {
    e.preventDefault();
    const startX = e.clientX;
    const startW = width;
    const move = (ev) => setWidth(startW + (startX - ev.clientX));
    const up = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  }, [width, setWidth]);

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
      aria-valuenow={width}
      tabIndex={0}
      title="Trascina per allargare o stringere il pannello"
      onPointerDown={onPointerDown}
      onKeyDown={onKeyDown}
    />
  );
}
