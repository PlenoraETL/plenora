/**
 * LoginPage.jsx — Pagina di autenticazione
 *
 * Layout split: brand panel sinistro + form destro.
 * Transizione geometrica con esagoni multi-dimensione sul bordo.
 *
 * Route: /login
 */
import React, { useState, useEffect, useRef } from 'react';
import { useNavigate, Navigate, useLocation } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { role } from '../utils/permissions';
import { useTheme } from '../hooks/useTheme';
import PlenoraLogo from '../components/common/PlenoraLogo';
import { isMfaRequired, loginErrorMessage } from '../utils/loginErrors';
import { returnPathAfterLogin } from '../utils/navigation';
import '../styles/LoginPage.css';

// Helper: dove mandare l'utente dopo il login in base al ruolo.
// Esecutore → /run (kiosk), tutti gli altri → / (dashboard normale).
const landingPathFor = (user) => (role.isEsecutore(user) ? '/run' : '/');
// R7.5: dopo il login (anche dopo un 401) si torna alla pagina richiesta.
const afterLoginPath = (user, from) => returnPathAfterLogin(from, landingPathFor(user), { esecutore: role.isEsecutore(user) });
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
export default function LoginPage() {
  const { login, token, user } = useAuth();
  const navigate         = useNavigate();
  const from             = useLocation().state?.from || null;
  const canvasRef        = useRef(null);

  const { theme, toggleTheme } = useTheme();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [mfaCode,  setMfaCode]  = useState('');
  const [needsMfa, setNeedsMfa]  = useState(false);
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
    });
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });

    return () => {
      window.removeEventListener('resize', onResize);
      observer.disconnect();
    };
  }, [token]);

  // Già autenticato → landing in base al ruolo (esecutore va su /run, altri su /)
  if (token) return <Navigate to={afterLoginPath(user, from)} replace />;

  async function handleSubmit(e) {
    if (e?.preventDefault) e.preventDefault();
    setError('');
    if (!username.trim()) { setError('Inserisci il nome utente.');  return; }
    if (!password)        { setError('Inserisci la password.');     return; }
    if (needsMfa && !mfaCode.trim()) { setError('Inserisci il codice MFA.'); return; }
    setLoading(true);
    try {
      const userData = await login(username, password, mfaCode.trim() || null);
      navigate(afterLoginPath(userData, from), { replace: true });
    } catch (err) {
      // Testo dal codice del backend (utils/loginErrors): il 401 del login
      // non passa dal percorso di uscita (api/http).
      if (isMfaRequired(err)) {
        setNeedsMfa(true);
        setError("Inserisci il codice MFA per completare l'accesso.");
      } else {
        setError(loginErrorMessage(err));
      }
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="lp">
      <canvas ref={canvasRef} className="lp__hex-canvas" />
<button className="lp__theme-toggle" onClick={toggleTheme} type="button" aria-label="Cambia tema">
        {theme === 'dark' ? '☀️' : '🌙'}
      </button>
<div className="lp__brand">
        <div className="lp__brand-inner">

          {/* Logo */}
          <div className="lp__logo">
            <PlenoraLogo
              width={260}
              height={72}
              gridLines="full"
              plenFill={theme === "light" ? "#0B1120" : "#FFFFFF"}
            />
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
              { key: 'arricchisci', label: 'Arricchisci', desc: 'Arricchimento da API, geocodifica, modelli ML' },
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
                onChange={e => {
                  // R8.22: il codice MFA e' di QUESTO utente: cambiando
                  // username si azzera (mai il codice di A inviato per B).
                  const next = e.target.value;
                  if (next.trim() !== username.trim()) { setNeedsMfa(false); setMfaCode(''); }
                  setUsername(next);
                }}
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
                  aria-label={showPw ? 'Nascondi password' : 'Mostra password'}
                >
                  {showPw ? '🙈' : '👁'}
                </button>
              </div>
            </div>

            {needsMfa && (
              <div className="lp__field">
                <label className="lp__label" htmlFor="lp-mfa">Codice MFA</label>
                <input
                  id="lp-mfa"
                  className="lp__input"
                  type="text"
                  inputMode="numeric"
                  pattern="[0-9]*"
                  placeholder="123456"
                  value={mfaCode}
                  onChange={e => setMfaCode(e.target.value)}
                  autoComplete="one-time-code"
                  // a11y-3: al passaggio MFA il cursore va sul codice.
                  autoFocus
                  disabled={loading}
                />
              </div>
            )}

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
