/**
 * LookupSuggestion — su un Case When che e' solo una tabella di
 * corrispondenza propone di trasformarlo in "Mappa valori", con lo
 * stesso risultato (panelConfig.lookupFromConditional). Fase 4, M4.
 */
import React from 'react';
import { lookupFromConditional } from './panelConfig';
import './panels.css';
import { termName } from '../../../utils/glossary';

export default function LookupSuggestion({ config, columns = [], onConvert, disabled }) {
  const lookup = lookupFromConditional(config, columns);
  if (!lookup) return null;
  return (
    <div className="ppanel__suggest">
      <div>
        Queste {lookup.mapping.length} condizioni «uguale a» su <b>{lookup.column}</b> sono una tabella di
        corrispondenza: con <b>{termName('lookup')}</b> si leggono e si modificano più facilmente, con lo stesso risultato.
      </div>
      <button type="button" className="ppanel__btn ppanel__btn--primary" disabled={disabled}
        onClick={() => onConvert(lookup)}>Trasforma in {termName('lookup')}</button>
    </div>
  );
}
