/**
 * AccessDeniedBanner — banner shared usato dai 3 editor
 * (Estrai/Manipola/Arricchisci) come `beforeCanvas` slot di EditorShell.
 *
 * Renderizza un banner role="alert" se il canvas contiene almeno un nodo
 * con risorsa non accessibile al gruppo del progetto. Stile/copy/contratto
 * preservati byte-per-byte rispetto al JSX duplicato pre-FE-CLEANUP-FINAL.
 */

import React from 'react';

import {
  hasAccessDeniedNodes,
  getAccessDeniedNodes,
} from '../../utils/canvasAccessControl';

function AccessDeniedBanner({ nodes }) {
  if (!hasAccessDeniedNodes(nodes)) return null;
  const count = getAccessDeniedNodes(nodes).length;
  return (
    <div
      role="alert"
      style={{
        background: '#fff4e5',
        border: '1px solid #d97706',
        borderRadius: 4,
        color: '#92400e',
        padding: '10px 14px',
        margin: '8px 16px 0',
        fontSize: '0.92em',
      }}
    >
      🔒 Il canvas contiene {count} nodo/i con risorse non accessibili al tuo gruppo. Rimuovili o sostituiscili prima di eseguire.
    </div>
  );
}

export default AccessDeniedBanner;
