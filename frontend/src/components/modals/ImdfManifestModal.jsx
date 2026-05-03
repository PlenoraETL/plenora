/**
 * ImdfManifestModal.jsx — Visualizza manifest.json IMDF + esito pubblicazione
 *
 * Riceve `manifest` (oggetto JSON parsato dal backend, salvato in
 * lastTaskResult.imdf.manifest) + `publish` (risultato dell'upload ArcGIS
 * eventualmente prodotto da un nodo imdf_publisher nel canvas).
 *
 * Tema scuro cyan: usa lo shell condiviso `imdf-modal.css`
 * (FE-PHASE2-IMDF-MODAL-SHELL-001) per overlay/panel/header/body/footer.
 * Le palette dinamiche (per-stato pub, per-severity validation) restano
 * inline runtime via le CSS vars `--imdf-modal-*`.
 */
import React, { useState } from 'react';
import { createPortal } from 'react-dom';

// Palette dinamica esposta via CSS vars (var(--imdf-modal-*)). Usata solo
// per gli style inline che dipendono da valori runtime (color/danger/
// success per Row).
const VARS = {
  accent:  'var(--imdf-modal-accent)',
  accent2: 'var(--imdf-modal-accent2)',
  text:    'var(--imdf-modal-text)',
  muted:   'var(--imdf-modal-muted)',
  border:  'var(--imdf-modal-border)',
  surface: 'var(--imdf-modal-surface)',
  font:    'var(--imdf-modal-font)',
  danger:  'var(--imdf-modal-danger)',
  success: 'var(--imdf-modal-success)',
};

export default function ImdfManifestModal({ isOpen, onClose, manifest, siteId, publish }) {
  const [showRaw, setShowRaw] = useState(false);

  if (!isOpen) return null;

  const m          = manifest || {};
  const sites      = m.sites || {};
  const validation = m.validation || {};
  const pub        = publish || null;

  return createPortal((
    <div onClick={onClose} className="imdf-modal__overlay">
      <div
        onClick={(e) => e.stopPropagation()}
        className="imdf-modal__panel imdf-modal__panel--md"
      >
        {/* Header */}
        <div className="imdf-modal__header">
          <div>
            <h2 className="imdf-modal__title">📋 Manifest IMDF</h2>
            <div className="imdf-modal__subtitle">
              {siteId ? `Site · ${siteId}` : 'Processing metadata'}
            </div>
          </div>
          <button type="button" onClick={onClose} className="imdf-modal__close">✕</button>
        </div>

        {/* Body */}
        {!manifest ? (
          <div className="imdf-modal__empty">
            Nessun manifest disponibile. Esegui il canvas per generare i dati IMDF.
          </div>
        ) : (
          <div className="imdf-modal__body imdf-modal__body--col">

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
                  <div key={sid} className="imdf-modal__card">
                    <div className="imdf-modal__card-title">{sid}</div>
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
                                        color={validation.passed ? VARS.success : VARS.danger} />
                <Row k="Errori totali"  v={validation.total_errors} />
                <Row k="Warning totali" v={validation.total_warnings} />
              </Section>
            )}

            {/* Pubblicazione ArcGIS */}
            {pub && (
              <Section title="Pubblicazione ArcGIS">
                <Row k="Esito"
                     v={pub.success ? '✓ Pubblicato' : '✗ Errore'}
                     color={pub.success ? VARS.success : VARS.danger} />
                {pub.folder_id         && <Row k="Folder"  v={pub.folder_id} mono />}
                {pub.service_item_id   && <Row k="Item ID" v={pub.service_item_id} mono />}

                {Array.isArray(pub.layers) && pub.layers.length > 0 && (
                  <div style={{
                    marginTop: 8,
                    background: VARS.surface,
                    border: `1px solid ${VARS.border}`,
                    borderRadius: 6,
                    padding: '8px 12px',
                  }}>
                    <div style={{
                      fontSize: 9, color: VARS.muted, textTransform: 'uppercase',
                      letterSpacing: 1, marginBottom: 6,
                    }}>Layer caricati</div>
                    {pub.layers.map((l) => (
                      <div key={l.id ?? l.name} style={{
                        display: 'flex', justifyContent: 'space-between',
                        fontSize: 11, padding: '4px 0',
                        borderBottom: `1px dashed ${VARS.border}`,
                      }}>
                        <span>
                          <span style={{ color: VARS.muted, marginRight: 6 }}>#{l.id}</span>
                          <span style={{ color: VARS.text }}>{l.name}</span>
                        </span>
                        <span style={{ color: VARS.accent }}>{l.feature_count ?? 0} feat.</span>
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
                         border: `1px solid ${VARS.accent}55`,
                         borderRadius: 5,
                         color: VARS.accent,
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
                    color: VARS.danger,
                    fontSize: 11,
                    background: 'rgba(239,68,68,0.08)',
                    border: `1px solid ${VARS.danger}40`,
                    borderRadius: 5,
                    padding: '8px 10px',
                    fontFamily: VARS.font,
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
              className="imdf-modal__toggle"
            >
              {showRaw ? '▲ Nascondi JSON' : '▼ Mostra JSON completo'}
            </button>

            {showRaw && (
              <pre className="imdf-modal__pre">{JSON.stringify(m, null, 2)}</pre>
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
      <div className="imdf-modal__section-title">{title}</div>
      <div>{children}</div>
    </div>
  );
}

function Row({ k, v, color, mono }) {
  if (v === undefined || v === null || v === '') return null;
  return (
    <div className="imdf-modal__kv">
      <div className="imdf-modal__kv-label">{k}</div>
      <div
        className={`imdf-modal__kv-value${mono ? ' imdf-modal__kv-value--mono' : ''}`}
        style={color ? { color } : undefined}
      >
        {typeof v === 'object' ? JSON.stringify(v) : String(v)}
      </div>
    </div>
  );
}
