/**
 * ImdfManifestModal.jsx — Visualizza manifest.json IMDF + esito pubblicazione
 *
 * Riceve `manifest` (oggetto JSON parsato dal backend, salvato in
 * lastTaskResult.imdf.manifest) + `publish` (risultato dell'upload ArcGIS
 * eventualmente prodotto da un nodo imdf_publisher nel canvas).
 *
 * Tema scuro cyan coerente con ImdfPreviewModal (JetBrains Mono,
 * #0c0e14 / #7c3aed).
 */
import React, { useState } from 'react';
import { createPortal } from 'react-dom';

// Tokens → var(--plen-*) del design system Plenora. Seguono data-theme
// automaticamente. Il cyan resta costante come brand IMDF.
// Mapping visivo:
//   bg      = modale esterno        → var(--plen-surface)  (white in light)
//   surface = header + card interne → var(--plen-surface-2)
//   inset   = pre/JSON              → var(--plen-bg)       (background pagina)
const TOKENS = {
  bg:       'var(--plen-surface, #0C0F18)',
  surface:  'var(--plen-surface-2, #111525)',
  inset:    'var(--plen-bg, #07090F)',
  border:   'var(--plen-border, rgba(255,255,255,0.08))',
  accent:   '#7C3AED',                          // IMDF brand (violet Plenora)
  accent2:  'var(--plen-manipola, #7c3aed)',
  text:     'var(--plen-text, #DDE4F2)',
  muted:    'var(--plen-text-sub, #7D8BA8)',
  dim:      'var(--plen-text-dim, #3F4D68)',
  danger:   'var(--plen-err, #F87171)',
  warn:     'var(--plen-warn, #FBBF24)',
  success:  'var(--plen-ok, #34D399)',
  font:     "var(--plen-font-mono, 'JetBrains Mono'), ui-monospace, Menlo, monospace",
};

