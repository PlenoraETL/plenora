/**
 * ActionMenu — il menu «⋯» delle azioni secondarie (Fase 5 T7, G3, DB1, S7).
 *
 * Regola G3: un'azione distruttiva (eliminare, svuotare) non sta MAI a vista
 * accanto all'azione primaria: vive qui, separata dalle altre da una linea,
 * in rosso, e chi la chiama chiede comunque conferma (ConfirmDialog).
 *
 *   <ActionMenu label="Azioni del progetto" items={[
 *     { label: 'Rinomina', onSelect: rename },
 *     { label: 'Elimina', onSelect: remove, danger: true },
 *   ]} />
 *
 * Voci nulle o false si saltano (permessi). Tastiera: Invio/Spazio apre,
 * Esc chiude e torna al pulsante, frecce tra le voci.
 */
import React, { useEffect, useId, useRef, useState } from 'react';
import './ActionMenu.css';

export default function ActionMenu({ items, label = 'Altre azioni', disabled = false, className = '' }) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef(null);
  const buttonRef = useRef(null);
  const menuId = useId();
  const visible = (items || []).filter(Boolean);
  const safe = visible.filter(i => !i.danger);
  const danger = visible.filter(i => i.danger);

  useEffect(() => {
    if (!open) return undefined;
    const onDown = (e) => { if (rootRef.current && !rootRef.current.contains(e.target)) setOpen(false); };
    document.addEventListener('mousedown', onDown);
    rootRef.current?.querySelector('[role="menuitem"]')?.focus();
    return () => document.removeEventListener('mousedown', onDown);
  }, [open]);

  if (visible.length === 0) return null;

  const close = () => { setOpen(false); buttonRef.current?.focus(); };
  const onKeyDown = (e) => {
    if (!open) return;
    if (e.key === 'Escape') { e.stopPropagation(); close(); return; }
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      const entries = [...rootRef.current.querySelectorAll('[role="menuitem"]:not(:disabled)')];
      const i = entries.indexOf(document.activeElement);
      const next = e.key === 'ArrowDown' ? (i + 1) % entries.length : (i - 1 + entries.length) % entries.length;
      entries[next]?.focus();
    }
  };
  const choose = (item) => (e) => {
    e.stopPropagation();
    setOpen(false);
    item.onSelect?.();
  };
  const renderItem = (item) => (
    <button
      key={item.label}
      type="button"
      role="menuitem"
      className={`plen-menu__item${item.danger ? ' plen-menu__item--danger' : ''}`}
      onClick={choose(item)}
      disabled={item.disabled}
      title={item.title}
    >
      {item.label}
    </button>
  );

  return (
    <div
      ref={rootRef}
      className={`plen-menu ${className}`}
      onKeyDown={onKeyDown}
      onClick={(e) => e.stopPropagation()}
    >
      <button
        ref={buttonRef}
        type="button"
        className="plen-menu__toggle"
        aria-label={label}
        title={label}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        disabled={disabled}
        onClick={() => setOpen(o => !o)}
      >
        ⋯
      </button>
      {open && (
        <div id={menuId} role="menu" aria-label={label} className="plen-menu__list">
          {safe.map(renderItem)}
          {safe.length > 0 && danger.length > 0 && <div role="separator" className="plen-menu__sep" />}
          {danger.map(renderItem)}
        </div>
      )}
    </div>
  );
}
