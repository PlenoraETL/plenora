/**
 * ShareDialog — la finestra «Condividi» di una risorsa (10/11/2026): UNA per
 * la pagina Eseguibili, la pagina Connessioni e il pannello della home per
 * catene. Dentro, il selettore dei gruppi (AllowedGroupsSelector, solo
 * superadmin). Il salvataggio (PUT /shares/...) invalida tutte le liste
 * condivise, anche la home (utils/sharedInvalidations).
 *
 * Props: resourceType (chiave di AllowedGroupsSelector), resourceId, name,
 * disabled (sola lettura), onClose.
 */
import React from 'react';
import ModalWrapper from '../common/ModalWrapper';
import AllowedGroupsSelector from './AllowedGroupsSelector';

const TITLES = Object.freeze({
  eseguibili: 'Condividi Eseguibile',
  connections: 'Condividi connessione',
  arcgis_connections: 'Condividi connessione',
  file_sources: 'Condividi file',
  datasources: 'Condividi Data Source',
});

export default function ShareDialog({ resourceType, resourceId, name, disabled = false, onClose }) {
  return (
    <ModalWrapper
      isOpen
      onClose={onClose}
      title={TITLES[resourceType] || 'Condividi'}
      icon="🔗"
      subtitle={name}
      size="md"
    >
      <AllowedGroupsSelector
        embedded
        resourceType={resourceType}
        resourceId={resourceId}
        disabled={disabled}
        onClose={onClose}
      />
    </ModalWrapper>
  );
}
