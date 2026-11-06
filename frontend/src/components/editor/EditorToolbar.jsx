/**
 * EditorToolbar — barra unica dei tre editor (Fase 4, M7), come il mockup:
 *   logo · Indietro · titolo · tipo · stato del salvataggio
 *   ……  Canvas | Passi · ＋ Passo · [stato extra] · ▶ Esegui · 💾 Salva · ⋯
 * Il menu "⋯" raccoglie le azioni dell'editor (input runtime, valori
 * statici, nota, versioni, caricamenti, ...) e il tema.
 *
 * Salvataggio esplicito (scelta dell'utente, 03/11/2026): quello che gira
 * (Scheduler, Eseguibili) e' sempre l'ultima versione SALVATA. L'indicatore
 * dice sempre come stanno le cose; Ctrl+S salva (gestito da EditorShell).
 */
import React, { useEffect, useRef, useState } from 'react';
import useCanvasStore from '../../store/useCanvasStore';
import { hasUnsavedChanges } from '../../store/saveState';
import PlenoraLogo from '../common/PlenoraLogo';
import { ColumnSyncBadge } from '../common/ColumnStatusNote';
import { addFirstMessage, term } from '../../utils/glossary';
import { serverActionLabel } from '../../utils/serverActions';
import { useOptionalToast } from '../../context/ToastContext';
import { sourceFreshnessRows } from './SourceFreshnessNote';
import { commandById, commandMenuItems, runCanvasCommand } from './canvasCommands';
import { isShortcutBlocked } from '../../utils/dialogStack';
import { matchesShortcut } from '../../utils/shortcuts';
import ShortcutsButton from '../common/ShortcutsButton';
import './EditorToolbar.css';
import { useCanvasDerived } from '../../hooks/useCanvasDerived';

// U18: c'e' un progetto sorgente (dal grafo solo con useCanvasDerived, UI-2).
const hasProjectSourceNodes = (s) => (s.nodes || []).some(n => n.type === 'project_source' && n.data?.source_project_id);

const MODE_LABEL = { estrai: 'ESTRAI', manipola: 'MANIPOLA', arricchisci: 'ARRICCHISCI' };

/** "Tutto salvato" / "Modifiche non salvate" / "Salvataggio…" / "Errore: riprova".
 *  Un solo contenitore role="status" (annunciato a ogni cambio di stato). */
export function SaveIndicator({ onRetry }) {
  const dirty = useCanvasStore(hasUnsavedChanges);
  const status = useCanvasStore(s => s.saveStatus);
  const error = useCanvasStore(s => s.saveError);
  const isReadOnly = useCanvasStore(s => s.isReadOnly);
  let content;
  if (isReadOnly) content = <span className="etb-save etb-save--ro">Sola lettura</span>;
  else if (status === 'saving') content = <span className="etb-save etb-save--saving"><i aria-hidden="true" />Salvataggio…</span>;
  else if (status === 'error') {
    content = (
      <button type="button" className="etb-save etb-save--error" title={error || ''} onClick={onRetry}
        aria-describedby="etb-save-error">
        <i aria-hidden="true" />Salvataggio non riuscito · riprova
        <span id="etb-save-error" className="etb-sr-only">{error || ''}</span>
      </button>
    );
  } else if (dirty) content = <span className="etb-save etb-save--dirty"><i aria-hidden="true" />Modifiche non salvate</span>;
  else content = <span className="etb-save etb-save--ok"><i aria-hidden="true" />Tutto salvato</span>;
  return <span className="etb-save-wrap" role="status" aria-live="polite">{content}</span>;
}

/** Menu "⋯": apre col fuoco sulla prima voce, frecce/Home/Fine per muoversi,
 *  Esc o una scelta lo chiudono e il fuoco torna al pulsante. */
