import React from 'react';

/**
 * Banner di risultato post-publish IMDF (success o failure).
 *
 * Pure presentation: legge `lastResult` (l'oggetto `imdf.publish` dello
 * store canvas, popolato dal polling Celery task) e renderizza statistiche
 * per layer + link al Feature Service. Non si mostra durante una pub in
 * corso ne` quando c'e' un error message attivo (gestiti dal parent).
 */
function ImdfPublishResult({ lastResult }) {
  if (!lastResult) return null;

  return (
    <div className={`imdf-pub-node__result ${lastResult.success ? 'imdf-pub-node__result--success' : 'imdf-pub-node__result--error'}`}>
      <div className="imdf-pub-node__result-title">
        {lastResult.success ? '✓ Pubblicato su ArcGIS' : '✗ Errore pubblicazione'}
        {lastResult.mode ? ` (${lastResult.mode})` : ''}
      </div>

      {lastResult.success && Array.isArray(lastResult.layers) && (
        <div className="imdf-pub-node__result-stats">
          {lastResult.layers.map((l) => {
            const bits = [];
            if (l.updated_count) bits.push(`~${l.updated_count}`);
            if (l.added_count)   bits.push(`+${l.added_count}`);
            if (l.deleted_count) bits.push(`−${l.deleted_count}`);
            if (l.skipped_count) bits.push(`×${l.skipped_count}`);
            if (bits.length === 0) bits.push(`${l.feature_count ?? 0} feat.`);
            return (
              <div key={l.id ?? l.name} className="imdf-pub-node__result-row">
                <span>{l.name}</span>
                <span>{bits.join(' ')}</span>
              </div>
            );
          })}
        </div>
      )}

      {lastResult.success && lastResult.renderers_applied != null && (
        <div className="imdf-pub-node__result-row">
          <span>Renderer applicati</span>
          <span>{lastResult.renderers_applied}/{lastResult.layers?.length ?? 0}</span>
        </div>
      )}

      {lastResult.service_url && (
        <a
          href={lastResult.service_url}
          target="_blank"
          rel="noopener noreferrer"
          className="imdf-pub-node__result-url"
          title={lastResult.service_url}
          onClick={(e) => e.stopPropagation()}
        >
          ↗ Apri Feature Service
        </a>
      )}

      {!lastResult.success && Array.isArray(lastResult.errors) && lastResult.errors.length > 0 && (
        <div className="imdf-pub-node__result-err">
          {lastResult.errors.slice(0, 3).join(' · ')}
        </div>
      )}
    </div>
  );
}

export default ImdfPublishResult;
