/**
 * NodePalette — "Cosa vuoi fare?" (Fase 4, M2). Ricerca con tastiera su
 * gruppi di elementi forniti dall'editor (operazioni per scopo in
 * Manipola, tabelle/viste in Estrai, connettori in Arricchisci).
 *
 * L'editor passa `groups` ([{label, items:[{id, label, icon, desc}]}]) e
 * `onPick(item, afterNodeId)`: il nuovo passo nasce dopo il nodo da cui
 * e' stata aperta la palette, gia' collegato.
 */
import React, { useEffect, useMemo, useRef, useState } from 'react';
import './NodePalette.css';

const norm = (s) => String(s || '').toLowerCase();

export default function NodePalette({ open, context, groups = [], onPick, onClose }) {
  const [query, setQuery] = useState('');
  const [active, setActive] = useState(0);
  const inputRef = useRef(null);
  const listRef = useRef(null);

  useEffect(() => {
    if (open) { setQuery(''); setActive(0); setTimeout(() => inputRef.current?.focus(), 0); }
  }, [open]);

  const filtered = useMemo(() => {
    const q = norm(query).trim();
    return groups
      .map(g => ({
        ...g,
        items: (q
          ? g.items.filter(i => `${norm(i.label)} ${norm(i.desc)} ${norm(i.id)} ${norm(g.label)}`.includes(q))
          : g.items).slice(0, g.limit || 60),
      }))
      .filter(g => g.items.length > 0 && (!q || !g.onlyWhenEmpty));
  }, [groups, query]);

  const flat = useMemo(() => filtered.flatMap(g => g.items), [filtered]);

  useEffect(() => {
    listRef.current?.querySelector('.npal__item--active')?.scrollIntoView?.({ block: 'nearest' });
  }, [active]);

  if (!open) return null;

  const pick = (item) => { if (item) { onPick(item); onClose(); } };
  const onKeyDown = (e) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); setActive(a => Math.min(flat.length - 1, a + 1)); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setActive(a => Math.max(0, a - 1)); }
    else if (e.key === 'Enter') { e.preventDefault(); pick(flat[active]); }
    else if (e.key === 'Escape') { e.preventDefault(); onClose(); }
  };

  let index = -1;
  return (
    <div className="npal__scrim" onMouseDown={e => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="npal" role="dialog" aria-modal="true" aria-label="Aggiungi un passo">
        <div className="npal__search">
          <span aria-hidden="true">🔎</span>
          <input
            ref={inputRef}
            value={query}
            onChange={e => { setQuery(e.target.value); setActive(0); }}
            onKeyDown={onKeyDown}
            placeholder="Cosa vuoi fare? es. filtra, rinomina, join, buffer…"
            aria-label="Cerca un'operazione"
          />
        </div>
        {context && <div className="npal__context">Dopo <b>{context}</b></div>}
        <div className="npal__list" ref={listRef}>
          {flat.length === 0 && <div className="npal__empty">Nessun risultato</div>}
          {filtered.map(g => (
            <div key={g.label}>
              <div className="npal__group">{g.label}</div>
              {g.items.map(item => {
                index += 1;
                const i = index;
                return (
                  <div
                    key={`${g.label}:${item.id}`}
                    className={`npal__item${i === active ? ' npal__item--active' : ''}`}
                    onMouseMove={() => { if (i !== active) setActive(i); }}
                    onClick={() => pick(item)}
                    role="option"
                    aria-selected={i === active}
                  >
                    <span className="npal__icon" aria-hidden="true">{item.icon || '⚙️'}</span>
                    <span className="npal__label">{item.label}</span>
                    {item.desc && <span className="npal__desc">{item.desc}</span>}
                  </div>
                );
              })}
            </div>
          ))}
        </div>
        <div className="npal__foot"><kbd>↑</kbd><kbd>↓</kbd> scegli · <kbd>Invio</kbd> aggiungi · <kbd>Esc</kbd> chiudi</div>
      </div>
    </div>
  );
}
