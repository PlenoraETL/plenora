/**
 * CanvasVersionsModal.jsx — Versioning del canvas (#12).
 *
 * Lista le versioni salvate di un progetto, permette di salvarne una nuova
 * (snapshot del canvas corrente) e di ripristinarne una. Il ripristino
 * sovrascrive il canvas lato server (passa per update_canvas → reset
 * canvas_validated + cascata) e ricarica l'editor per mostrarlo.
 */
import React, { useState, useEffect, useCallback } from 'react';
import ModalWrapper, { ModalButton } from '../common/ModalWrapper';
import { useToast } from '../../hooks/useToast';
import useCanvasStore from '../../store/useCanvasStore';
import { hasUnsavedChanges } from '../../store/saveState';
import { clearDraft } from '../../utils/localDrafts';
import { toastApiError } from '../../utils/toastApiError';
import {
  createCanvasVersion, listCanvasVersions, restoreCanvasVersion,
} from '../../api/projects';
import './CanvasVersionsModal.css';

function fmtDate(d) {
  if (!d) return '';
  try {
    return new Date(d).toLocaleString('it-IT', {
      day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit',
    });
  } catch { return String(d); }
}

export default function CanvasVersionsModal({ isOpen, onClose, projectId }) {
  const toast = useToast();
  const [versions, setVersions] = useState([]);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [label, setLabel] = useState('');
  const [confirmId, setConfirmId] = useState(null); // versione in attesa di conferma ripristino
  // Una versione fotografa il canvas SALVATO (Fase 4, M7): con modifiche
  // non salvate si chiede di salvare prima, per non perderle di vista.
  const unsaved = useCanvasStore(hasUnsavedChanges);
  const isReadOnly = useCanvasStore(s => s.isReadOnly);

  const load = useCallback(async () => {
    if (!projectId) return;
    setLoading(true);
    try {
      const res = await listCanvasVersions(projectId);
      setVersions(res.versions || []);
    } catch (err) {
      toastApiError(toast, err, 'Errore caricamento versioni');
    } finally {
      setLoading(false);
    }
  }, [projectId, toast]);

  useEffect(() => { if (isOpen) { setConfirmId(null); load(); } }, [isOpen, load]);

  const handleSave = async () => {
    if (unsaved || isReadOnly) return;
    setSaving(true);
    try {
      await createCanvasVersion(projectId, label.trim() || null);
      setLabel('');
      toast.success('Versione salvata');
      await load();
    } catch (err) {
      toastApiError(toast, err, 'Errore salvataggio versione');
    } finally {
      setSaving(false);
    }
  };

  const handleRestore = async (versionId) => {
    if (isReadOnly) return;
    if (confirmId !== versionId) { setConfirmId(versionId); return; }
    try {
      await restoreCanvasVersion(projectId, versionId);
      toast.success('Versione ripristinata, ricarico l’editor…');
      // Ricarica l'editor per mostrare il canvas ripristinato attraverso il
      // normale percorso di load (deserializzazione per-editor). Le modifiche
      // locali sono superate dal ripristino confermato: il canvas e' pulito,
      // altrimenti l'avviso "modifiche non salvate" bloccava il ricaricamento.
      // Il canvas del server e' cambiato: la copia di sicurezza non vale piu'.
      useCanvasStore.getState().markClean();
      clearDraft(projectId);
      setTimeout(() => window.location.reload(), 600);
    } catch (err) {
      toastApiError(toast, err, 'Errore ripristino versione');
      setConfirmId(null);
    }
  };

  if (!isOpen) return null;

  return (
    <ModalWrapper
      isOpen
      onClose={onClose}
      title="Versioni del canvas"
      icon="🕘"
      size="md"
      footer={<ModalButton variant="secondary" onClick={onClose}>Chiudi</ModalButton>}
    >
      <div className="cv-versions">
        {unsaved && !isReadOnly && (
          <div className="cv-versions__empty" role="note">
            Ci sono modifiche non salvate: una versione fotografa il canvas salvato. Salva prima, poi crea la versione.
          </div>
        )}
        {!isReadOnly && (
        <div className="cv-versions__save">
          <input
            className="cv-versions__label-input"
            placeholder="Etichetta (opzionale, es. “prima del refactor”)"
            value={label}
            onChange={e => setLabel(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') handleSave(); }}
            maxLength={120}
          />
          <ModalButton variant="primary" onClick={handleSave} disabled={saving || unsaved}>
            💾 Salva versione attuale
          </ModalButton>
        </div>
        )}

        {loading ? (
          <div className="cv-versions__empty">Caricamento…</div>
        ) : versions.length === 0 ? (
          <div className="cv-versions__empty">
            Nessuna versione salvata. Salva uno snapshot del canvas corrente per
            poterci tornare in seguito.
          </div>
        ) : (
          <ul className="cv-versions__list">
            {versions.map(v => (
              <li key={v.id} className="cv-versions__item">
                <div className="cv-versions__meta">
                  <div className="cv-versions__name">{v.label || '(senza etichetta)'}</div>
                  <div className="cv-versions__sub">
                    {fmtDate(v.created_at)}{v.created_by ? ` · ${v.created_by}` : ''}
                  </div>
                </div>
                {!isReadOnly && <button
                  type="button"
                  className={`cv-versions__restore${confirmId === v.id ? ' cv-versions__restore--confirm' : ''}`}
                  onClick={() => handleRestore(v.id)}
                  onBlur={() => confirmId === v.id && setConfirmId(null)}
                >
                  {confirmId === v.id ? 'Confermi? (sovrascrive)' : '↩ Ripristina'}
                </button>}
              </li>
            ))}
          </ul>
        )}
      </div>
    </ModalWrapper>
  );
}
