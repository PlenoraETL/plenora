/**
 * NodePalette — "Cosa vuoi fare?" (Fase 4, M2). Ricerca con tastiera su
 * gruppi di elementi forniti dall'editor (operazioni per scopo in
 * Manipola, tabelle/viste in Estrai, connettori in Arricchisci, sorgenti e
 * uscite in tutti; UI-3: anche la ricerca nel canvas e «Collega a…»).
 *
 * L'editor passa `groups` ([{label, items:[{id, label, icon, desc}]}]) e
 * `onPick(item, afterNodeId)`: il nuovo passo nasce dopo il nodo da cui
 * e' stata aperta la palette, gia' collegato.
 *
 * UI-3 (06/11/2026):
 *  - U19: niente elenchi tagliati in silenzio. Un gruppo oltre il suo
 *    `limit` (e i risultati oltre RESULTS_MAX) dice quante voci restano e
 *    che la ricerca le trova; `keywords` (es. le colonne di una tabella)
 *    entrano nella ricerca.
 *  - U29: con `scope` la palette ricorda preferiti (★) e recenti per
 *    editor (paletteMemory); i recenti sostituiscono «Più usate» (gruppo
 *    con `fallback: true`) appena ce ne sono.
 */
import React, { useEffect, useMemo, useRef, useState } from 'react';
import useDialog from '../../hooks/useDialog';
import { displayIcon } from './nodeDescriptors';
import { itemKey, readPaletteMemory, memoryGroups, rememberPick, toggleFavorite } from './paletteMemory';
import './NodePalette.css';
import '../common/ListState.css';
import { pressable } from '../../utils/pressable';
import { formatNumber } from '../../utils/format';

const norm = (s) => String(s || '').toLowerCase();
const RESULTS_MAX = 60;
const GROUP_LIMIT = 60;

/**
 * Quanto un elemento corrisponde alla ricerca (0 = no). Conta prima il
 * NOME: "filtra" deve proporre "Filtra" prima di "Campiona", che sta nel
 * gruppo Filtra solo per scopo.
 */
function matchScore(item, groupLabel, q) {
  const label = norm(item.label);
  if (label === q) return 100;
  if (label.startsWith(q)) return 90;
  if (label.split(/[\s()/_-]+/).some(w => w.startsWith(q))) return 80;
  if (label.includes(q)) return 70;
  if (norm(item.aka).includes(q)) return 60;
  if (norm(item.id).includes(q)) return 50;
  if (norm(item.desc).includes(q)) return 30;
  if ((item.keywords || []).some(k => norm(k).includes(q))) return 25;
  if (norm(groupLabel).includes(q)) return 10;
  return 0;
}

/**
 * Gruppi da mostrare per una ricerca. Senza testo: i gruppi dell'editor
 * (ognuno fino al suo `limit`, con `hidden` = voci non mostrate). Con
 * testo: un solo elenco "Risultati" ordinato per matchScore (a parita',
 * l'ordine dell'editor), senza doppioni e senza i gruppi `onlyWhenEmpty`.
 */
export function filterPalette(groups, query) {
  const q = norm(query).trim();
  if (!q) {
    return groups
      .map(g => {
        const limit = g.limit || GROUP_LIMIT;
        return { ...g, items: g.items.slice(0, limit), hidden: Math.max(0, g.items.length - limit) };
      })
      .filter(g => g.items.length > 0);
  }
  const seen = new Set();
  const scored = [];
  groups.filter(g => !g.onlyWhenEmpty).forEach((g, gi) => g.items.forEach((item, ii) => {
    const key = itemKey(item);
    const score = matchScore(item, g.label, q);
    if (score > 0 && !seen.has(key)) { seen.add(key); scored.push({ item, score, order: gi * 100000 + ii }); }
  }));
  scored.sort((a, b) => b.score - a.score || a.order - b.order);
  const items = scored.slice(0, RESULTS_MAX).map(x => x.item);
  return items.length ? [{ label: 'Risultati', items, hidden: scored.length - items.length, searching: true }] : [];
}

/** Gruppi con preferiti e recenti in cima (solo senza ricerca). */
function withMemory(groups, memory) {
  const remembered = memoryGroups(groups, memory);
  const hasRecent = remembered.some(g => g.label === 'Recenti');
  return [...remembered, ...groups.filter(g => !(g.fallback && hasRecent))];
}

