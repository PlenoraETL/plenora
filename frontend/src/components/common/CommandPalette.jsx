/**
 * CommandPalette.jsx — palette comandi globale (Cmd/Ctrl+K). #25 roadmap.
 *
 * Homegrown (nessuna dipendenza): listener globale Cmd/Ctrl+K apre un overlay
 * con ricerca + lista comandi di navigazione, navigazione da tastiera
 * (↑/↓/Enter/Esc). Va montata DENTRO il Router (usa useNavigate).
 */
import React, { useState, useEffect, useRef, useMemo, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import './CommandPalette.css';

// Destinazioni di navigazione comuni (tutte ProtectedRoute, visibili a ogni
// utente loggato). Le route admin sono escluse: restano raggiungibili dal menu
// e comunque guardate dalle *Route.
const COMMANDS = [
  { label: 'Progetti',                 hint: 'Lista progetti',          path: '/',                    icon: '📂' },
  { label: 'Connessioni DB',           hint: 'Database',                path: '/connections',         icon: '🔌' },
  { label: 'Connettori esterni / API', hint: 'API enrichment',          path: '/external-connectors', icon: '🌐' },
  { label: 'Data Source',              hint: 'SharePoint · FTP · S3',   path: '/datasources',         icon: '📁' },
  { label: 'File',                     hint: 'File manager',            path: '/files',               icon: '🗂' },
  { label: 'Schedule',                 hint: 'Esecuzioni pianificate',  path: '/schedules',           icon: '⏰' },
  { label: 'Log',                      hint: 'Esecuzioni e log tecnici', path: '/logs',               icon: '📋' },
  { label: 'Eseguibili',               hint: 'Pipeline pubblicate',     path: '/eseguibili',          icon: '▶' },
  { label: 'Ticket',                   hint: 'Supporto',                path: '/tickets',             icon: '🎫' },
];

export default function CommandPalette() {
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [active, setActive] = useState(0);
  const inputRef = useRef(null);

  // Toggle globale Cmd/Ctrl+K.
  useEffect(() => {
    const onKey = (e) => {
      if ((e.metaKey || e.ctrlKey) && (e.key === 'k' || e.key === 'K')) {
        e.preventDefault();
        setOpen((v) => !v);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  // Reset + focus all'apertura.
  useEffect(() => {
    if (open) {
      setQuery('');
      setActive(0);
      // focus dopo il mount del campo
      requestAnimationFrame(() => inputRef.current?.focus());
    }
  }, [open]);

  const results = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return COMMANDS;
    return COMMANDS.filter(
      (c) => c.label.toLowerCase().includes(q) || c.hint.toLowerCase().includes(q),
    );
  }, [query]);

  const go = useCallback((cmd) => {
    if (!cmd) return;
    setOpen(false);
    navigate(cmd.path);
  }, [navigate]);

  const onKeyDown = (e) => {
    if (e.key === 'Escape') { setOpen(false); return; }
    if (e.key === 'ArrowDown') { e.preventDefault(); setActive((i) => Math.min(i + 1, results.length - 1)); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setActive((i) => Math.max(i - 1, 0)); }
    else if (e.key === 'Enter') { e.preventDefault(); go(results[active]); }
  };

  if (!open) return null;

  return (
    <div className="cmdk__overlay" onClick={() => setOpen(false)}>
      <div
        className="cmdk__panel"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label="Palette comandi"
      >
        <div className="cmdk__search">
          <span className="cmdk__search-icon">⌘</span>
          <input
            ref={inputRef}
            className="cmdk__input"
            placeholder="Vai a… (digita per filtrare)"
            value={query}
            onChange={(e) => { setQuery(e.target.value); setActive(0); }}
            onKeyDown={onKeyDown}
          />
          <kbd className="cmdk__kbd">Esc</kbd>
        </div>
        <div className="cmdk__list">
          {results.length === 0 ? (
            <div className="cmdk__empty">Nessun comando per “{query}”</div>
          ) : (
            results.map((cmd, i) => (
              <button
                type="button"
                key={cmd.path}
                className={`cmdk__item${i === active ? ' cmdk__item--active' : ''}`}
                onMouseEnter={() => setActive(i)}
                onClick={() => go(cmd)}
              >
                <span className="cmdk__item-icon">{cmd.icon}</span>
                <span className="cmdk__item-label">{cmd.label}</span>
                <span className="cmdk__item-hint">{cmd.hint}</span>
              </button>
            ))
          )}
        </div>
      </div>
    </div>
  );
}
