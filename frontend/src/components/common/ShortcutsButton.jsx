/**
 * ShortcutsButton — il pulsante «?» sempre visibile che apre la guida delle
 * scorciatoie (UI-4, U28): intestazione delle pagine (PageLayout) e barra
 * degli editor. Stessa guida del tasto `?` e della palette Ctrl+K.
 */
import React from 'react';
import { openShortcutsHelp } from '../../utils/shortcuts';

export default function ShortcutsButton({ className = 'plen-shortcuts-btn' }) {
  return (
    <button
      type="button"
      className={className}
      onClick={openShortcutsHelp}
      title="Scorciatoie da tastiera (?)"
      aria-label="Scorciatoie da tastiera"
    >
      ?
    </button>
  );
}
