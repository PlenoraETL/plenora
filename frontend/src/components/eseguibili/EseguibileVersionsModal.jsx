/**
 * EseguibileVersionsModal — versioni di un eseguibile: ripubblica dal
 * progetto, ripristina una versione.
 *
 * Fase 5 T2 (R9.15, R4.8): versioni dall'hook comune (errore con «Riprova»);
 * Ripubblica e Ripristina chiedono conferma con la conseguenza (cambiano la
 * versione che parte da ora per tutti), una sola azione alla volta.
 */
import React from 'react';
import {
  activateEseguibileVersion,
  listEseguibileVersions,
  republishEseguibile,
} from '../../api';
import { humanizeError } from '../../utils/errorMessages';
import { useToast } from '../../hooks/useToast';
import { useResource } from '../../hooks/useResource';
import { useBusy } from '../../hooks/useBusy';
import { useConfirmModal } from '../../hooks/useConfirmModal';
import ModalWrapper, { ModalButton } from '../common/ModalWrapper';
import ConfirmDialog from '../common/ConfirmDialog';
import ListState from '../common/ListState';
import { formatDateTime } from '../../utils/format';
import { projectTypeChip } from '../../utils/projectTypes';

function EseguibileVersionsModal({ eseguibile, onClose, onUpdated }) {
  const toast = useToast();
  const confirmModal = useConfirmModal();
  const resource = useResource(() => listEseguibileVersions(eseguibile.id), {
    key: eseguibile.id,
    errorMessage: (exc) => humanizeError(exc).message,
  });
  const versions = resource.data || [];
  const actions = useBusy();
  const busy = actions.any;
  const [error, setError] = React.useState(null);

  const runAction = async (perform, success) => {
    setError(null);
    try {
      const r = await actions.run('version', perform);
      if (r.skipped) return;
      toast.success(success(r.value));
      await resource.reload();
      onUpdated?.(r.value);
    } catch (exc) {
      setError(humanizeError(exc).message);
    }
  };

  const republish = async () => {
    if (busy) return;
    const ok = await confirmModal.confirm({
      title: 'Ripubblicare dal progetto?',
      message: 'Viene creata una nuova versione dallo stato attuale del progetto e diventa quella attiva: '
        + 'le prossime esecuzioni la useranno. Le esecuzioni già avviate continuano con la loro versione.',
      confirmText: 'Ripubblica',
      variant: 'warning',
    });
    if (!ok) return;
    await runAction(() => republishEseguibile(eseguibile.id), (updated) => `Pubblicata la versione ${updated.active_version}`);
  };

  const activate = async (version) => {
    if (busy) return;
    const ok = await confirmModal.confirm({
      title: `Ripristinare la versione ${version.version_number}?`,
      message: `La versione ${version.version_number} diventa quella attiva: le prossime esecuzioni la useranno `
        + 'al posto di quella attuale. Le esecuzioni già avviate continuano con la loro versione.',
      confirmText: 'Ripristina',
      variant: 'warning',
    });
    if (!ok) return;
    await runAction(() => activateEseguibileVersion(eseguibile.id, version.id), () => `Versione ${version.version_number} attivata`);
  };

  return (
    <ModalWrapper
      isOpen
      onClose={onClose}
      title={`Versioni - ${eseguibile.name}`}
      icon="V"
      size="md"
      busy={busy}
      footer={(
        <>
          <ModalButton variant="secondary" onClick={onClose} disabled={busy}>Chiudi</ModalButton>
          <ModalButton variant="primary" onClick={republish} loading={busy} disabled={busy}>
            Ripubblica dal progetto
          </ModalButton>
        </>
      )}
    >
      <ConfirmDialog {...confirmModal} />
      <div className="esv">
        <p className="esv__info">
          La ripubblicazione crea uno snapshot immutabile. Le esecuzioni gia'
          avviate continuano a usare la versione selezionata al loro avvio.
        </p>
        {error && <div className="esv__error" role="alert">{error}</div>}
        <ListState
          resource={resource}
          loading={<div className="esv__loading">Caricamento versioni...</div>}
          errorTitle="Versioni non caricate"
          empty="Nessuna versione."
        >
          <div className="esv__list">
            {versions.map(version => (
              <div className={`esv__item ${version.active ? 'esv__item--active' : ''}`} key={version.id}>
                <div>
                  <strong>Versione {version.version_number}</strong>
                  <span>{projectTypeChip(version.project_type)} · {version.inputs_count} input</span>
                  <small>{formatDateTime(version.created_at)}</small>
                </div>
                {version.active ? (
                  <span className="esv__active">Attiva</span>
                ) : (
                  <button type="button" onClick={() => activate(version)} disabled={busy}>
                    Ripristina
                  </button>
                )}
              </div>
            ))}
          </div>
        </ListState>
      </div>
    </ModalWrapper>
  );
}

export default EseguibileVersionsModal;
