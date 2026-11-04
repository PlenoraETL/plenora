/**
 * autoRunNote — nota "eseguito automaticamente" dei nodi Carica (DB e
 * ArcGIS). Dipende dall'editor (Blocco A, e.6): il pulsante si chiama
 * "Esegui" come nella barra (nome dal glossario), e cio' che lo precede e'
 * la query in Estrai, il flusso in Manipola, l'arricchimento in Arricchisci.
 */
import React from 'react';
import useCanvasStore from '../../../store/useCanvasStore';
import { term } from '../../../utils/glossary';

const AFTER = {
  estrai: 'dopo la query',
  manipola: 'dopo il flusso',
  arricchisci: "dopo l'arricchimento",
};

export function autoRunAfter(mode) {
  return AFTER[mode] || `dopo l'${term('output').name}`;
}

export default function AutoRunNote({ className }) {
  const mode = useCanvasStore(s => s.mode);
  return (
    <div className={className}>
      ⚡ Eseguito automaticamente con <strong>{term('run').name}</strong>, {autoRunAfter(mode)}
    </div>
  );
}
