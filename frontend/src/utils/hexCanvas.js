/**
 * hexCanvas.js — Hook condiviso per sfondo esagonale canvas
 *
 * Usato da: ProjectList, EstraiEditor, ManipolaEditor, ArricchisciEditor,
 *           ConnectionManager, FileManager, ApiConnectionManager,
 *           DataSourceManager, ScheduleList, LogViewer
 *
 * Disegna un pattern di esagoni sovrapposti con opacità variabile e
 * supporto automatico per tema chiaro/scuro + resize.
 */
import { useEffect, useCallback } from 'react';

/**
 * Disegna lo sfondo esagonale sul canvas fornito.
 * Funzione pura (no side-effect) — richiamabile direttamente per draw manuali.
 */
function drawHexBackground(canvas) {
  if (!canvas) return;
  const isDark = document.documentElement.getAttribute('data-theme') !== 'light';
  const parent = canvas.parentElement;
  const rect = parent?.getBoundingClientRect();
  const W = (rect?.width > 10 ? rect.width : null) || parent?.offsetWidth || canvas.offsetWidth || window.innerWidth;
  const H = (rect?.height > 10 ? rect.height : null) || parent?.offsetHeight || canvas.offsetHeight || window.innerHeight;
  if (W < 10 || H < 10) return;
  canvas.width = W;
  canvas.height = H;
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, W, H);

  const accentColors = isDark
    ? [[75,139,255],[52,193,122],[155,109,255]]
    : [[37,99,235],[22,163,74],[124,58,237]];
  const rng = s => { let x = Math.sin(s + 1) * 43758.5453; return x - Math.floor(x); };
  const sizes = [44, 26, 16, 60];

  sizes.forEach((R, si) => {
    const hexW = R * Math.sqrt(3);
    const hexH = R * 2;
    const offX = rng(si * 99) * hexW * 0.5;
    const offY = rng(si * 77) * hexH * 0.5;
    const cols = Math.ceil(W / hexW) + 2;
    const rows = Math.ceil(H / (hexH * 0.75)) + 2;
    for (let col = -1; col < cols; col++) {
      for (let row = -1; row < rows; row++) {
        const cx = col * hexW + (row % 2 === 0 ? 0 : hexW / 2) + offX;
        const cy = row * hexH * 0.75 + offY;
        const tx = cx / W;
        const ty = 1 - cy / H;
        const diag = Math.pow(tx * 0.5 + ty * 0.5, 1.4);
        const seed = si * 1000 + col * 37 + row * 13;
        const jitter = (rng(seed) - 0.5) * 0.35;
        const base = isDark ? 0.06 : 0.09;
        const op = Math.min(0.22, Math.max(0, base + jitter * base * 2)) * diag;
        if (op < 0.01) continue;
        ctx.beginPath();
        for (let i = 0; i < 6; i++) {
          const a = (Math.PI / 180) * (60 * i - 30);
          i === 0
            ? ctx.moveTo(cx + R * Math.cos(a), cy + R * Math.sin(a))
            : ctx.lineTo(cx + R * Math.cos(a), cy + R * Math.sin(a));
        }
        ctx.closePath();
        const ac = accentColors[Math.abs(Math.floor(col) + row + si) % 3];
        ctx.strokeStyle = `rgba(${ac[0]},${ac[1]},${ac[2]},${op.toFixed(3)})`;
        ctx.lineWidth = 0.6;
        ctx.stroke();
      }
    }
  });
}

/**
 * Hook React per sfondo esagonale con auto-resize e reazione al cambio tema.
 *
 * @param {React.RefObject} canvasRef — ref al <canvas> element
 */
export function useHexCanvas(canvasRef) {
  const draw = useCallback(() => drawHexBackground(canvasRef.current), [canvasRef]);

  useEffect(() => {
    const raf = requestAnimationFrame(draw);
    const t = setTimeout(draw, 300);
    const onResize = () => requestAnimationFrame(draw);
    window.addEventListener('resize', onResize);
    const obs = new MutationObserver(() => requestAnimationFrame(draw));
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
    let resObs;
    if (canvasRef.current && window.ResizeObserver) {
      resObs = new ResizeObserver(() => requestAnimationFrame(draw));
      resObs.observe(canvasRef.current.parentElement || canvasRef.current);
    }
    return () => {
      cancelAnimationFrame(raf);
      clearTimeout(t);
      window.removeEventListener('resize', onResize);
      obs.disconnect();
      resObs?.disconnect();
    };
  }, [canvasRef, draw]);
}