// `notice` (Fase 5 T2, N13): { message, onRetry } quando parte del catalogo
// non e' arrivata; la palette lo dice con «Riprova» invece di restare vuota.
export default function NodePalette({
  open, context, groups = [], onPick, onClose, notice = null,
  // UI-3: la stessa finestra serve anche a cercare nel canvas e a collegare.
  placeholder = 'Cosa vuoi fare? es. filtra, rinomina, Join, raggruppa…',
  ariaLabel = 'Aggiungi un passo',
  searchLabel = "Cerca un'operazione",
  scope = null,
  pickVerb = 'aggiungi',
}) {
  const [query, setQuery] = useState('');
  const [active, setActive] = useState(0);
  const [memory, setMemory] = useState(() => readPaletteMemory(scope));
  const listRef = useRef(null);
  // Finestra dello stack unico (N14): Esc, Tab, focus e ritorno del focus.
  const dialog = useDialog({ isOpen: open, onClose, labelledBy: false });

  useEffect(() => {
    if (open) { setQuery(''); setActive(0); setMemory(readPaletteMemory(scope)); }
  }, [open, scope]);

  const shownGroups = useMemo(() => (scope ? withMemory(groups, memory) : groups), [groups, memory, scope]);
  const filtered = useMemo(() => filterPalette(shownGroups, query), [shownGroups, query]);

  const flat = useMemo(() => filtered.flatMap(g => g.items), [filtered]);

  useEffect(() => {
    listRef.current?.querySelector('.npal__item--active')?.scrollIntoView?.({ block: 'nearest' });
  }, [active]);

  if (!open) return null;

  const pick = (item) => {
    if (!item) return;
    if (scope) rememberPick(scope, item);
    onPick(item);
    onClose();
  };
  const onKeyDown = (e) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); setActive(a => Math.min(flat.length - 1, a + 1)); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setActive(a => Math.max(0, a - 1)); }
    else if (e.key === 'Enter') { e.preventDefault(); pick(flat[active]); }
  };

  let index = -1;
  return (
    <div className="npal__scrim" {...dialog.overlayProps}>
      <div ref={dialog.ref} className="npal" {...dialog.dialogProps} aria-label={ariaLabel}>
        <div className="npal__search">
          <span aria-hidden="true">🔎</span>
          <input
            autoFocus
            type="search"
            value={query}
            onChange={e => { setQuery(e.target.value); setActive(0); }}
            onKeyDown={onKeyDown}
            placeholder={placeholder}
            aria-label={searchLabel}
          />
        </div>
        {context && <div className="npal__context">Dopo <b>{context}</b></div>}
        {notice && (
          <div className="npal__context" role="alert">
            {notice.message}{' '}
            {notice.onRetry && <button type="button" className="list-state__retry" onClick={() => notice.onRetry()}>Riprova</button>}
          </div>
        )}
        <div className="npal__list" ref={listRef}>
          {flat.length === 0 && <div className="npal__empty">Nessun risultato</div>}
          {filtered.map(g => (
            <div key={g.label}>
              <div className="npal__group">{g.label}</div>
              {g.items.map(item => {
                index += 1;
                const i = index;
                const favorite = memory.favorites.includes(itemKey(item));
                return (
                  <div key={`${g.label}:${item.id}`} className="npal__row">
                    <div
                      className={`npal__item${i === active ? ' npal__item--active' : ''}`}
                      onMouseMove={() => { if (i !== active) setActive(i); }}
                      {...pressable(() => pick(item))}
                      role="option"
                      aria-selected={i === active}
                    >
                      <span className="npal__icon" aria-hidden="true">{displayIcon(item.icon)}</span>
                      <span className="npal__label">{item.label}</span>
                      {item.desc && <span className="npal__desc">{item.desc}</span>}
                    </div>
                    {scope && (
                      <button
                        type="button"
                        className={`npal__star${favorite ? ' npal__star--on' : ''}`}
                        aria-pressed={favorite}
                        aria-label={favorite ? `Togli ${item.label} dai preferiti` : `Aggiungi ${item.label} ai preferiti`}
                        title={favorite ? 'Togli dai preferiti' : 'Aggiungi ai preferiti'}
                        onClick={() => setMemory(toggleFavorite(scope, item))}
                      >
                        {favorite ? '★' : '☆'}
                      </button>
                    )}
                  </div>
                );
              })}
              {g.hidden > 0 && (
                <div className="npal__more" role="note">
                  {g.searching
                    ? `Altri ${formatNumber(g.hidden)} risultati: scrivi di più per restringere la ricerca.`
                    : `Altre ${formatNumber(g.hidden)} voci non mostrate: scrivi per cercarle.`}
                </div>
              )}
            </div>
          ))}
        </div>
        <div className="npal__foot"><kbd>↑</kbd><kbd>↓</kbd> scegli · <kbd>Invio</kbd> {pickVerb} · <kbd>Esc</kbd> chiudi</div>
      </div>
    </div>
  );
}
