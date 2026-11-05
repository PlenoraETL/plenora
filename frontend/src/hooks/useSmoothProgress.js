/**
 * useSmoothProgress.js — progress percepita "fluida" (Epic C).
 *
 * Problema: molti task non riportano progress dal backend → la barra resta a
 * 0% per minuti e sembra bloccata. Questo hook segue il progress reale quando
 * avanza (non torna mai indietro), ma quando il reale è fermo fa avvicinare la
 * barra asintoticamente a un tetto (default 90%) così resta viva. Arriva a 100
 * solo quando il reale lo raggiunge.
 *
 * @param {number} realProgress 0..100 dal backend
 * @param {boolean} active       true mentre il task è in corso
 * @returns {number} percentuale da mostrare (intero)
 */
import { useEffect, useRef, useState } from 'react';

const now = () => (typeof performance !== 'undefined' ? performance.now() : Date.now());

export function useSmoothProgress(realProgress, active, { ceiling = 90 } = {}) {
  // perf-5 (Fase 5 T7): il valore esatto vive in un ref; lo stato (e quindi
  // il rendering) cambia solo quando cambia la percentuale MOSTRATA (intera),
  // non a ogni frame.
  const [display, setDisplay] = useState(0);
  const exactRef = useRef(0);
  const rafRef = useRef(null);

  useEffect(() => {
    if (!active) {
      exactRef.current = realProgress >= 100 ? 100 : 0;
      setDisplay(exactRef.current);
      return undefined;
    }
    let last = now();
    const tick = (t) => {
      const dt = Math.min(0.1, (t - last) / 1000);
      last = t;
      const prev = exactRef.current;
      let next;
      if (realProgress >= 100) next = 100;
      else {
        // tetto = max(reale, ceiling): se il backend supera il tetto, segue il reale
        const cap = Math.max(realProgress, ceiling);
        const base = Math.max(prev, realProgress); // non regredisce mai
        next = Math.min(base + (cap - base) * (1 - Math.exp(-dt * 0.4)), cap);
      }
      exactRef.current = next;
      const shown = Math.round(next);
      setDisplay(d => (d === shown ? d : shown));
      rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);
    return () => { if (rafRef.current) cancelAnimationFrame(rafRef.current); };
  }, [active, realProgress, ceiling]);

  return Math.round(display);
}

export default useSmoothProgress;
