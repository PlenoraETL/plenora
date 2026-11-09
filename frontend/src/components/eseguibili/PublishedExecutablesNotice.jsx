/**
 * PublishedExecutablesNotice — nell'editor del progetto sorgente, gli
 * Eseguibili pubblicati da qui con una copia superata dalle ultime modifiche
 * (N1, regola e testi in ./SourceStatus). L'azione apre le versioni con
 * l'anteprima della ripubblicazione.
 */
import React, { useState } from 'react';
import { listSourceStatusOfProject } from '../../api';
import { useResource } from '../../hooks/useResource';
import { ResourceError } from '../common/ListState';
import { can } from '../../utils/permissions';
import useCanvasStore from '../../store/useCanvasStore';
import EseguibileVersionsModal from './EseguibileVersionsModal';
import { PUBLISH_NEW_VERSION, sourceChangedText } from './SourceStatus';

/**
 * Editor del progetto sorgente: un avviso per ogni Eseguibile pubblicato da
 * qui con una copia superata. Solo per chi puo' ripubblicare; si rilegge a
 * ogni salvataggio del canvas (il confronto cambia).
 */
export default function PublishedExecutablesNotice({ projectId, user }) {
  const savedAt = useCanvasStore(s => s.lastSavedServerAt);
  const enabled = Boolean(projectId) && can.manageEseguibile(user);
  const resource = useResource(() => listSourceStatusOfProject(projectId), {
    key: ['eseguibili-source-status', projectId || null, savedAt || null],
    enabled,
    keepPreviousData: true,
  });
  const [open, setOpen] = useState(null);
  if (!enabled) return null;
  const changed = (resource.data || []).filter(s => s.changed);
  return (
    <>
      <ResourceError resource={resource} title="Stato degli Eseguibili non caricato" compact />
      {changed.map(status => (
        <div key={status.eseguibile_id} className="draft-offer draft-offer--notice" role="status">
          <span>{sourceChangedText(status)}</span>
          <button type="button" className="etb-btn" onClick={() => setOpen(status)}>{PUBLISH_NEW_VERSION}</button>
        </div>
      ))}
      {open && (
        <EseguibileVersionsModal
          eseguibile={{ id: open.eseguibile_id, name: open.name }}
          onClose={() => setOpen(null)}
          onUpdated={() => resource.reload()}
        />
      )}
    </>
  );
}
