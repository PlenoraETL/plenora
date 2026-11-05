import React, { useCallback, useEffect, useState } from 'react';
import {
  activateEseguibileVersion,
  listEseguibileVersions,
  republishEseguibile,
} from '../../api';
import { humanizeError } from '../../utils/errorMessages';
import { useToast } from '../../hooks/useToast';
import ModalWrapper, { ModalButton } from '../common/ModalWrapper';

function EseguibileVersionsModal({ eseguibile, onClose, onUpdated }) {
  const toast = useToast();
  const [versions, setVersions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setVersions(await listEseguibileVersions(eseguibile.id));
    } catch (exc) {
      setError(humanizeError(exc).message);
    } finally {
      setLoading(false);
    }
  }, [eseguibile.id]);

  useEffect(() => { load(); }, [load]);

  const republish = async () => {
    setBusy(true);
    setError(null);
    try {
      const updated = await republishEseguibile(eseguibile.id);
      toast.success(`Pubblicata la versione ${updated.active_version}`);
      await load();
      onUpdated?.(updated);
    } catch (exc) {
      setError(humanizeError(exc).message);
    } finally {
      setBusy(false);
    }
  };

  const activate = async version => {
    setBusy(true);
    setError(null);
    try {
      const updated = await activateEseguibileVersion(eseguibile.id, version.id);
      toast.success(`Versione ${version.version_number} attivata`);
      await load();
      onUpdated?.(updated);
    } catch (exc) {
      setError(humanizeError(exc).message);
    } finally {
      setBusy(false);
    }
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
          <ModalButton variant="primary" onClick={republish} loading={busy}>
            Ripubblica dal progetto
          </ModalButton>
        </>
      )}
    >
      <div className="esv">
        <p className="esv__info">
          La ripubblicazione crea uno snapshot immutabile. Le esecuzioni gia'
          avviate continuano a usare la versione selezionata al loro avvio.
        </p>
        {error && <div className="esv__error">{error}</div>}
        {loading ? <div className="esv__loading">Caricamento versioni...</div> : (
          <div className="esv__list">
            {versions.map(version => (
              <div className={`esv__item ${version.active ? 'esv__item--active' : ''}`} key={version.id}>
                <div>
                  <strong>Versione {version.version_number}</strong>
                  <span>{version.project_type.toUpperCase()} - {version.inputs_count} input</span>
                  <small>{new Date(version.created_at).toLocaleString('it-IT')}</small>
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
        )}
      </div>
    </ModalWrapper>
  );
}

export default EseguibileVersionsModal;
