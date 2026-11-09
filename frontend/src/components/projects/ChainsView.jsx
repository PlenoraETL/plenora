/**
 * ChainsView — la home per catene (09/11/2026, mockup approvato dall'utente).
 *
 * Ogni catena e' una riga: Connessione -> Estrai -> Manipola e Arricchisci ->
 * Pubblicato (regole in utils/projectChains). Un clic su una scheda la
 * seleziona: a monte in blu, a valle in verde, il resto sbiadito; il
 * pannello laterale dice tipo, ultima esecuzione, avvisi, «Usa» ed «È usato
 * da» (cliccabili) e le azioni (Apri, Esegui, Cronologia, menu «⋯»). Sotto,
 * «Da guardare» (tabella WATCH_RULES) e i contatori.
 *
 * Dati: la risorsa condivisa `dashboardGraph` (GET /dashboard/graph, niente
 * canvas); nomi delle connessioni e dei file dalle liste della dashboard.
 * Nessun selettore dello store del canvas. Le catene passano da VirtualList.
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useSharedResource } from '../../hooks/useSharedResource';
import { ResourceError } from '../common/ListState';
import EmptyState from '../common/EmptyState';
import ActionMenu from '../common/ActionMenu';
import VirtualList from '../common/VirtualList';
import ProjectTags from './ProjectTags';
import { ICON } from '../../utils/icons';
import { isShortcutBlocked, isTypingTarget } from '../../utils/dialogStack';
import { matchesShortcut } from '../../utils/shortcuts';
import {
  COLUMNS, COLLAPSE_AT, buildGraph, buildChains, chainCounters, filterChains, lineage,
  nodeMeta, nodeTags, orderChains, relationOf, typeLabel, watchFindings, byName,
} from '../../utils/projectChains';
import { formatCount } from './useProjectListData';
import './ChainsView.css';

// Parti della risposta non calcolate (GET /dashboard/graph `unavailable`).
const PART_LABELS = Object.freeze({
  links: 'collegamenti tra i progetti',
  runs: 'stato delle esecuzioni',
  eseguibili: 'Eseguibili',
  schedules: 'pianificazioni',
});
const WATCH_SHOWN = 8;
// Testo di una colonna richiusa: «9 Estrai», «3 connessioni».
const MORE_LABELS = Object.freeze({ origin: 'connessioni', estrai: 'Estrai', transform: 'Manipola e Arricchisci', published: 'pubblicati' });

const typeClass = (node) => `ch__badge ch__badge--${node.kind === 'project' ? node.type : node.kind === 'eseguibile' ? 'eseguibile' : 'origin'}`;

function NodeCard({ node, meta, relation, warned, onPick }) {
  const flags = nodeTags(node);
  return (
    <button
      type="button"
      id={`ch-node-${node.key}`}
      className={`ch__card${relation ? ` ch__card--${relation}` : ''}`}
      aria-pressed={relation === 'sel'}
      onClick={() => onPick(node.key)}
    >
      <span className="ch__card-flags">
        <span className={typeClass(node)}>{typeLabel(node)}</span>
        {flags.map((f) => <span key={f} className="ch__flag">{f}</span>)}
        {warned && <span className="ch__flag ch__flag--warn">Da guardare</span>}
        {relation === 'up' && <span className="ch__flag ch__flag--up">usato</span>}
        {relation === 'down' && <span className="ch__flag ch__flag--down">lo usa</span>}
      </span>
      <span className="ch__card-name">{node.name}</span>
      <span className="ch__card-meta">{meta}</span>
    </button>
  );
}

function ChainRow({ chain, selected, lin, findings, metas, expanded, onToggle, onPick }) {
  const rel = (n) => relationOf(n.key, selected, lin);
  const cols = COLUMNS.map((c) => chain.columns[c.key]);
  const warned = chain.nodes.filter((n) => findings.byKey.has(n.key)).length;
  const cells = [];
  cols.forEach((col, i) => {
    if (i > 0) {
      const leftHas = cols.slice(0, i).some((x) => x.length);
      const show = leftHas && col.length > 0;
      // Freccia accesa: la colonna piena a sinistra e questa hanno schede collegate alla selezione.
      const left = [...cols.slice(0, i)].reverse().find((x) => x.length) || [];
      const active = Boolean(selected) && left.some((n) => rel(n) !== 'none') && col.some((n) => rel(n) !== 'none');
      cells.push(
        <div key={`a${i}`} className={`ch__arrow${active ? ' ch__arrow--on' : ''}`} aria-hidden="true">
          {show && (
            <svg width="22" height="14" viewBox="0 0 22 14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M1 7h18" /><path d="M14 2l5 5-5 5" /></svg>
          )}
        </div>,
      );
    }
    const key = `${chain.id}:${COLUMNS[i].key}`;
    const passthrough = col.length === 0 && cols.slice(0, i).some((x) => x.length) && cols.slice(i + 1).some((x) => x.length);
    const hasSelection = Boolean(selected) && col.some((n) => selected === n.key);
    const collapsible = col.length > COLLAPSE_AT;
    const open = !collapsible || expanded.has(key) || hasSelection;
    const related = selected ? col.filter((n) => rel(n) !== 'none').length : 0;
    cells.push(
      <div key={`c${i}`} className="ch__cell" role="group" aria-label={COLUMNS[i].label}>
        {passthrough && <div className="ch__pass" aria-hidden="true" />}
        {open ? col.map((n) => (
          <NodeCard key={n.key} node={n} meta={metas.get(n.key)} relation={rel(n)} warned={findings.byKey.has(n.key)} onPick={onPick} />
        )) : (
          <button type="button" className={`ch__more${related ? ' ch__more--on' : ''}`} aria-expanded="false" onClick={() => onToggle(key)}>
            <span className="ch__more-count">{col.length} {MORE_LABELS[COLUMNS[i].key]}</span>
            <span className="ch__more-text">{related ? `${related} collegati alla selezione · ` : ''}Mostra</span>
          </button>
        )}
        {collapsible && open && !hasSelection && (
          <button type="button" className="ch__less" aria-expanded="true" onClick={() => onToggle(key)}>Richiudi</button>
        )}
      </div>,
    );
  });
  return (
    <section className="ch__chain" aria-labelledby={`ch-title-${chain.id}`}>
      <div className="ch__chain-head">
        <h2 className="ch__chain-title" id={`ch-title-${chain.id}`}>{chain.title}</h2>
        {chain.desc && <span className="ch__chain-desc">{chain.desc}</span>}
        {warned > 0 && <span className="ch__chain-warn">{warned} da guardare</span>}
      </div>
      <div className="ch__scroll">
        <div className="ch__grid">{cells}</div>
      </div>
    </section>
  );
}

function RelatedList({ title, empty, keys, graph, onPick }) {
  const items = keys.map((k) => graph.nodes.get(k)).filter(Boolean).sort(byName);
  return (
    <div className="ch__related">
      <h3 className="ch__h3">{title}</h3>
      {items.length === 0 ? <span className="ch__muted">{empty}</span> : items.map((n) => (
        <button key={n.key} type="button" className="ch__related-item" onClick={() => onPick(n.key)}>
          <span className="ch__related-name">{n.name}</span>
          <span className={typeClass(n)}>{typeLabel(n)}</span>
        </button>
      ))}
    </div>
  );
}

function SelectionPanel({ node, meta, graph, findings, actions, onPick }) {
  const [renaming, setRenaming] = useState(false);
  const [draft, setDraft] = useState('');
  useEffect(() => { setRenaming(false); }, [node?.key]);
  if (!node) {
    return (
      <section className="ch__panel" aria-label="Progetto selezionato">
        <p className="ch__muted">Clicca un progetto per vedere cosa usa e chi lo usa.</p>
      </section>
    );
  }
  const a = actions(node) || {};
  const warns = findings.byKey.get(node.key) || [];
  const commitRename = () => {
    setRenaming(false);
    const v = draft.trim();
    if (v && v !== node.name) a.rename?.(v);
  };
  return (
    <section className="ch__panel" aria-label="Progetto selezionato">
      <div className="ch__panel-head">
        <span className={typeClass(node)}>{typeLabel(node)}</span>
        {renaming ? (
          <input
            className="ch__rename"
            aria-label="Nuovo nome"
            value={draft}
            autoFocus
            onChange={(e) => setDraft(e.target.value)}
            onBlur={commitRename}
            onKeyDown={(e) => {
              if (e.key === 'Enter') commitRename();
              else if (matchesShortcut('close', e)) { e.stopPropagation(); setRenaming(false); }
            }}
          />
        ) : <h2 className="ch__panel-name">{node.name}</h2>}
        <span className="ch__muted">{meta}</span>
      </div>
      {a.tags && <ProjectTags tags={a.tags.value} onSetTags={a.tags.onSet} />}
      {warns.map((w) => <div key={w.rule.id} className="ch__warn" role="note">{w.text}</div>)}
      <RelatedList title="Usa" empty="Niente: è all'inizio della catena" keys={graph.ups.get(node.key) || []} graph={graph} onPick={onPick} />
      <RelatedList
        title="È usato da"
        empty={node.kind === 'eseguibile' ? 'Nessuno: è il punto d\'arrivo' : 'Nessuno: se lo modifichi non cambia nient\'altro'}
        keys={graph.downs.get(node.key) || []}
        graph={graph}
        onPick={onPick}
      />
      <div className="ch__actions">
        {a.open && <button type="button" className="ch__btn ch__btn--primary" onClick={a.open}>{a.openLabel || 'Apri'}</button>}
        {a.run && <button type="button" className="ch__btn" onClick={a.run}>{ICON.run} Esegui</button>}
        {a.history && <button type="button" className="ch__btn" onClick={a.history}>{ICON.run_history} Cronologia</button>}
        {a.schedules && <button type="button" className="ch__btn" onClick={a.schedules}>{ICON.schedule} Pianificazioni</button>}
        <ActionMenu
          label={`Azioni per ${node.name}`}
          items={[
            a.rename && { label: '✎ Rinomina', onSelect: () => { setDraft(node.name); setRenaming(true); } },
            a.clone && { label: '⧉ Clona', onSelect: a.clone },
            a.publish && { label: `${ICON.executable} Pubblica come Eseguibile`, onSelect: a.publish },
            a.remove && { label: `${ICON.delete} Elimina`, onSelect: a.remove, danger: true },
          ]}
        />
      </div>
    </section>
  );
}

function WatchPanel({ findings, graph, onPick }) {
  const [all, setAll] = useState(false);
  const list = findings.list;
  const visibleItems = all ? list : list.slice(0, WATCH_SHOWN);
  return (
    <section className="ch__panel" aria-labelledby="ch-watch-title">
      <h2 className="ch__panel-title" id="ch-watch-title">Da guardare</h2>
      {list.length === 0 && <p className="ch__muted">Niente da guardare.</p>}
      {visibleItems.map((f) => {
        const node = graph.nodes.get(f.key);
        return (
          <button key={`${f.rule.id}:${f.key}`} type="button" className="ch__todo" onClick={() => onPick(f.key, { scroll: true })}>
            <span className="ch__todo-title">{node.name} · {typeLabel(node)}</span>
            <span className="ch__todo-text">{f.text}</span>
          </button>
        );
      })}
      {list.length > WATCH_SHOWN && (
        <button type="button" className="ch__less" aria-expanded={all} onClick={() => setAll((v) => !v)}>
          {all ? 'Mostra meno' : `Mostra tutti (${list.length})`}
        </button>
      )}
    </section>
  );
}

function Counters({ counters }) {
  const tiles = [
    [counters.projects, counters.projects === 1 ? 'progetto' : 'progetti'],
    [counters.published, counters.published === 1 ? 'pubblicato per gli esecutori' : 'pubblicati per gli esecutori'],
    [counters.schedulesActive, counters.schedulesActive === 1 ? 'pianificazione attiva' : 'pianificazioni attive'],
    [counters.connectionsUsed, counters.connectionsUsed === 1 ? 'connessione usata' : 'connessioni usate'],
  ];
  return (
    <section className="ch__panel ch__counters" aria-label="Contatori">
      {tiles.map(([value, label]) => (
        <div key={label} className="ch__counter">
          <span className="ch__counter-val">{formatCount(value)}</span>
          <span className="ch__counter-label">{label}</span>
        </div>
      ))}
    </section>
  );
}

/**
 * @param {object} props
 * @param {object} props.search          hooks/useSearch della pagina
 * @param {Map} props.connections        id -> { name, kind } (database e ArcGIS)
 * @param {Map} props.fileNames          id -> nome del file
 * @param {(node) => object} props.actions azioni del nodo selezionato (open, run, history, ...)
 * @param {React.ReactNode} [props.empty] stato vuoto senza progetti
 */
