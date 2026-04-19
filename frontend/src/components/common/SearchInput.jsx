/**
 * SearchInput - Input di ricerca con icona e clear button
 * 
 * Props:
 * - value: string
 * - onChange: (value: string) => void
 * - placeholder: string
 * - debounce: number (ms, 0 = disabled)
 * - autoFocus: boolean
 * - size: 'sm' | 'md' | 'lg'
 */

import React, { useState, useEffect, useCallback, useRef } from 'react';
import './SearchInput.css';

function SearchInput({
  value = '',
  onChange,
  placeholder = 'Cerca...',
  debounce = 300,
  autoFocus = false,
  size = 'md',
  className = '',
  disabled = false
}) {
  const [localValue, setLocalValue] = useState(value);
  const debounceRef = useRef(null);
  const inputRef = useRef(null);

  // Sync con value esterno
  useEffect(() => {
    setLocalValue(value);
  }, [value]);

  // Debounce onChange
  useEffect(() => {
    if (debounce <= 0) return;

    debounceRef.current = setTimeout(() => {
      if (localValue !== value) {
        onChange?.(localValue);
      }
    }, debounce);

    return () => clearTimeout(debounceRef.current);
  }, [localValue, debounce, onChange, value]);

  const handleChange = useCallback((e) => {
    const newValue = e.target.value;
    setLocalValue(newValue);
    
    // Se no debounce, chiama subito
    if (debounce <= 0) {
      onChange?.(newValue);
    }
  }, [debounce, onChange]);

  const handleClear = useCallback(() => {
    setLocalValue('');
    onChange?.('');
    inputRef.current?.focus();
  }, [onChange]);

  const handleKeyDown = useCallback((e) => {
    if (e.key === 'Escape') {
      handleClear();
    }
  }, [handleClear]);

  return (
    <div className={`search-input search-input--${size} ${className} ${disabled ? 'search-input--disabled' : ''}`}>
      <span className="search-input__icon">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <circle cx="11" cy="11" r="8"/>
          <path d="m21 21-4.35-4.35"/>
        </svg>
      </span>
      
      <input
        ref={inputRef}
        type="text"
        className="search-input__field"
        value={localValue}
        onChange={handleChange}
        onKeyDown={handleKeyDown}
        placeholder={placeholder}
        autoFocus={autoFocus}
        disabled={disabled}
      />
      
      {localValue && (
        <button 
          className="search-input__clear"
          onClick={handleClear}
          type="button"
          aria-label="Clear search"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M18 6L6 18M6 6l12 12"/>
          </svg>
        </button>
      )}
    </div>
  );
}

/**
 * Versione con filtri dropdown integrati
 */
export function SearchInputWithFilter({
  value,
  onChange,
  placeholder,
  filters = [],
  activeFilter = 'all',
  onFilterChange,
  ...props
}) {
  return (
    <div className="search-input-with-filter">
      <SearchInput
        value={value}
        onChange={onChange}
        placeholder={placeholder}
        {...props}
      />
      
      {filters.length > 0 && (
        <div className="search-input-filters">
          {filters.map(filter => (
            <button type="button"
              key={filter.value}
              className={`search-input-filter ${activeFilter === filter.value ? 'search-input-filter--active' : ''}`}
              onClick={() => onFilterChange?.(filter.value)}
            >
              {filter.icon && <span className="search-input-filter__icon">{filter.icon}</span>}
              {filter.label}
              {filter.count !== undefined && (
                <span className="search-input-filter__count">{filter.count}</span>
              )}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export default SearchInput;
