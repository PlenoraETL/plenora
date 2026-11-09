/**
 * SourceStatus — un Eseguibile pubblicato che non corrisponde piu' al suo
 * progetto (N1, 09/11/2026).
 *
 * La versione pubblicata e' una copia congelata (versione = release): resta
 * quella che si esegue. Il backend dice se il canvas attuale del progetto ha
 * un'altra impronta (regola unica `eseguibili.service.source_status`) e, in
 * quel caso, che cosa direbbe la ripubblicazione (i collegamenti degli input
 * che non troverebbero piu' il passo o il campo, con il motivo). Qui si
 * mostra, nello stesso modo, nell'editor del progetto, nella lista degli
 * Eseguibili e nella finestra delle versioni (anteprima della
 * ripubblicazione).
 */
import React from 'react';
import { formatDateTime } from '../../utils/format';
import './BindingWarnings.css';

export const SOURCE_CHANGED_BADGE = 'Progetto cambiato dopo la pubblicazione';
export const PUBLISH_NEW_VERSION = 'Pubblica nuova versione';

/** «L'Eseguibile «nome» (versione N del …) usa una copia del progetto precedente alle ultime modifiche.» */
export function sourceChangedText(status) {
  const version = status?.version ? `versione ${status.version}${status.published_at ? ` del ${formatDateTime(status.published_at)}` : ''}` : null;
  return `L'Eseguibile «${status?.name || ''}»${version ? ` (${version})` : ''} usa una copia del progetto precedente alle ultime modifiche.`;
}

/** Badge della lista: progetto cambiato, con l'azione che apre le versioni. */
export function SourceChangedBadge({ onPublish }) {
  return (
    <div className="binding-warnings" role="status">
      <strong>⚠ {SOURCE_CHANGED_BADGE}</strong>
      {onPublish && (
        <button type="button" className="esl__btn" onClick={onPublish}>{PUBLISH_NEW_VERSION}</button>
      )}
    </div>
  );
}

/** Anteprima della ripubblicazione: cosa non andrebbe, prima di provarci. */
export function RepublishPreview({ status }) {
  if (!status?.changed) return null;
  const problems = status.binding_problems || [];
  return (
    <div className="binding-warnings" role="status">
      <strong>⚠ {SOURCE_CHANGED_BADGE}</strong>
      <div>{sourceChangedText(status)}</div>
      {problems.length > 0 && (
        <>
          <div>Ripubblicando, questi input non troverebbero più il campo a cui sono collegati: correggili nel progetto.</div>
          <ul className="binding-warnings__list">
            {problems.map(p => (
              <li key={`${p.input_key}|${p.target_node_id}|${p.target_field || ''}`}>
                <code>{p.input_key}</code>: {p.motivo}
              </li>
            ))}
          </ul>
        </>
      )}
      {status.publish_error && <div>Ripubblicando: {status.publish_error}</div>}
    </div>
  );
}

/** La ripubblicazione non riuscirebbe (collegamenti rotti o altro rifiuto). */
export const republishBlocked = (status) => Boolean(status?.changed && ((status.binding_problems || []).length || status.publish_error));
