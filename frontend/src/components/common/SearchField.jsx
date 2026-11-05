/**
 * SearchField — IL campo di ricerca delle pagine e dei pannelli (Fase 5,
 * voce P28). Lo stato e la regola stanno in hooks/useSearch.
 *
 *   const search = useSearch();
 *   const shown = search.filter(items, (it) => [it.name, it.description]);
 *   <SearchField search={search} label="Cerca progetti" shown={shown.length} total={items.length} />
 *   <SearchEmpty search={search} shown={shown.length} />
 *
 * Comportamento unico:
 *  - filtra mentre scrivi, dopo la pausa dichiarata in useSearch; Invio
 *    applica subito (e non invia il modulo intorno);
 *  - Esc svuota il campo (se e' gia' vuoto lascia passare Esc: chiude la
 *    finestra intorno); × cancella;
 *  - etichetta accessibile obbligatoria (`label`, anche segnaposto);
 *  - con una ricerca attiva mostra il conteggio «N di M» (o «N risultati»
 *    se il totale non e' noto, ricerca lato server);
 *  - SearchEmpty: «Nessun risultato per "x"» con «Cancella ricerca».
 *  - In sola lettura (pannelli dei nodi) resta attiva: READ_ONLY_SAFE.
 *
 * Il test tests/shared/searchField.contract.test.jsx vieta input di
 * ricerca fatti a mano fuori da qui (eccezioni dichiarate col motivo).
 */
import React from 'react';
import { READ_ONLY_SAFE } from './readOnlySafe';
import { formatNumber } from '../../utils/format';
import './SearchField.css';

function countText(shown, total) {
  if (shown == null) return null;
  if (total == null) return `${formatNumber(shown)} ${shown === 1 ? 'risultato' : 'risultati'}`;
  return `${formatNumber(shown)} di ${formatNumber(total)}`;
}

export default function SearchField({
  search,
  label,
  placeholder,
  shown,
  total,
  className = '',
  inputClassName = '',
  compact = false,
  disabled = false,
  autoFocus = false,
  inputRef,
  onKeyDown,
}) {
  const onKey = (e) => {
    if (e.key === 'Escape' && search.text) {
      // Usato qui: la finestra intorno non si chiude (dialogStack guarda defaultPrevented).
      e.preventDefault();
      search.clear();
    } else if (e.key === 'Enter') {
      e.preventDefault();
      search.flush();
    }
    onKeyDown?.(e);
  };
  const count = search.active ? countText(shown, total) : null;
  return (
    <div className={`search-field${compact ? ' search-field--compact' : ''}${className ? ` ${className}` : ''}`}>
      <span className="search-field__icon" aria-hidden="true">⌕</span>
      <input
        {...READ_ONLY_SAFE}
        ref={inputRef}
        type="search"
        className={`search-field__input${inputClassName ? ` ${inputClassName}` : ''}`}
        aria-label={label}
        placeholder={placeholder || `${label}…`}
        value={search.text}
        autoFocus={autoFocus}
        disabled={disabled}
        onChange={(e) => search.setText(e.target.value)}
        onKeyDown={onKey}
      />
      {count && <span className="search-field__count" aria-live="polite">{count}</span>}
      {search.text && !disabled && (
        <button
          {...READ_ONLY_SAFE}
          type="button"
          className="search-field__clear"
          aria-label="Cancella ricerca"
          title="Cancella ricerca (Esc)"
          onClick={() => search.clear()}
        >
          ×
        </button>
      )}
    </div>
  );
}

/** Stato vuoto di una ricerca attiva senza risultati (nulla altrimenti). */
export function SearchEmpty({ search, shown, className = '' }) {
  if (!search.active || shown !== 0) return null;
  return (
    <div className={`search-empty${className ? ` ${className}` : ''}`} role="status">
      <span className="search-empty__text">Nessun risultato per &quot;{search.query}&quot;</span>
      <button {...READ_ONLY_SAFE} type="button" className="search-empty__clear" onClick={() => search.clear()}>
        Cancella ricerca
      </button>
    </div>
  );
}
