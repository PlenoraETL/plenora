/**
 * ThemeToggle — toggle Scuro/Chiaro condiviso.
 *
 * Le pagine hanno classi CSS con prefisso BEM-like specifico
 * (es. acm__theme-toggle, cm__theme-opt, ...). Per non riscrivere il CSS
 * il componente accetta `classPrefix` ("acm" | "cm" | ...) e genera
 * le classi `${prefix}__theme-toggle`, `${prefix}__theme-opt`,
 * `${prefix}__theme-opt--on`.
 *
 * In alternativa è possibile passare `classes={{ wrap, opt, optOn }}`
 * per controllo esplicito.
 */
import React from 'react';
import { useTheme } from '../../hooks/useTheme';

export default function ThemeToggle({ classPrefix, classes }) {
  const { theme, setTheme } = useTheme();

  const wrap  = classes?.wrap  ?? `${classPrefix}__theme-toggle`;
  const opt   = classes?.opt   ?? `${classPrefix}__theme-opt`;
  const optOn = classes?.optOn ?? `${classPrefix}__theme-opt--on`;

  return (
    <div className={wrap}>
      <div
        className={`${opt}${theme === 'dark' ? ` ${optOn}` : ''}`}
        onClick={() => setTheme('dark')}
      >🌙 Scuro</div>
      <div
        className={`${opt}${theme === 'light' ? ` ${optOn}` : ''}`}
        onClick={() => setTheme('light')}
      >☀️ Chiaro</div>
    </div>
  );
}