export default function ImdfManifestModal({ isOpen, onClose, manifest, siteId, publish }) {
  const [showRaw, setShowRaw] = useState(false);

  if (!isOpen) return null;

  const m          = manifest || {};
  const sites      = m.sites || {};
  const validation = m.validation || {};
  const pub        = publish || null;

  return createPortal((
    <div
      onClick={onClose}
      style={{
        position: 'fixed', inset: 0,
        background: 'rgba(0,0,0,0.72)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        zIndex: 'var(--z-modal-priority, 10000)',
        fontFamily: TOKENS.font,
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          background: TOKENS.bg,
          color: TOKENS.text,
          border: `1px solid ${TOKENS.border}`,
          borderRadius: 10,
          width: 'min(780px, 94vw)',
          maxHeight: '90vh',
          boxShadow: '0 24px 80px rgba(0,0,0,0.7)',
          display: 'flex', flexDirection: 'column',
          overflow: 'hidden',
        }}
      >
        {/* Header */}
        <div style={{
          display: 'flex', justifyContent: 'space-between', alignItems: 'center',
          padding: '16px 20px',
          background: TOKENS.surface,
          borderBottom: `1px solid ${TOKENS.border}`,
        }}>
          <div>
            <h2 style={{
              margin: 0, fontSize: 15, fontWeight: 800,
              color: TOKENS.accent, letterSpacing: 0.3,
            }}>
              📋 Manifest IMDF
            </h2>
            <div style={{
              fontSize: 9, color: TOKENS.muted, textTransform: 'uppercase',
              letterSpacing: 0.5, marginTop: 2,
            }}>
              {siteId ? `Site · ${siteId}` : 'Processing metadata'}
            </div>
          </div>
          <button type="button" onClick={onClose} style={{
            background: 'none', border: 'none', color: TOKENS.muted,
            fontSize: 18, cursor: 'pointer', padding: 4,
          }}>✕</button>
        </div>

        {/* Body */}
        {!manifest ? (
          <div style={{ padding: '30px 20px', color: TOKENS.muted, fontSize: 12, textAlign: 'center' }}>
            Nessun manifest disponibile. Esegui il canvas per generare i dati IMDF.
          </div>
        ) : (
          <div style={{ overflow: 'auto', padding: 20, display: 'flex', flexDirection: 'column', gap: 18 }}>

            {/* Processing */}
            <Section title="Processing">
              <Row k="Processed at"    v={m.processed_at} />
              <Row k="Input DXF count" v={m.input_dxf_count} />
              <Row k="Excel"           v={m.excel} />
            </Section>

            {/* Siti */}
            {Object.keys(sites).length > 0 && (
              <Section title="Siti">
                {Object.entries(sites).map(([sid, info]) => (
                  <div key={sid} style={{
                    background: TOKENS.surface,
                    border: `1px solid ${TOKENS.border}`,
                    borderLeft: `3px solid ${TOKENS.accent2}`,
                    borderRadius: 6,
                    padding: '10px 12px',
                    marginBottom: 8,
                  }}>
                    <div style={{
                      fontSize: 12, fontWeight: 700, color: TOKENS.accent,
                      marginBottom: 6, letterSpacing: 0.3,
                    }}>{sid}</div>
                    <Row k="Livelli"   v={info.levels} />
                    <Row k="Locali"    v={info.units} />
                    <Row k="Aperture"  v={info.openings} />
                    <Row k="Area (m²)" v={info.area} />
                  </div>
                ))}
              </Section>
            )}

            {/* Validation summary */}
            {validation && Object.keys(validation).length > 0 && (
              <Section title="Validazione">
                <Row k="Esito"          v={validation.passed ? '✓ OK' : '✗ Errori'}
                                        color={validation.passed ? TOKENS.success : TOKENS.danger} />
                <Row k="Errori totali"  v={validation.total_errors} />
                <Row k="Warning totali" v={validation.total_warnings} />
              </Section>
            )}

            {/* Pubblicazione ArcGIS */}
            {pub && (
              <Section title="Pubblicazione ArcGIS">
                <Row k="Esito"
                     v={pub.success ? '✓ Pubblicato' : '✗ Errore'}
                     color={pub.success ? TOKENS.success : TOKENS.danger} />
                {pub.folder_id         && <Row k="Folder"  v={pub.folder_id} mono />}
                {pub.service_item_id   && <Row k="Item ID" v={pub.service_item_id} mono />}

                {Array.isArray(pub.layers) && pub.layers.length > 0 && (
                  <div style={{
                    marginTop: 8,
                    background: TOKENS.surface,
                    border: `1px solid ${TOKENS.border}`,
                    borderRadius: 6,
                    padding: '8px 12px',
                  }}>
                    <div style={{
                      fontSize: 9, color: TOKENS.muted, textTransform: 'uppercase',
                      letterSpacing: 1, marginBottom: 6,
                    }}>Layer caricati</div>
                    {pub.layers.map((l) => (
                      <div key={l.id ?? l.name} style={{
                        display: 'flex', justifyContent: 'space-between',
                        fontSize: 11, padding: '4px 0',
                        borderBottom: `1px dashed ${TOKENS.border}`,
                      }}>
                        <span>
                          <span style={{ color: TOKENS.muted, marginRight: 6 }}>#{l.id}</span>
                          <span style={{ color: TOKENS.text }}>{l.name}</span>
                        </span>
                        <span style={{ color: TOKENS.accent }}>{l.feature_count ?? 0} feat.</span>
                      </div>
                    ))}
                  </div>
                )}

                {pub.renderers_applied != null && (
                  <Row k="Renderer applicati"
                       v={`${pub.renderers_applied}/${pub.layers?.length ?? 0}`} />
                )}

                {pub.service_url && (
                  <div style={{ marginTop: 10 }}>
                    <a href={pub.service_url}
                       target="_blank"
                       rel="noopener noreferrer"
                       style={{
                         display: 'inline-flex', alignItems: 'center', gap: 6,
                         padding: '7px 14px',
                         background: 'rgba(124,58,237,0.12)',
                         border: `1px solid ${TOKENS.accent}55`,
                         borderRadius: 5,
                         color: TOKENS.accent,
                         fontSize: 11,
                         fontWeight: 600,
                         textDecoration: 'none',
                         textTransform: 'uppercase',
                         letterSpacing: 0.5,
                       }}>
                      ↗ Apri Feature Service
                    </a>
                  </div>
                )}

                {pub.error && (
                  <div style={{
                    marginTop: 8,
                    color: TOKENS.danger,
                    fontSize: 11,
                    background: 'rgba(239,68,68,0.08)',
                    border: `1px solid ${TOKENS.danger}40`,
                    borderRadius: 5,
                    padding: '8px 10px',
                    fontFamily: TOKENS.font,
                  }}>
                    {pub.error}
                  </div>
                )}
              </Section>
            )}

            {/* Toggle raw JSON */}
            <button
              type="button"
              onClick={() => setShowRaw(v => !v)}
              style={{
                alignSelf: 'flex-start',
                background: TOKENS.surface,
                border: `1px solid ${TOKENS.border}`,
                color: TOKENS.text,
                padding: '6px 12px',
                borderRadius: 5,
                cursor: 'pointer',
                fontSize: 10,
                fontWeight: 600,
                textTransform: 'uppercase',
                letterSpacing: 0.5,
                fontFamily: TOKENS.font,
              }}
            >
              {showRaw ? '▲ Nascondi JSON' : '▼ Mostra JSON completo'}
            </button>

            {showRaw && (
              <pre style={{
                background: TOKENS.inset,
                border: `1px solid ${TOKENS.border}`,
                padding: 12,
                borderRadius: 6,
                fontSize: 10,
                maxHeight: 340,
                overflow: 'auto',
                margin: 0,
                color: TOKENS.muted,
              }}>{JSON.stringify(m, null, 2)}</pre>
            )}
          </div>
        )}
      </div>
    </div>
  ), document.body);
}

// ── Sub-components ──────────────────────────────────────────────────────
function Section({ title, children }) {
  return (
    <div>
      <div style={{
        fontSize: 9, color: TOKENS.muted, textTransform: 'uppercase',
        letterSpacing: 1, marginBottom: 8,
        paddingBottom: 4, borderBottom: `1px solid ${TOKENS.border}`,
      }}>{title}</div>
      <div>{children}</div>
    </div>
  );
}

function Row({ k, v, color, mono }) {
  if (v === undefined || v === null || v === '') return null;
  return (
    <div style={{
      display: 'flex', gap: 12, fontSize: 11,
      padding: '4px 0',
    }}>
      <div style={{ minWidth: 140, color: TOKENS.muted }}>{k}</div>
      <div style={{
        flex: 1,
        color: color || TOKENS.text,
        fontFamily: mono ? TOKENS.font : 'inherit',
        wordBreak: 'break-word',
      }}>
        {typeof v === 'object' ? JSON.stringify(v) : String(v)}
      </div>
    </div>
  );
}
