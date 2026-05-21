/**
 * PlenoraLogo — SVG brand logo riusabile.
 *
 * Pre-fix il blocco SVG di 7 righe (3 polygon + opzionali line + 2 text)
 * era copiato letteralmente in 6 file (3 editor + AppSidebar + LoginPage
 * + EsecutoreHome). Manutenzione del brand richiedeva 6 edit
 * sincronizzati.
 *
 * Variazioni coperte via props:
 *   - dimensioni (`width`/`height`)
 *   - linee interne (`gridLines`: `'none'` | `'left'` | `'full'`)
 *   - color del testo "Plen" (`plenFill` esplicito o `plenClassName`
 *     per styling tematico via CSS)
 *   - color del testo "ora" (`oraFill`, default `#4B8BFF`)
 */

const _POLYGONS = [
  { points: '16,58 52,40 88,58 52,76', fill: 'rgba(245,166,35,0.10)', stroke: '#F5A623', strokeWidth: 1.4 },
  { points: '16,44 52,26 88,44 52,62', fill: 'rgba(155,109,255,0.10)', stroke: '#9B6DFF', strokeWidth: 1.4 },
  { points: '16,30 52,12 88,30 52,48', fill: 'rgba(52,193,122,0.12)', stroke: '#34C17A', strokeWidth: 2 },
];

const _GRID_LINES = {
  none: [],
  left: [
    { x1: 16, y1: 30, x2: 16, y2: 58, stroke: 'rgba(255,255,255,0.08)', strokeWidth: 1 },
  ],
  full: [
    { x1: 16, y1: 30, x2: 16, y2: 58, stroke: 'rgba(255,255,255,0.08)', strokeWidth: 1 },
    { x1: 88, y1: 30, x2: 88, y2: 58, stroke: 'rgba(255,255,255,0.08)', strokeWidth: 1 },
    { x1: 52, y1: 48, x2: 52, y2: 76, stroke: 'rgba(255,255,255,0.06)', strokeWidth: 1 },
  ],
};

export default function PlenoraLogo({
  width = 90,
  height = 28,
  gridLines = 'none',
  plenFill,
  plenClassName,
  oraFill = '#4B8BFF',
}) {
  const plenProps = plenClassName
    ? { className: plenClassName }
    : { fill: plenFill || '#FFFFFF' };

  return (
    <svg width={width} height={height} viewBox="0 0 350 90" xmlns="http://www.w3.org/2000/svg">
      {_POLYGONS.map((p, i) => (
        <polygon
          key={`poly-${i}`}
          points={p.points}
          fill={p.fill}
          stroke={p.stroke}
          strokeWidth={p.strokeWidth}
          strokeLinejoin="round"
        />
      ))}
      {(_GRID_LINES[gridLines] || []).map((l, i) => (
        <line
          key={`line-${i}`}
          x1={l.x1}
          y1={l.y1}
          x2={l.x2}
          y2={l.y2}
          stroke={l.stroke}
          strokeWidth={l.strokeWidth}
        />
      ))}
      <text
        x="108" y="46"
        fontFamily="'Syne',sans-serif" fontSize="34" fontWeight="800" letterSpacing="-1.5"
        {...plenProps}
      >Plen</text>
      <text
        x="222" y="46"
        fontFamily="'Syne',sans-serif" fontSize="34" fontWeight="800" letterSpacing="-1.5"
        fill={oraFill}
      >ora</text>
    </svg>
  );
}
