/**
 * ChangePasswordModal — modal per il cambio password personale.
 *
 * Disponibile a qualsiasi utente autenticato (viewer incluso).
 * Richiede la password attuale per sicurezza.
 *
 * Props:
 *   - onClose: () => void   chiude il modal
 *
 * Uso:
 *   {showPwdModal && <ChangePasswordModal onClose={() => setShowPwdModal(false)} />}
 */

import React, { useState } from 'react';
import { changeMyPassword } from '../api/users';
import ModalWrapper, { ModalButton } from './common/ModalWrapper';
import { PASSWORD_MIN_LENGTH, passwordProblem } from '../utils/passwordRule';
import './ChangePasswordModal.css';
import { requestErrorMessage } from '../utils/errorMessages';

export default function ChangePasswordModal({ onClose }) {
  const [oldPwd, setOldPwd] = useState('');
  const [newPwd, setNewPwd] = useState('');
  const [confirmPwd, setConfirmPwd] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState(false);

  const handleSubmit = async (e) => {
    e?.preventDefault?.();
    setError('');

    if (!oldPwd) return setError('Inserisci la password attuale');
    // Regola unica della password (utils/passwordRule, uguale al backend).
    const problem = newPwd ? passwordProblem(newPwd) : 'Inserisci la nuova password';
    if (problem) return setError(problem);
    if (newPwd !== confirmPwd) return setError('Le due password non coincidono');
    if (newPwd === oldPwd) return setError('La nuova password deve essere diversa dall\'attuale');

    setLoading(true);
    try {
      await changeMyPassword(oldPwd, newPwd);
      setSuccess(true);
      setTimeout(() => onClose(), 1500);
    } catch (e) {
      const msg = requestErrorMessage(e, 'Errore durante il cambio password');
      setError(msg);
    } finally {
      setLoading(false);
    }
  };

  // Enter su confirmPwd -> submit (form nativo non utilizzabile perche'
  // il footer di ModalWrapper vive fuori dal children).
  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !loading && !success) {
      e.preventDefault();
      handleSubmit();
    }
  };

  return (
    <ModalWrapper
      isOpen={true}
      onClose={onClose}
      title="Cambia password"
      icon="🔑"
      size="sm"
      busy={loading || success}
      footer={
        <>
          <ModalButton variant="secondary" onClick={onClose} disabled={loading}>
            Annulla
          </ModalButton>
          <ModalButton variant="primary" onClick={handleSubmit} loading={loading} disabled={success}>
            {success ? '✓ Salvato' : 'Cambia password'}
          </ModalButton>
        </>
      }
    >
      {error && <div className="cpm__error">{error}</div>}
      {success && <div className="cpm__success">✓ Password aggiornata</div>}

      <div className="cpm__field">
        <label>Password attuale</label>
        <input
          type="password"
          value={oldPwd}
          onChange={(e) => setOldPwd(e.target.value)}
          onKeyDown={handleKeyDown}
          autoFocus
          autoComplete="current-password"
          disabled={loading || success}
        />
      </div>

      <div className="cpm__field">
        <label>Nuova password</label>
        <input
          type="password"
          value={newPwd}
          onChange={(e) => setNewPwd(e.target.value)}
          onKeyDown={handleKeyDown}
          autoComplete="new-password"
          placeholder={`Almeno ${PASSWORD_MIN_LENGTH} caratteri`}
          disabled={loading || success}
        />
      </div>

      <div className="cpm__field">
        <label>Conferma nuova password</label>
        <input
          type="password"
          value={confirmPwd}
          onChange={(e) => setConfirmPwd(e.target.value)}
          onKeyDown={handleKeyDown}
          autoComplete="new-password"
          disabled={loading || success}
        />
      </div>
    </ModalWrapper>
  );
}
