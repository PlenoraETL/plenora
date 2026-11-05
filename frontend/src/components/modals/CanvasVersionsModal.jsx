/**
 * CanvasVersionsModal.jsx — Versioning del canvas (#12).
 *
 * Lista le versioni salvate di un progetto, permette di salvarne una nuova
 * (snapshot del canvas corrente) e di ripristinarne una. Il ripristino
 * sovrascrive il canvas lato server (passa per update_canvas → reset
 * canvas_validated + cascata), condizionato alla versione della scheda, e
 * ricarica l'editor per mostrarlo.
 *
 * Ripristino (Fase 5, T4, N8): chiede sempre con il dialog del progetto e,
 * con modifiche non salvate, dice che verranno sostituite. Dalla conferma al
 * ricaricamento la finestra resta aperta e occupata (`restoring`): nessun
 * secondo ripristino, nessuna chiusura, nessuna scorciatoia sul canvas
 * dietro (stack unico delle finestre), quindi nessuna modifica persa nella
 * pausa prima del reload.
 */
import React, { useState, useEffect, useRef } from 'react';
import { useResource } from '../../hooks/useResource';
import { ResourceError } from '../common/ListState';
import ModalWrapper, { ModalButton } from '../common/ModalWrapper';
import ConfirmDialog from '../common/ConfirmDialog';
import { useConfirmModal } from '../../hooks/useConfirmModal';
import { useToast } from '../../hooks/useToast';
import useCanvasStore from '../../store/useCanvasStore';
import { hasUnsavedChanges } from '../../store/saveState';
import { restoreVersion } from '../../utils/serverActions';
import { toastApiError } from '../../utils/toastApiError';
import {
  createCanvasVersion, listCanvasVersions,
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
  const [saving, setSaving] = useState(false);
  const [label, setLabel] = useState('');
  const [restoring, setRestoring] = useState(null); // id della versione in ripristino
  const restoringRef = useRef(false);
  const confirmModal = useConfirmModal();
  // Una versione fotografa il canvas SALVATO (Fase 4, M7): con modifiche
  // non salvate si chiede di salvare prima, per non perderle di vista.
  const unsaved = useCanvasStore(hasUnsavedChanges);
  const isReadOnly = useCanvasStore(s => s.isReadOnly);

  // Fase 5 T2: versioni dall'hook comune, chieste a ogni apertura (la
  // chiave cambia), errore con «Riprova» al posto di "Nessuna versione".
  const [openCount, setOpenCount] = useState(0);
  useEffect(() => { if (isOpen) setOpenCount(n => n + 1); }, [isOpen]);
  const resource = useResource(async () => (await listCanvasVersions(projectId)).versions || [], {
    key: [projectId, openCount],
    enabled: Boolean(isOpen && projectId),
    keepPreviousData: true,
  });
  const versions = resource.data || [];
  const loading = resource.initialLoading;
  const load = resource.reload;

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

  const handleRestore = async (version) => {
    if (isReadOnly || restoringRef.current) return;
    restoringRef.current = true;
    const name = version.label || fmtDate(version.created_at) || 'la versione scelta';
    const ok = await confirmModal.confirm({
      title: 'Ripristinare questa versione?',
      message: unsaved
        ? `Ci sono modifiche non salvate: il ripristino di «${name}» le scarta (non restano nemmeno come copia di sicurezza). Per tenerle, annulla e salva prima.`
        : `Il canvas salvato viene sostituito da «${name}» e l'editor si ricarica.`,
      confirmText: unsaved ? 'Ripristina e scarta le modifiche' : 'Ripristina',
      variant: unsaved ? 'danger' : 'warning',
    });
    if (!ok) { restoringRef.current = false; return; }
    setRestoring(version.id);
    try {
      // Scrittura condizionata alla versione della scheda (Blocco A, regola
      // 8): se il server e' andato oltre, niente ripristino e l'avviso
      // dell'editor offre le scelte. Riuscito: la copia di sicurezza di
      // QUESTA scheda non vale piu' (le altre restano e vengono avvisate) e
      // l'editor si ricarica dal percorso normale di apertura
      // (utils/serverActions.restoreVersion).
      const response = await restoreVersion(version.id);
      // Riuscito: si resta occupati fino al ricaricamento della pagina.
      if (response) { toast.success('Versione ripristinata, ricarico l’editor…'); return; }
    } catch (err) {
      toastApiError(toast, err, 'Errore ripristino versione');
    }
    restoringRef.current = false;
    setRestoring(null);
  };

  if (!isOpen) return null;

  return (
    <ModalWrapper
      isOpen
      onClose={onClose}
      title="Versioni del canvas"
      icon="🕘"
      size="md"
      busy={Boolean(restoring)}
      footer={<ModalButton variant="secondary" onClick={onClose} disabled={Boolean(restoring)}>Chiudi</ModalButton>}
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

        {/* Errore sempre visibile: anche sopra i dati gia' caricati. */}
        <ResourceError resource={resource} title="Versioni non caricate" />

        {loading ? (
          <div className="cv-versions__empty">Caricamento…</div>
        ) : resource.status === 'error' && !resource.hasData ? null : versions.length === 0 ? (
          <div className="cv-versions__empty">
            Nessuna versione salvata. Salva una copia del canvas corrente per
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
                  className={`cv-versions__restore${restoring === v.id ? ' cv-versions__restore--confirm' : ''}`}
                  onClick={() => handleRestore(v)}
                  disabled={Boolean(restoring)}
                >
                  {restoring === v.id ? 'Ripristino…' : '↩ Ripristina'}
                </button>}
              </li>
            ))}
          </ul>
        )}
      </div>
      <ConfirmDialog {...confirmModal} />
    </ModalWrapper>
  );
}