function OverflowMenu({ items }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  const buttonRef = useRef(null);
  const listRef = useRef(null);
  const close = (refocus = true) => {
    setOpen(false);
    if (refocus) buttonRef.current?.focus();
  };
  useEffect(() => {
    if (!open) return undefined;
    listRef.current?.querySelector('[role="menuitem"]:not(:disabled)')?.focus();
    const outside = (e) => { if (!ref.current?.contains(e.target)) setOpen(false); };
    // Esc chiude il menu, non una finestra aperta sopra (regola unica, utils/dialogStack).
    const esc = (e) => { if (matchesShortcut('close', e) && !isShortcutBlocked(e)) { e.preventDefault(); setOpen(false); buttonRef.current?.focus(); } };
    document.addEventListener('mousedown', outside);
    document.addEventListener('keydown', esc);
    return () => { document.removeEventListener('mousedown', outside); document.removeEventListener('keydown', esc); };
  }, [open]);
  const onListKey = (e) => {
    const all = [...(listRef.current?.querySelectorAll('[role="menuitem"]:not(:disabled)') || [])];
    if (!all.length) return;
    const i = all.indexOf(document.activeElement);
    const go = { ArrowDown: i + 1, ArrowUp: i - 1, Home: 0, End: all.length - 1 }[e.key];
    if (go === undefined) return;
    e.preventDefault();
    all[(go + all.length) % all.length].focus();
  };
  // `items` puo' essere una funzione: le voci si calcolano all'apertura
  // (comandi del canvas: dipendono dalla selezione e dagli appunti).
  const visible = (typeof items === 'function' ? items() : items).filter(i => i && !i.hidden);
  if (!visible.length) return null;
  return (
    <div className="etb-menu" ref={ref}>
      <button ref={buttonRef} type="button" className="etb-btn etb-btn--icon" aria-haspopup="menu" aria-expanded={open}
        aria-label="Altre azioni" title="Altre azioni" onClick={() => setOpen(o => !o)}>⋯</button>
      {open && (
        <div className="etb-menu__list" role="menu" ref={listRef} onKeyDown={onListKey}>
          {visible.map(item => (item.divider
            ? <div key={item.key} className="etb-menu__divider" role="separator" />
            : (
              <button key={item.key} type="button" role="menuitem" className="etb-menu__item"
                disabled={item.disabled} title={(item.disabled && item.reason) || item.title}
                onClick={() => { close(); item.onClick?.(); }}>
                <span aria-hidden="true">{item.icon}</span>
                <span className="etb-menu__text">
                  {item.label}
                  {/* E3: una voce spenta dice perche', scritto (non solo nel tooltip). */}
                  {item.disabled && item.reason && <small className="etb-menu__reason">{item.reason}</small>}
                </span>
              </button>
            )))}
        </div>
      )}
    </div>
  );
}

