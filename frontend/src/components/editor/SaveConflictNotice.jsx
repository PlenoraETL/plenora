/**
 * SaveConflictNotice — avviso del salvataggio rifiutato perche' il progetto
 * sul server e' andato oltre la versione della scheda (409
 * canvas_version_conflict; Blocco A, regola 8, utils/canvasVersion).
 *
 * Niente e' stato sovrascritto. Le scelte (utils/serverActions):
 *  - "Ricarica la versione del server": le modifiche di questa scheda
 *    restano come sua copia di sicurezza, offerta al ricaricamento;
 *  - "Sovrascrivi con le mie modifiche": la stessa scrittura senza
 *    controllo di versione, scelta consapevole.
 */
import React, { useState } from 'react';
import useCanvasStore from '../../store/useCanvasStore';
import { overwriteServer, reloadServerVersion } from '../../utils/serverActions';

const hhmm = (iso) => {
  const t = iso ? Date.parse(iso) : NaN;
  return Number.isFinite(t) ? new Date(t).toLocaleTimeString('it-IT', { hour: '2-digit', minute: '2-digit' }) : null;
};

export default function SaveConflictNotice() {
  const conflict = useCanvasStore(s => s.saveConflict);
  const isReadOnly = useCanvasStore(s => s.isReadOnly);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState(null);
  if (!conflict || isReadOnly) return null;

  const when = hhmm(conflict.serverUpdatedAt);
  const what = conflict.kind === 'restore' ? 'Il ripristino della versione non è stato fatto' : 'Le tue modifiche non sono state salvate';

  const reload = () => {
    const res = reloadServerVersion();
    if (!res.ok) setProblem('Il browser non permette di tenere una copia delle modifiche: prima di ricaricare salvale altrove o sovrascrivi.');
  };
  const overwrite = async () => {
    setBusy(true);
    setProblem(null);
    try {
      await overwriteServer();
    } catch (e) {
      setProblem(`Sovrascrittura non riuscita: ${e?.message || 'errore di comunicazione col server'}.`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="draft-offer draft-offer--conflict" role="alert">
      <span>
        {what}: il progetto è stato salvato da un&apos;altra scheda o da un altro utente
        {when ? ` alle ${when}` : ''}, dopo che l&apos;hai aperto. Sul server niente è stato sovrascritto.
        {' '}Ricaricando, le tue modifiche restano come copia di sicurezza di questa scheda.
        {problem && <strong> {problem}</strong>}
      </span>
      <button type="button" className="etb-btn etb-btn--primary" disabled={busy} onClick={reload}>
        Ricarica la versione del server
      </button>
      <button type="button" className="etb-btn" disabled={busy} onClick={overwrite}>
        Sovrascrivi con le mie modifiche
      </button>
    </div>
  );
}
