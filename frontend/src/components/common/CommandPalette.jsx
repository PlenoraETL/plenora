/**
 * CommandPalette.jsx — palette comandi globale (Cmd/Ctrl+K). #25 roadmap.
 *
 * Homegrown (nessuna dipendenza): listener globale Cmd/Ctrl+K apre un overlay
 * con ricerca + lista comandi di navigazione, navigazione da tastiera
 * (↑/↓/Enter/Esc). Va montata DENTRO il Router (usa useNavigate).
 *
 * Fase 5, T4: e' una finestra dello stack unico (hooks/useDialog: Esc, Tab,
 * focus e ritorno del focus); Ctrl+K non si apre sopra un'altra finestra
 * (utils/dialogStack.isShortcutBlocked) e la navigazione passa dal guard
 * unico delle modifiche non salvate (components/common/NavigationGuard).
 *
 * UI-4 (U23): pagine con i nomi della barra laterale (tabella unica
 * utils/navItems) e ricerca dei progetti gia' scaricati, senza richieste;
 * Ctrl+K dalla tabella delle scorciatoie (utils/shortcuts, U28).
 */
import React, { useState, useEffect, useRef, useMemo, useCallback } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { useAuth } from '../../context/AuthContext';
import { role } from '../../utils/permissions';
import useDialog from '../../hooks/useDialog';
import { isShortcutBlocked } from '../../utils/dialogStack';
import { visibleNavItems } from '../../utils/navItems';
import { matchesShortcut, openShortcutsHelp, SHORTCUTS } from '../../utils/shortcuts';
import { ICON } from '../../utils/icons';
import { peekShared } from '../../utils/sharedResources';
import { projectType } from '../../utils/projectTypes';
import { projectEditorPath } from '../../utils/projectRoutes';
import { prefetchProjectEditor } from '../../utils/editorChunks';
import './CommandPalette.css';

// Progetti mostrati al massimo per una ricerca (gli altri restringendola).
const MAX_PROJECTS = 8;
const HELP = SHORTCUTS.find(sh => sh.id === 'help');

/*
 * Comandi: le pagine dalla tabella unica della navigazione (utils/navItems:
 * stessi nomi e icone della barra laterale, UI-4 U23; quelle di
 * amministrazione solo a chi le vede) e la guida delle scorciatoie.
 */
function commandsFor(user) {
  return [
    ...visibleNavItems(user).map(item => ({ key: item.key, label: item.label, hint: item.hint, path: item.path, icon: item.icon })),
    { key: 'shortcuts', label: 'Scorciatoie da tastiera', hint: `Guida dei tasti (${HELP.keys.join(' ')})`, icon: ICON.shortcuts, action: openShortcutsHelp },
  ];
}

/*
 * Progetti: dalla lista gia' scaricata nella cache condivisa (risorsa
 * `projects`), nessuna richiesta in piu'. Solo con una ricerca. La cache e'
 * della sessione (una risposta arrivata dopo il logout non la riempie) ed e'
 * invalidata da ogni rinomina o eliminazione (conferma C-K5): mai i
 * progetti di un altro utente o nomi superati.
 */
const knownProjects = () => (peekShared('projects')?.data?.projects || [])
  .filter(p => p && p.id != null)
  .map(p => ({ id: p.id, name: String(p.name || ''), project_type: p.project_type }));

function projectCommands(q) {
  if (!q) return [];
  return knownProjects()
    .filter(p => p.name.toLowerCase().includes(q))
    .slice(0, MAX_PROJECTS)
    .map(p => {
      const t = projectType(p.project_type);
      return {
        key: `project:${p.id}`, label: p.name, hint: `Progetto ${t.label}`, icon: t.icon,
        path: projectEditorPath(p), projectType: p.project_type,
      };
    });
}

export default function CommandPalette() {
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [active, setActive] = useState(0);
  const close = useCallback(() => setOpen(false), []);
  const dialog = useDialog({ isOpen: open, onClose: close, labelledBy: false });
  const dialogIdRef = useRef(dialog.id);
  dialogIdRef.current = dialog.id;
  // P28 (Fase 5 T7): niente palette senza sessione, sul login e nel kiosk
  // dell'esecutore (/run): le sue destinazioni li' non esistono.
  const { token, user } = useAuth();
  const { pathname } = useLocation();
  const enabled = Boolean(token) && !role.isEsecutore(user) && !/^\/(login|run)(\/|$)/.test(pathname);
  const enabledRef = useRef(enabled);
  enabledRef.current = enabled;
  useEffect(() => { if (!enabled) setOpen(false); }, [enabled]);

  // Toggle globale Cmd/Ctrl+K: non sopra un'altra finestra (regola unica);
  // con la palette in cima la richiude.
  useEffect(() => {
    const onKey = (e) => {
      if (matchesShortcut('palette', e)) {
        if (!enabledRef.current) return;
        if (isShortcutBlocked(e, { ownDialogId: dialogIdRef.current })) return;
        e.preventDefault();
        setOpen((v) => !v);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  // Reset all'apertura (il focus va sul campo: autoFocus, rispettato dallo stack).
  useEffect(() => {
    if (open) {
      setQuery('');
      setActive(0);
    }
  }, [open]);

  const commands = useMemo(() => commandsFor(user), [user]);
  const results = useMemo(() => {
    const q = query.trim().toLowerCase();
    const pages = q
      ? commands.filter((c) => c.label.toLowerCase().includes(q) || c.hint.toLowerCase().includes(q))
      : commands;
    return [...pages, ...projectCommands(q)];
  }, [query, commands]);

  // Il progetto evidenziato: il suo editor si scarica gia' (UI-4, V13).
  const activeProjectType = results[active]?.projectType;
  const activeIsProject = Boolean(results[active]?.key?.startsWith('project:'));
  useEffect(() => {
    if (activeIsProject) prefetchProjectEditor(activeProjectType);
  }, [activeIsProject, activeProjectType]);

  const go = useCallback((cmd) => {
    if (!cmd) return;
    setOpen(false);
    if (typeof cmd.action === 'function') cmd.action();
    else navigate(cmd.path);
  }, [navigate]);

  const onKeyDown = (e) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); setActive((i) => Math.min(i + 1, results.length - 1)); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setActive((i) => Math.max(i - 1, 0)); }
    else if (e.key === 'Enter') { e.preventDefault(); go(results[active]); }
  };

  if (!open) return null;

  return (
    <div className="cmdk__overlay" {...dialog.overlayProps}>
      <div
        ref={dialog.ref}
        className="cmdk__panel"
        {...dialog.dialogProps}
        aria-label="Palette comandi"
      >
        <div className="cmdk__search">
          <span className="cmdk__search-icon" aria-hidden="true">{ICON.search}</span>
          <input
            autoFocus
            aria-label="Cerca un comando"
            className="cmdk__input"
            placeholder="Vai a una pagina o a un progetto…"
            value={query}
            onChange={(e) => { setQuery(e.target.value); setActive(0); }}
            onKeyDown={onKeyDown}
          />
          <kbd className="cmdk__kbd">Esc</kbd>
        </div>
        <div className="cmdk__list">
          {results.length === 0 ? (
            <div className="cmdk__empty">
              Nessuna pagina né progetto per “{query}”
              {knownProjects().length === 0 && ' (i progetti si cercano dopo aver aperto la dashboard)'}
            </div>
          ) : (
            results.map((cmd, i) => (
              <button
                type="button"
                key={cmd.key}
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
