import React, { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { createProject } from '../../api';
import { useToast } from '../../hooks/useToast';
import ModalWrapper, { ModalButton } from '../common/ModalWrapper';
import { toastApiError } from '../../utils/toastApiError';

const TYPE_META = {
  estrai:      { icon: '↓', label: 'Estrai',      btn: '↓ Estrai' },
  manipola:    { icon: '⚙', label: 'Manipola',    btn: '⚙ Manipola' },
  arricchisci: { icon: '◈', label: 'Arricchisci', btn: '◈ Arricchisci' },
};

function NewProjectModal({
  isOpen,
  initialType = 'estrai',
  connections = [],
  arcgisConnections = [],
  onClose,
  onCreated,
}) {
  const toast = useToast();
  const navigate = useNavigate();
  const [name, setName] = useState('');
  const [desc, setDesc] = useState('');
  const [type, setType] = useState(initialType);
  const [connId, setConnId] = useState('');
  const [creating, setCreating] = useState(false);

  // Re-sync `type` quando il parent cambia `initialType` (es. click su
  // "+ Nuovo Estrai" vs "+ Nuovo Manipola" da sezioni diverse della home).
  useEffect(() => {
    if (isOpen) {
      setType(initialType);
      setName('');
      setDesc('');
      setConnId('');
    }
  }, [isOpen, initialType]);

  if (!isOpen) return null;

  const handleCreate = async () => {
    if (creating) return;
    if (!name.trim()) { toast.warning('Inserisci un nome'); return; }
    if (type === 'estrai' && !connId) {
      toast.warning('Seleziona una connessione'); return;
    }
    setCreating(true);
    try {
      await createProject(name, desc, type, connId);
      onCreated?.();
      onClose?.();
    } catch (err) {
      toastApiError(toast, err, 'Errore creazione');
    } finally {
      setCreating(false);
    }
  };

  const meta = TYPE_META[type] || TYPE_META.estrai;

  return (
    <ModalWrapper
      isOpen={true}
      onClose={onClose}
      title={`Nuovo progetto ${meta.label}`}
      icon={meta.icon}
      size="md"
      closeOnOverlay={!creating}
      closeOnEsc={!creating}
      footer={
        <>
          <ModalButton variant="secondary" onClick={onClose} disabled={creating}>Annulla</ModalButton>
          <ModalButton variant="primary" onClick={handleCreate} loading={creating}>
            Crea Progetto
          </ModalButton>
        </>
      }
    >
      <div className="pl__modal-field">
        <label className="pl__modal-label">Tipo</label>
        <div className="pl__modal-type-row">
          {Object.keys(TYPE_META).map(t => (
            <button
              key={t}
              type="button"
              className={`pl__modal-type-btn pl__modal-type-btn--${t} ${type === t ? 'pl__modal-type-btn--on' : ''}`}
              onClick={() => setType(t)}
            >
              {TYPE_META[t].btn}
            </button>
          ))}
        </div>
      </div>

      <div className="pl__modal-field">
        <label className="pl__modal-label">Nome *</label>
        <input
          type="text"
          className="pl__modal-input"
          value={name}
          onChange={e => setName(e.target.value)}
          placeholder="Es. 'Catasto Campania Q2'"
          autoFocus
        />
      </div>

      <div className="pl__modal-field">
        <label className="pl__modal-label">Descrizione</label>
        <textarea
          className="pl__modal-textarea"
          value={desc}
          onChange={e => setDesc(e.target.value)}
          placeholder="Descrizione opzionale..."
          rows={2}
        />
      </div>

      {type === 'estrai' && (
        <div className="pl__modal-field">
          <label className="pl__modal-label" htmlFor="new-project-connection">Connessione *</label>
          <select
            id="new-project-connection"
            className="pl__modal-select"
            value={connId}
            onChange={e => setConnId(e.target.value)}
          >
            <option value="">-- Seleziona connessione --</option>
            {connections.length > 0 && (
              <optgroup label="Database">
                {connections.map(c => <option key={c.id} value={c.id}>{c.name} ({c.database})</option>)}
              </optgroup>
            )}
            {arcgisConnections.length > 0 && (
              <optgroup label="ArcGIS Server">
                {arcgisConnections.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
              </optgroup>
            )}
          </select>
          {connections.length === 0 && arcgisConnections.length === 0 && (
            <p className="pl__modal-error">
              Nessuna connessione.{' '}
              <span
                className="pl__modal-error-link"
                onClick={() => { onClose?.(); navigate('/connections'); }}
              >
                Creane una
              </span>
            </p>
          )}
        </div>
      )}

      {type === 'manipola' && (
        <div className="pl__modal-info pl__modal-info--manipola">
          💡 I progetti Manipola usano come input i risultati dei progetti Estrai.
        </div>
      )}

      {type === 'arricchisci' && (
        <div className="pl__modal-info pl__modal-info--arricchisci">
          💡 I progetti Arricchisci chiamano API esterne per arricchire i dati estratti.
        </div>
      )}
    </ModalWrapper>
  );
}

export default NewProjectModal;
