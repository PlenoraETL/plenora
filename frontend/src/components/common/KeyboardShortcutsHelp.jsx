/**
 * KeyboardShortcutsHelp.jsx — overlay guida scorciatoie da tastiera. #33.
 *
 * Rende SCOPRIBILI le shortcut sparse del FE (prima non c'era alcun help).
 * Si apre con `?` (Shift+/) globale o via evento `plenora:open-shortcuts`
 * (dalla Command Palette). Registro centralizzato qui sotto.
 */
import React, { useState, useEffect } from 'react';
import ModalWrapper from './ModalWrapper';
import { isShortcutBlocked, isTypingTarget } from '../../utils/dialogStack';
import './KeyboardShortcutsHelp.css';

// Registro scorciatoie. `keys` = sequenza di tasti mostrati come <kbd>.
const SHORTCUTS = [
  {
    group: 'Globale',
    items: [
      { keys: ['⌘/Ctrl', 'K'], label: 'Apri la palette comandi (vai a…)' },
      { keys: ['?'],            label: 'Mostra questa guida' },
      { keys: ['Esc'],          label: 'Chiudi la finestra in primo piano (modale, palette o guida)' },
    ],
  },
  {
    group: 'Canvas (editor)',
    items: [
      { keys: ['Canc'],            label: 'Elimina il nodo o il collegamento selezionato (chiede conferma)' },
      { keys: ['⌘/Ctrl', 'Z'],     label: 'Annulla (undo) l’ultima modifica' },
      { keys: ['⌘/Ctrl', '⇧', 'Z'], label: 'Ripristina (redo)' },
      { keys: ['⌘/Ctrl', 'Y'],     label: 'Ripristina (redo) — alternativa' },
      { keys: ['⌘/Ctrl', 'S'],     label: 'Salva il progetto' },
    ],
  },
];

export const SHORTCUTS_EVENT = 'plenora:open-shortcuts';

export default function KeyboardShortcutsHelp() {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    const onKey = (e) => {
      // `?` (Shift+/) quando non si sta scrivendo in un campo e nessuna
      // finestra e' aperta (regola unica delle scorciatoie, utils/dialogStack).
      if (isTypingTarget(e.target) || isShortcutBlocked(e)) return;
      if (e.key === '?' || (e.key === '/' && e.shiftKey)) {
        e.preventDefault();
        setOpen((v) => !v);
      }
    };
    const onEvent = () => setOpen(true);
    window.addEventListener('keydown', onKey);
    window.addEventListener(SHORTCUTS_EVENT, onEvent);
    return () => {
      window.removeEventListener('keydown', onKey);
      window.removeEventListener(SHORTCUTS_EVENT, onEvent);
    };
  }, []);

  if (!open) return null;

  return (
    <ModalWrapper
      isOpen
      onClose={() => setOpen(false)}
      title="Scorciatoie da tastiera"
      icon="⌨️"
      size="sm"
    >
      <div className="kbd-help">
        {SHORTCUTS.map((g) => (
          <div key={g.group} className="kbd-help__group">
            <div className="kbd-help__group-title">{g.group}</div>
            {g.items.map((it, i) => (
              <div key={i} className="kbd-help__row">
                <span className="kbd-help__keys">
                  {it.keys.map((k, j) => <kbd key={j} className="kbd-help__key">{k}</kbd>)}
                </span>
                <span className="kbd-help__label">{it.label}</span>
              </div>
            ))}
          </div>
        ))}
      </div>
    </ModalWrapper>
  );
}
