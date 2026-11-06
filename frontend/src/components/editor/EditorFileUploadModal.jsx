/**
 * EditorFileUploadModal — carica un file senza uscire dall'editor (UI-3, U30).
 *
 * Prima l'unica via era la pagina «File» (uscendo dall'editor). Qui: scelta
 * del file, nome, avanzamento del caricamento (U17) e, a file caricato, il
 * file entra nell'elenco delle sorgenti e nel canvas al centro della vista
 * (`onUploaded(file)`). Fogli e layer si scelgono nella gestione file (che si
 * apre in un'altra scheda, senza perdere l'editor): qui si carica il primo.
 */
import React, { useEffect, useState } from 'react';
import ModalWrapper, { ModalButton } from '../common/ModalWrapper';
import TransferProgress from '../common/TransferProgress';
import { uploadFile } from '../../api/files';
import { useTransferProgress } from '../../hooks/useTransferProgress';
import { requestErrorMessage } from '../../utils/errorMessages';
import { formatBytes } from '../../utils/format';
import { term } from '../../utils/glossary';
import { can } from '../../utils/permissions';
import { useAuth } from '../../context/AuthContext';

const ACCEPT = '.csv,.xlsx,.xls,.json,.geojson,.gpkg,.zip,.gdb,.tar.gz';
const baseName = (name) => String(name || '').replace(/\.(tar\.gz|[^.]+)$/i, '');

export default function EditorFileUploadModal({ isOpen, onClose, onUploaded, variant = 'manipola' }) {
  const [file, setFile] = useState(null);
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const transfer = useTransferProgress();
  const { user } = useAuth();
  const { reset } = transfer;

  useEffect(() => {
    if (isOpen) { setFile(null); setName(''); setError(null); setBusy(false); reset(); }
  }, [isOpen, reset]);

  const pick = (f) => {
    setFile(f || null);
    if (f && !name.trim()) setName(baseName(f.name));
  };

  const submit = async () => {
    // Stesso permesso della pagina File (l'editor non mostra il comando senza).
    if (!file || !name.trim() || busy || !can.uploadFile(user)) return;
    setBusy(true);
    setError(null);
    try {
      const created = await uploadFile(file, name.trim(), '', '', '', null, null, { onProgress: transfer.onProgress });
      // Il backend puo' restituire il file o un elenco (file a piu' layer).
      const first = Array.isArray(created) ? created[0] : (created?.file || created);
      await onUploaded?.(first);
      onClose();
    } catch (err) {
      setError(requestErrorMessage(err, 'Caricamento non riuscito'));
    } finally {
      setBusy(false);
      reset();
    }
  };

  const footer = (
    <>
      <ModalButton variant="secondary" onClick={onClose} disabled={busy}>Annulla</ModalButton>
      <ModalButton variant="primary" onClick={submit} disabled={!file || !name.trim() || busy}>
        {busy ? 'Caricamento…' : 'Carica e aggiungi al canvas'}
      </ModalButton>
    </>
  );

  return (
    <ModalWrapper isOpen={isOpen} onClose={onClose} title={term('upload_file').name} icon={term('upload_file').icon} size="md" variant={variant}
      footer={footer} busy={busy}>
      <form className="editor-upload" onSubmit={(e) => { e.preventDefault(); submit(); }}>
        <label className="form-group">
          <span>File</span>
          <input type="file" accept={ACCEPT} disabled={busy} onChange={e => pick(e.target.files?.[0])} />
        </label>
        {file && <p className="editor-upload__meta">{file.name} · {formatBytes(file.size)}</p>}
        <label className="form-group">
          <span>Nome</span>
          <input type="text" value={name} disabled={busy} onChange={e => setName(e.target.value)} placeholder="Nome del file nell'elenco" />
        </label>
        <p className="editor-upload__hint">
          Per scegliere un foglio, un layer o le colonne da importare usa la{' '}
          <a href="/files" target="_blank" rel="noreferrer">gestione file</a> (si apre in un&apos;altra scheda):
          qui si carica il primo foglio o layer con tutte le colonne.
        </p>
        <TransferProgress progress={transfer.progress} verb="Caricamento" />
        {error && <p className="editor-upload__error" role="alert">{error}</p>}
      </form>
    </ModalWrapper>
  );
}
