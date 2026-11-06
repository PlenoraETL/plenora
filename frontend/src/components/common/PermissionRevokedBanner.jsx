/**
 * PermissionRevokedBanner — banner inline per esito task con
 * `result.status === 'permission_revoked'`.
 *
 * Backend (`app/shared/async_user_recheck.py`) ritorna questo status
 * quando il ruolo dell'utente viene declassato a viewer/esecutore
 * mid-task o l'utente viene disabilitato. I task `carica`,
 * `enrichment`, `pipeline` propagano il dict { status:
 * 'permission_revoked', success: false, message } al risultato.
 *
 * Visualizzazione: card inline rossa (no toast, no fullscreen) per
 * restare visibile finche' l'utente guarda l'esito nel modal corrente.
 *
 * Pattern visuale coerente con `ImdfPublisherNode.jsx:278-298` (inline
 * style su <div role='alert'>): no nuovo CSS file.
 */

import React from 'react';

export default function PermissionRevokedBanner({ message }) {
  return (
    <div
      role="alert"
      style={{
        background: '#fef2f2',
        border: '1px solid #dc2626',
        borderRadius: 4,
        color: '#991b1b',
        padding: '10px 12px',
        margin: '8px 0',
        fontSize: '0.9em',
      }}
    >
      <div style={{ fontWeight: 600, marginBottom: 4 }}>
        🔒 Permessi revocati durante l'esecuzione
      </div>
      <div style={{ fontSize: '0.9em', opacity: 0.85 }}>
        {message || (
          <>
            Il tuo ruolo è stato modificato o l'utenza disabilitata mentre
            l'esecuzione era in corso. L'operazione è stata interrotta in modo
            sicuro. Contatta un amministratore per ripristinare i permessi.
          </>
        )}
      </div>
    </div>
  );
}
