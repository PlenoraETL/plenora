/**
 * autoRunNote — nota "eseguito automaticamente" dei nodi Carica (DB e
 * ArcGIS). Dipende dall'editor (Blocco A, e.6): il pulsante si chiama
 * "Esegui" come nella barra, e cio' che lo precede e' la query in Estrai,
 * la pipeline in Manipola, l'arricchimento in Arricchisci.
 */
import React from 'react';
import useCanvasStore from '../../../store/useCanvasStore';

const AFTER = {
  estrai: 'dopo la query',
  manipola: 'dopo la pipeline',
  arricchisci: "dopo l'arricchimento",
};

export function autoRunAfter(mode) {
  return AFTER[mode] || "dopo l'Output";
}

export default function AutoRunNote({ className }) {
  const mode = useCanvasStore(s => s.mode);
  return (
    <div className={className}>
      ⚡ Eseguito automaticamente con <strong>Esegui</strong>, {autoRunAfter(mode)}
    </div>
  );
}
