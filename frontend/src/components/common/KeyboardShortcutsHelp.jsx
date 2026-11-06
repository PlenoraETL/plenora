/**
 * KeyboardShortcutsHelp.jsx — overlay guida scorciatoie da tastiera. #33.
 *
 * Rende SCOPRIBILI le shortcut del FE. Si apre con `?` globale, con il
 * pulsante «?» (components/common/ShortcutsButton) o con l'evento
 * SHORTCUTS_EVENT (dalla palette Ctrl+K). Le righe vengono dalla tabella
 * unica utils/shortcuts, la stessa che usano i gestori dei tasti (UI-4,
 * U28): la guida e' completa per costruzione.
 */
import React, { useState, useEffect } from 'react';
import ModalWrapper from './ModalWrapper';
import { isShortcutBlocked, isTypingTarget } from '../../utils/dialogStack';
import { SHORTCUTS, SHORTCUTS_EVENT, matchesShortcut } from '../../utils/shortcuts';
import { ICON } from '../../utils/icons';
import './KeyboardShortcutsHelp.css';

const GROUPS = [...new Set(SHORTCUTS.map(s => s.group))];

const Keys = ({ keys }) => (
  <span className="kbd-help__keys">
    {keys.map((k, j) => <kbd key={j} className="kbd-help__key">{k}</kbd>)}
  </span>
);

export default function KeyboardShortcutsHelp() {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    const onKey = (e) => {
      // `?` quando non si sta scrivendo in un campo e nessuna finestra e'
      // aperta (regola unica delle scorciatoie, utils/dialogStack).
      if (isTypingTarget(e.target) || isShortcutBlocked(e)) return;
      if (matchesShortcut('help', e)) {
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
      icon={ICON.shortcuts}
      size="sm"
    >
      <div className="kbd-help">
        {GROUPS.map((group) => (
          <div key={group} className="kbd-help__group">
            <div className="kbd-help__group-title">{group}</div>
            {SHORTCUTS.filter(s => s.group === group).map((s) => (
              <div key={s.id} className="kbd-help__row" data-shortcut={s.id}>
                <Keys keys={s.keys} />
                {s.altKeys && <><span className="kbd-help__or">o</span><Keys keys={s.altKeys} /></>}
                <span className="kbd-help__label">{s.label}</span>
              </div>
            ))}
          </div>
        ))}
      </div>
    </ModalWrapper>
  );
}
