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
  const [display, setDisplay] = useState(0);
  const rafRef = useRef(null);

  useEffect(() => {
    if (!active) {
      setDisplay(realProgress >= 100 ? 100 : 0);
      return undefined;
    }
    let last = now();
    const tick = (t) => {
      const dt = Math.min(0.1, (t - last) / 1000);
      last = t;
      setDisplay((prev) => {
        if (realProgress >= 100) return 100;
        // tetto = max(reale, ceiling): se il backend supera il tetto, segue il reale
        const cap = Math.max(realProgress, ceiling);
        const base = Math.max(prev, realProgress); // non regredisce mai
        const next = base + (cap - base) * (1 - Math.exp(-dt * 0.4));
        return Math.min(next, cap);
      });
      rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);
    return () => { if (rafRef.current) cancelAnimationFrame(rafRef.current); };
  }, [active, realProgress, ceiling]);

  return Math.round(display);
}

export default useSmoothProgress;
