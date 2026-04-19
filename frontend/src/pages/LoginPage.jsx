/**
 * LoginPage.jsx — Pagina di autenticazione
 *
 * Layout split: brand panel sinistro + form destro.
 * Transizione geometrica con esagoni multi-dimensione sul bordo.
 *
 * Route: /login
 */
import React, { useState, useEffect, useRef } from 'react';
import { useNavigate, Navigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { role } from '../utils/permissions';
import '../styles/LoginPage.css';

// Helper: dove mandare l'utente dopo il login in base al ruolo.
// Esecutore → /run (kiosk), tutti gli altri → / (dashboard normale).
const landingPathFor = (user) => (role.isEsecutore(user) ? '/run' : '/');

/* ── Hex canvas transition ─────────────────────────────────────────────── */
function drawHexTransition(canvas) {
  if (!canvas) return;

  const W = canvas.offsetWidth  || window.innerWidth;
  const H = canvas.offsetHeight || window.innerHeight;
  canvas.width  = W;
  canvas.height = H;

  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, W, H);

  const isDark = document.documentElement.getAttribute('data-theme') !== 'light';
  const panelColor   = isDark ? [12, 15, 24]   : [255, 255, 255];
  const bgColor      = isDark ? [7,  9,  15]    : [239, 242, 249];
  const accentColors = isDark
    ? [[75, 139, 255], [52, 193, 122], [155, 109, 255]]
    : [[37,  99, 235], [22, 163,  74], [124,  58, 237]];

  const rng = s => { let x = Math.sin(s + 1) * 43758.5453; return x - Math.floor(x); };

  const boundary = W * 0.52;
  const zoneW    = W * 0.32;
  const sizes    = [38, 24, 16, 52];

  sizes.forEach((R, si) => {
    const hexW = R * Math.sqrt(3);
    const hexH = R * 2;
    const cols = Math.ceil(zoneW * 2 / hexW) + 2;
    const rows = Math.ceil(H / (hexH * 0.75)) + 2;

    for (let col = -cols / 2; col < cols / 2; col++) {
      for (let row = 0; row < rows; row++) {
        const cx = boundary + col * hexW + (row % 2 === 0 ? 0 : hexW / 2);
        const cy = row * hexH * 0.75 - R;
        const dx = (cx - boundary) / zoneW;
        if (Math.abs(dx) > 1.2) continue;

        const seed   = si * 1000 + col * 37 + row * 13;
        const jitter = (rng(seed) - 0.5) * 0.6;
        const bell   = Math.cos(dx * Math.PI * 0.65);
        const op     = Math.min(1, Math.max(0, bell + jitter * (1 - Math.abs(dx))));
        if (op < 0.04) continue;

        ctx.beginPath();
        for (let i = 0; i < 6; i++) {
          const a  = (Math.PI / 180) * (60 * i - 30);
          const px = cx + R * Math.cos(a);
          const py = cy + R * Math.sin(a);
          i === 0 ? ctx.moveTo(px, py) : ctx.lineTo(px, py);
        }
        ctx.closePath();

        const t = (dx + 1) / 2;
        const r = Math.round(panelColor[0] * (1 - t) + bgColor[0] * t);
        const g = Math.round(panelColor[1] * (1 - t) + bgColor[1] * t);
        const b = Math.round(panelColor[2] * (1 - t) + bgColor[2] * t);
        ctx.fillStyle   = `rgba(${r},${g},${b},${(op * 0.85).toFixed(2)})`;
        ctx.fill();

        const ac = accentColors[Math.abs(Math.floor(col) + row + si) % 3];
        ctx.strokeStyle = `rgba(${ac[0]},${ac[1]},${ac[2]},${(op * 0.35).toFixed(2)})`;
        ctx.lineWidth   = 0.8;
        ctx.stroke();
      }
    }
  });
}