export default function EditorToolbar({
  mode, title, onBack, leftExtra, statusExtra,
  onSave, saving, onAddStep, executing = false,
  // Fase 5 T2 (R9.8): la finestra di esecuzione chiusa ("Continua in secondo
  // piano") si riapre da qui, mentre gira o con un esito non ancora visto.
  onShowExecution = null, executionUnseen = false,
  menuItems = [], theme, setTheme,
}) {
  const isReadOnly = useCanvasStore(s => s.isReadOnly);
  const toast = useOptionalToast();
  const canvasView = useCanvasStore(s => s.canvasView);
  const canUndo = useCanvasStore(s => (s.past?.length ?? 0) > 0);
  const canRedo = useCanvasStore(s => (s.future?.length ?? 0) > 0);
  const setCanvasView = useCanvasStore(s => s.setCanvasView);
  const runState = useCanvasStore(s => s.runState);
  // Esegui solo con l'Output montato (e' lui il percorso unico di esecuzione).
  const runReady = useCanvasStore(s => s.runReady);
  // UI-2 (V5): riletti solo quando cambiano struttura o selezione.
  const hasOutput = useCanvasDerived(s => s.nodes.some(n => n.type === 'result' || n.type === 'resultNode'), { on: 'structure' });
  const selectedId = useCanvasDerived(s => {
    const sel = s.nodes.filter(n => n.selected);
    return sel.length === 1 ? sel[0].id : null;
  }, { on: 'selection' });
  const busy = runState === 'starting' || runState === 'running' || executing;
  const unsaved = useCanvasStore(hasUnsavedChanges);
  const recalc = useCanvasStore(s => Boolean(s.runOptions?.force_recalculate));
  const hasProjectSources = useCanvasDerived(hasProjectSourceNodes);
  const freshnessText = hasProjectSources
    ? sourceFreshnessRows(useCanvasStore.getState().nodes).map(r => `${r.name}: ${r.text}`).join('\n')
    : '';
  // Comandi del canvas (UI-3, U27) e guida (U30) nel menu: calcolati
  // all'apertura, con i nomi e i tasti della tabella unica.
  const commandItems = () => {
    const onRun = (id) => {
      const res = runCanvasCommand(id);
      if (res.message && toast) (res.ok ? toast.success : toast.warning)(res.message);
    };
    return [
      { key: 'cmd-div', divider: true },
      ...commandMenuItems(useCanvasStore.getState(), onRun),
      { key: 'help', icon: '📘', label: `${commandById('help').name} (F1)`, title: 'Guida, aperta sul nodo selezionato', onClick: () => onRun('help') },
    ];
  };
  // Gli errori del percorso di esecuzione sono mostrati da ResultNode
  // (finestra di validazione): qui non resta nessuna promessa rifiutata.
  const run = () => Promise.resolve(useCanvasStore.getState().getEditorAction('runPipeline')?.()).catch(err => {
    console.error('[EditorToolbar] Esegui:', err);
  });

  const themeItems = setTheme ? [
    { key: 'theme-div', divider: true },
    {
      key: 'theme', icon: theme === 'dark' ? '☀️' : '🌙',
      label: theme === 'dark' ? 'Tema chiaro' : 'Tema scuro',
      onClick: () => setTheme(theme === 'dark' ? 'light' : 'dark'),
    },
  ] : [];

  return (
    <header className={`etb etb--${mode}`}>
      <div className="etb__left">
        <PlenoraLogo gridLines="left" plenClassName="etb__logo-text" />
        <button type="button" className="etb-btn etb-btn--ghost" onClick={onBack}>← Indietro</button>
        <h2 className="etb__title" title={title}>{title}</h2>
        <span className={`etb__mode etb__mode--${mode}`}>{MODE_LABEL[mode] || mode}</span>
        {leftExtra}
        <SaveIndicator onRetry={onSave} />
        {/* P29 (Fase 5 T7): Annulla/Ripeti anche col mouse (stesse azioni di Ctrl+Z/Y). */}
        {!isReadOnly && (
          <div className="etb-seg" role="group" aria-label="Cronologia delle modifiche">
            <button type="button" onClick={() => useCanvasStore.getState().undo()} disabled={!canUndo} title="Annulla (Ctrl+Z)" aria-label="Annulla">↶</button>
            <button type="button" onClick={() => useCanvasStore.getState().redo()} disabled={!canRedo} title="Ripeti (Ctrl+Y)" aria-label="Ripeti">↷</button>
          </div>
        )}
      </div>
      <div className="etb__right">
        <div className="etb-seg" role="group" aria-label="Vista">
          <button type="button" aria-pressed={canvasView === 'canvas'} onClick={() => setCanvasView('canvas', selectedId)}>
            <span aria-hidden="true">◇ </span>Canvas
          </button>
          <button type="button" aria-pressed={canvasView === 'steps'} onClick={() => setCanvasView('steps', selectedId)}>
            <span aria-hidden="true">☰ </span>Passi
          </button>
        </div>
        {onAddStep && !isReadOnly && (
          <button type="button" className="etb-btn" onClick={onAddStep} title={term('add_step').description}>
            {term('add_step').icon} {term('add_step').name}
          </button>
        )}
        {/* Stato della fonte unica delle colonne: uguale nei tre editor. */}
        <ColumnSyncBadge />
        {statusExtra}
        {/* U18 (UI-3): da dove l'esecuzione prende i dati delle sorgenti,
            visibile e cambiabile qui (prima solo nel pannello dell'Output). */}
        {hasProjectSources && mode !== 'estrai' && !isReadOnly && (
          <button type="button" className="etb-btn etb-btn--ghost" aria-pressed={recalc}
            title={`${recalc ? 'Ricalcola anche i progetti sorgente' : 'Usa l\'ultimo risultato salvato di ogni sorgente'}. Clic per cambiare.\n${freshnessText}`}
            onClick={() => useCanvasStore.getState().setRunOptions?.({ force_recalculate: !recalc })}>
            {recalc ? '🔄 Ricalcola tutto' : '⚡ Risultati salvati'}
          </button>
        )}
        {/* U12 (UI-3): perche' Esegui e' spento, scritto accanto. */}
        {!hasOutput && <span className="etb__run-hint">{addFirstMessage('output')} per eseguire</span>}
        <button
          type="button"
          className="etb-btn etb-btn--primary"
          onClick={run}
          disabled={!hasOutput || !runReady || busy}
          title={hasOutput ? term('run').description : `${addFirstMessage('output')} per poter eseguire`}
        >
          {/* Regola unica (U15): con modifiche non salvate «Salva ed esegui». */}
          <span aria-hidden="true">{busy ? '⏳ ' : `${term('run').icon} `}</span>{busy ? term('run').busyName : serverActionLabel('run', unsaved)}
        </button>
        {onShowExecution && (
          <button type="button" className="etb-btn" onClick={() => onShowExecution()}>
            {executionUnseen ? "Vedi l'esito" : "Mostra l'esecuzione"}
          </button>
        )}
        {!isReadOnly && onSave && (
          <button type="button" className="etb-btn" onClick={onSave} disabled={saving} title={term('save').description}>
            {saving ? term('save').busyName : <><span aria-hidden="true">{term('save').icon} </span>{term('save').name}</>}
          </button>
        )}
        <OverflowMenu items={() => [...menuItems, ...commandItems(), ...themeItems]} />
        <ShortcutsButton className="etb-btn etb-btn--icon" />
      </div>
    </header>
  );
}
