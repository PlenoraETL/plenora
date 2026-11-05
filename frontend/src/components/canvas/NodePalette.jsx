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
import useDialog from '../../hooks/useDialog';
import { displayIcon } from './nodeDescriptors';
import './NodePalette.css';

const norm = (s) => String(s || '').toLowerCase();

/**
 * Quanto un elemento corrisponde alla ricerca (0 = no). Conta prima il
 * NOME: "filtra" deve proporre "Filtra" prima di "Campiona", che sta nel
 * gruppo Filtra solo per scopo.
 */
export function matchScore(item, groupLabel, q) {
  const label = norm(item.label);
  if (label === q) return 100;
  if (label.startsWith(q)) return 90;
  if (label.split(/[\s()/_-]+/).some(w => w.startsWith(q))) return 80;
  if (label.includes(q)) return 70;
  if (norm(item.aka).includes(q)) return 60;
  if (norm(item.id).includes(q)) return 50;
  if (norm(item.desc).includes(q)) return 30;
  if (norm(groupLabel).includes(q)) return 10;
  return 0;
}

/**
 * Gruppi da mostrare per una ricerca. Senza testo: i gruppi dell'editor.
 * Con testo: un solo elenco "Risultati" ordinato per matchScore (a parita',
 * l'ordine dell'editor), senza doppioni e senza il gruppo "Più usate".
 */
export function filterPalette(groups, query) {
  const q = norm(query).trim();
  if (!q) {
    return groups
      .map(g => ({ ...g, items: g.items.slice(0, g.limit || 60) }))
      .filter(g => g.items.length > 0);
  }
  const seen = new Set();
  const scored = [];
  groups.filter(g => !g.onlyWhenEmpty).forEach((g, gi) => g.items.forEach((item, ii) => {
    const key = `${item.kind || ''}:${item.id}`;
    const score = matchScore(item, g.label, q);
    if (score > 0 && !seen.has(key)) { seen.add(key); scored.push({ item, score, order: gi * 10000 + ii }); }
  }));
  scored.sort((a, b) => b.score - a.score || a.order - b.order);
  const items = scored.slice(0, 60).map(x => x.item);
  return items.length ? [{ label: 'Risultati', items }] : [];
}

export default function NodePalette({ open, context, groups = [], onPick, onClose }) {
  const [query, setQuery] = useState('');
  const [active, setActive] = useState(0);
  const listRef = useRef(null);
  // Finestra dello stack unico (N14): Esc, Tab, focus e ritorno del focus.
  const dialog = useDialog({ isOpen: open, onClose, labelledBy: false });

  useEffect(() => {
    if (open) { setQuery(''); setActive(0); }
  }, [open]);

  const filtered = useMemo(() => filterPalette(groups, query), [groups, query]);

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
  };

  let index = -1;
  return (
    <div className="npal__scrim" {...dialog.overlayProps}>
      <div ref={dialog.ref} className="npal" {...dialog.dialogProps} aria-label="Aggiungi un passo">
        <div className="npal__search">
          <span aria-hidden="true">🔎</span>
          <input
            autoFocus
            value={query}
            onChange={e => { setQuery(e.target.value); setActive(0); }}
            onKeyDown={onKeyDown}
            placeholder="Cosa vuoi fare? es. filtra, rinomina, Join, raggruppa…"
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
                    <span className="npal__icon" aria-hidden="true">{displayIcon(item.icon)}</span>
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