export default function ChainsView({ search, connections, fileNames, actions, empty = null }) {
  const resource = useSharedResource('dashboardGraph');
  const raw = resource.data;
  const [selected, setSelected] = useState(null);
  const [filter, setFilter] = useState('all');
  const [expanded, setExpanded] = useState(() => new Set());
  const scrollTo = useRef(null);

  const graph = useMemo(
    () => buildGraph(raw || { projects: [], eseguibili: [] }, { connections, fileNames }),
    [raw, connections, fileNames],
  );
  const runsUnavailable = graph.unavailable.has('runs');
  // La riga di stato di ogni scheda: una volta per grafo, non a ogni render.
  const metas = useMemo(
    () => new Map([...graph.nodes.values()].map((n) => [n.key, nodeMeta(n, { runsUnavailable })])),
    [graph, runsUnavailable],
  );
  const findings = useMemo(() => watchFindings(graph), [graph]);
  const chains = useMemo(() => orderChains(buildChains(graph), findings), [graph, findings]);
  const sel = selected && graph.nodes.has(selected) ? selected : null;
  const lin = useMemo(() => lineage(graph, sel), [graph, sel]);
  const shown = useMemo(
    () => filterChains(chains, { filter, findings, matches: search?.active ? search.matches : null }),
    [chains, filter, findings, search?.active, search?.matches],
  );
  const counters = useMemo(() => chainCounters(graph), [graph]);
  const isolatedCount = chains.find((c) => c.isolated)?.nodes.filter((n) => n.kind === 'project').length || 0;

  const pick = useCallback((key, { scroll = false } = {}) => {
    setSelected((cur) => (cur === key && !scroll ? null : key));
    if (scroll) scrollTo.current = key;
  }, []);
  useEffect(() => {
    if (!scrollTo.current) return;
    const el = document.getElementById(`ch-node-${scrollTo.current}`);
    scrollTo.current = null;
    el?.scrollIntoView?.({ block: 'nearest', behavior: 'smooth' });
    el?.focus?.({ preventScroll: true });
  }, [sel]);
  // Esc toglie la selezione (non se una finestra o un campo l'ha gia' usato).
  useEffect(() => {
    if (!sel) return undefined;
    const onKey = (e) => {
      if (!matchesShortcut('close', e) || isShortcutBlocked(e) || isTypingTarget(e.target)) return;
      setSelected(null);
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [sel]);
  const toggle = useCallback((key) => setExpanded((cur) => {
    const next = new Set(cur);
    if (next.has(key)) next.delete(key); else next.add(key);
    return next;
  }), []);

  if (resource.initialLoading) {
    return <p className="ch__muted ch__loading" role="status">Caricamento delle catene…</p>;
  }
  if (resource.status === 'error' && !raw) {
    return <ResourceError resource={resource} title="Catene non caricate" />;
  }
  if (!graph.nodes.size && empty) return empty;

  const chips = [
    ['all', 'Tutte le catene'],
    ['warn', `Da guardare (${findings.byKey.size})`],
    ['pub', 'Pubblicati'],
    ['unused', `Non usati (${isolatedCount})`],
  ];
  const missing = [...graph.unavailable].map((p) => PART_LABELS[p] || p);

  return (
    <div className="ch">
      <div className="ch__filters" role="group" aria-label="Filtra le catene">
        {chips.map(([key, label]) => (
          <button key={key} type="button" className={`ch__chip${filter === key ? ' ch__chip--on' : ''}`} aria-pressed={filter === key} onClick={() => setFilter(key)}>
            {label}
          </button>
        ))}
      </div>
      {resource.status === 'error' && raw && <ResourceError resource={resource} title="Catene non aggiornate" compact />}
      {missing.length > 0 && (
        <ResourceError compact title="Alcuni dati delle catene non caricati" message={`${missing.join(', ')}: le verifiche che li usano non sono state fatte`} onRetry={resource.reload} />
      )}
      <div className="ch__layout">
        <div className="ch__main">
          <div className="ch__scroll" aria-hidden="true">
            <div className="ch__grid ch__grid--head">
              {COLUMNS.map((c, i) => (
                <React.Fragment key={c.key}>
                  {i > 0 && <span />}
                  <span className="ch__col-label">{c.label}</span>
                </React.Fragment>
              ))}
            </div>
          </div>
          {shown.length === 0 ? (
            <EmptyState
              compact
              icon={ICON.search}
              title={search?.active ? `Nessuna catena per «${search.query}»` : 'Nessuna catena con questo filtro'}
              text="Cambia il filtro o la ricerca."
              action={<button type="button" className="ch__btn" onClick={() => { setFilter('all'); search?.clear?.(); }}>Mostra tutte</button>}
            />
          ) : (
            <VirtualList
              className="ch__chains"
              label="Catene dei progetti"
              items={shown}
              itemKey={(c) => c.id}
              estimatedItemHeight={220}
              renderItem={(chain) => (
                <ChainRow
                  chain={chain}
                  selected={sel}
                  lin={lin}
                  findings={findings}
                  metas={metas}
                  expanded={expanded}
                  onToggle={toggle}
                  onPick={pick}
                />
              )}
            />
          )}
        </div>
        <aside className="ch__side" aria-label="Dettagli e controlli">
          <SelectionPanel node={sel ? graph.nodes.get(sel) : null} meta={sel ? metas.get(sel) : null} graph={graph} findings={findings} actions={actions} onPick={pick} />
          <WatchPanel findings={findings} graph={graph} onPick={pick} />
          <Counters counters={counters} />
        </aside>
      </div>
    </div>
  );
}