/* ── Componente ────────────────────────────────────────────────────────── */
export default function LoginPage() {
  const { login, token, user } = useAuth();
  const navigate         = useNavigate();
  const canvasRef        = useRef(null);

  const [theme,    setTheme]    = useState(() => {
    const saved = localStorage.getItem('plenora-theme');
    const initial = saved || document.documentElement.getAttribute('data-theme') || 'dark';
    document.documentElement.setAttribute('data-theme', initial);
    return initial;
  });

  const toggleTheme = () => {
    const next = theme === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', next);
    localStorage.setItem('plenora-theme', next);
    setTheme(next);
  };
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [showPw,   setShowPw]   = useState(false);
  const [error,    setError]    = useState('');
  const [loading,  setLoading]  = useState(false);

  // Canvas hex transition — hook deve stare PRIMA del return condizionale
  // (Rules of Hooks: non chiamare hook dopo return condizionale)
  useEffect(() => {
    if (token) return; // già loggato → effetto non necessario
    const canvas = canvasRef.current;
    drawHexTransition(canvas);

    const onResize = () => setTimeout(() => drawHexTransition(canvas), 50);
    window.addEventListener('resize', onResize);

    const observer = new MutationObserver(() => {
      drawHexTransition(canvas);
      setTheme(document.documentElement.getAttribute('data-theme') || 'dark');
    });
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });

    return () => {
      window.removeEventListener('resize', onResize);
      observer.disconnect();
    };
  }, [token]);

  // Già autenticato → landing in base al ruolo (esecutore va su /run, altri su /)
  if (token) return <Navigate to={landingPathFor(user)} replace />;

  async function handleSubmit(e) {
    if (e?.preventDefault) e.preventDefault();
    setError('');
    if (!username.trim()) { setError('Inserisci il nome utente.');  return; }
    if (!password)        { setError('Inserisci la password.');     return; }
    setLoading(true);
    try {
      const userData = await login(username, password);
      navigate(landingPathFor(userData), { replace: true });
    } catch (err) {
      setError(err.message || 'Credenziali non valide.');
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="lp">
      <canvas ref={canvasRef} className="lp__hex-canvas" />

      {/* ── Theme toggle ── */}
      <button className="lp__theme-toggle" onClick={toggleTheme} type="button" aria-label="Cambia tema">
        {theme === 'dark' ? '☀️' : '🌙'}
      </button>

      {/* ── Brand panel ── */}
      <div className="lp__brand">
        <div className="lp__brand-inner">

          {/* Logo */}
          <div className="lp__logo">
            <svg width="260" height="72" viewBox="0 0 350 90" xmlns="http://www.w3.org/2000/svg">
              <polygon points="16,58 52,40 88,58 52,76" fill="rgba(245,166,35,0.10)"  stroke="#F5A623" strokeWidth="1.4" strokeLinejoin="round"/>
              <polygon points="16,44 52,26 88,44 52,62" fill="rgba(155,109,255,0.10)" stroke="#9B6DFF" strokeWidth="1.4" strokeLinejoin="round"/>
              <polygon points="16,30 52,12 88,30 52,48" fill="rgba(52,193,122,0.12)"  stroke="#34C17A" strokeWidth="2"   strokeLinejoin="round"/>
              <line x1="16" y1="30" x2="16" y2="58" stroke="rgba(255,255,255,0.08)" strokeWidth="1"/>
              <line x1="88" y1="30" x2="88" y2="58" stroke="rgba(255,255,255,0.08)" strokeWidth="1"/>
              <line x1="52" y1="48" x2="52" y2="76" stroke="rgba(255,255,255,0.06)" strokeWidth="1"/>
              <text x="108" y="46" fontFamily="'Syne',sans-serif" fontSize="34" fontWeight="800" letterSpacing="-1.5" fill={theme === "light" ? "#0B1120" : "#FFFFFF"}>Plen</text>
              <text x="222" y="46" fontFamily="'Syne',sans-serif" fontSize="34" fontWeight="800" letterSpacing="-1.5" fill="#4B8BFF">ora</text>
            </svg>
          </div>

          {/* Headline */}
          <div className="lp__headline">
            <h1 className="lp__title">Ogni dato<br />ha un posto.</h1>
            <p className="lp__desc">Plenora lo trova.</p>
          </div>

          {/* Moduli */}
          <div className="lp__modules">
            {[
              { key: 'estrai',      label: 'Estrai',      desc: 'Connetti DB, ArcGIS, file geospaziali' },
              { key: 'manipola',    label: 'Manipola',    desc: 'Spatial join, pivot, trasformazioni'   },
              { key: 'arricchisci', label: 'Arricchisci', desc: 'API enrichment, geocoding, modelli ML' },
            ].map(m => (
              <div key={m.key} className="lp__module">
                <span className={`lp__module-dot lp__module-dot--${m.key}`} />
                <div>
                  <div className="lp__module-label">{m.label}</div>
                  <div className="lp__module-desc">{m.desc}</div>
                </div>
              </div>
            ))}
          </div>

          <div className="lp__brand-footer">
            <span className="lp__brand-badge">ESRI Partner</span>
          </div>
        </div>
      </div>

      {/* ── Form panel ── */}
      <div className="lp__form-side">
        <div className="lp__form-wrap">
          <div className="lp__form-header">
            <h2 className="lp__form-title">Accedi</h2>
            <p className="lp__form-sub">Inserisci le tue credenziali per continuare</p>
          </div>

          <form className="lp__form" onSubmit={handleSubmit} noValidate>
            <div className="lp__field">
              <label className="lp__label" htmlFor="lp-username">Username</label>
              <input
                id="lp-username"
                className="lp__input"
                type="text"
                placeholder="nome.utente"
                value={username}
                onChange={e => setUsername(e.target.value)}
                autoComplete="username"
                autoFocus
                disabled={loading}
              />
            </div>

            <div className="lp__field">
              <label className="lp__label" htmlFor="lp-password">Password</label>
              <div className="lp__input-wrap">
                <input
                  id="lp-password"
                  className="lp__input lp__input--pw"
                  type={showPw ? 'text' : 'password'}
                  placeholder="••••••••"
                  value={password}
                  onChange={e => setPassword(e.target.value)}
                  autoComplete="current-password"
                  disabled={loading}
                />
                <button
                  className="lp__pw-toggle"
                  type="button"
                  onClick={() => setShowPw(v => !v)}
                  tabIndex={-1}
                  aria-label={showPw ? 'Nascondi password' : 'Mostra password'}
                >
                  {showPw ? '🙈' : '👁'}
                </button>
              </div>
            </div>

            {error && (
              <div className="lp__error" role="alert">
                <span aria-hidden="true">⚠</span> {error}
              </div>
            )}

            <button className="lp__btn" type="submit" disabled={loading}>
              {loading ? 'Accesso in corso…' : 'Accedi →'}
            </button>
          </form>
        </div>

        <div className="lp__form-footer">Plenora GeoData Platform</div>
      </div>
    </div>
  );
}
