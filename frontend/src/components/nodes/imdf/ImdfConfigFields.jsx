import React from 'react';

export const PUBLISH_MODE_OPTIONS = [
  { value: 'replace', label: '↻ Replace', desc: 'Cancella FS esistente e ricrea da zero' },
  { value: 'append',  label: '+ Append',  desc: 'Aggiunge features al FS esistente (no duplicate check)' },
  { value: 'upsert',  label: '⇵ Upsert',  desc: 'Cancella per business key e re-insert (nuovi OBJECTID)' },
  { value: 'update',  label: '✎ Update',  desc: 'Aggiorna features esistenti via OBJECTID (preserva history). Skip unmatched' },
];

/**
 * Config fields del nodo IMDF Publisher: banner access-denied + status
 * bundle + 4 form fields (Connessione / Cartella / Nome FS / Mode) +
 * checkbox apply_renderers (solo mode='replace').
 *
 * Tutti i field scrivono via `onUpdate(fieldKey, value)`; il parent
 * propaga al canvas store. Niente state locale.
 */
function ImdfConfigFields({
  // Valori da data del nodo
  connectionId,
  folderId,
  fsNameTemplate,
  publishMode,
  applyRenderers,
  // Stato RB.7.C
  isAccessDenied,
  accessDeniedReason,
  // Stato bundle (dallo store)
  bundleTaskId,
  bundleSiteId,
  // Liste dal hook useImdfConnectionsAndFolders
  connections,
  folders,
  loadingConns,
  loadingFolders,
  // Flag publishing → disable tutti i field
  publishing,
  // Callback di update generico
  onUpdate,
}) {
  return (
    <>
      {isAccessDenied && (
        <div
          className="plen-banner-warn"
          role="alert"
          title={accessDeniedReason || 'Connessione ArcGIS non accessibile'}
        >
          🔒 Connessione ArcGIS non accessibile al tuo gruppo.
          {accessDeniedReason ? (
            <div className="plen-banner-warn__detail">{accessDeniedReason}</div>
          ) : null}
        </div>
      )}

      <div className={`imdf-pub-node__bundle ${bundleTaskId ? 'ok' : 'missing'}`}>
        {bundleTaskId ? (
          <>
            📦 Bundle: <strong>{bundleSiteId || bundleTaskId.slice(0, 8)}</strong>
            <span className="imdf-pub-node__bundle-hint">
              (gestito dall'Output node)
            </span>
          </>
        ) : (
          <>⚠ Nessun bundle — esegui il canvas o selezionalo dall'Output</>
        )}
      </div>

      <div className="imdf-pub-node__section">
        <label className="imdf-pub-node__label">Connessione ArcGIS</label>
        <select
          value={connectionId}
          onChange={e => onUpdate('imdf_arcgis_connection_id', e.target.value)}
          className="imdf-pub-node__select"
          disabled={loadingConns || publishing}
        >
          <option value="">
            {loadingConns ? 'Caricamento...' : 'Seleziona connessione...'}
          </option>
          {connections.map(c => (
            <option key={c.id} value={c.id}>
              {c.name}{c.server_type ? ` (${c.server_type})` : ''}
            </option>
          ))}
        </select>
      </div>

      <div className="imdf-pub-node__section">
        <label className="imdf-pub-node__label">Cartella destinazione</label>
        <select
          value={folderId}
          onChange={e => onUpdate('imdf_arcgis_folder', e.target.value)}
          className="imdf-pub-node__select"
          disabled={!connectionId || loadingFolders || publishing}
        >
          <option value="">{loadingFolders ? 'Caricamento...' : '(root)'}</option>
          {folders.filter(f => f.id).map((f, i) => (
            <option key={i} value={f.title || f.id}>{f.title || f.id}</option>
          ))}
        </select>
      </div>

      <div className="imdf-pub-node__section">
        <label className="imdf-pub-node__label">Nome Feature Service</label>
        <input
          type="text"
          value={fsNameTemplate}
          onChange={e => onUpdate('imdf_fs_name_template', e.target.value)}
          placeholder="es. imdf_LO2244"
          className="imdf-pub-node__input"
          disabled={publishing}
        />
      </div>

      <div className="imdf-pub-node__section">
        <label className="imdf-pub-node__label">Modalita'</label>
        <select
          value={publishMode}
          onChange={e => onUpdate('imdf_publish_mode', e.target.value)}
          className="imdf-pub-node__select"
          disabled={publishing}
        >
          {PUBLISH_MODE_OPTIONS.map(m => (
            <option key={m.value} value={m.value}>{m.label}</option>
          ))}
        </select>
        <div className="imdf-pub-node__hint">
          {PUBLISH_MODE_OPTIONS.find(m => m.value === publishMode)?.desc}
        </div>
      </div>

      {publishMode === 'replace' && (
        <div className="imdf-pub-node__section">
          <label className="imdf-pub-node__checkbox">
            <input
              type="checkbox"
              checked={applyRenderers}
              onChange={e => onUpdate('imdf_apply_renderers', e.target.checked)}
              disabled={publishing}
            />
            <span>Applica stili da renderers.json</span>
          </label>
        </div>
      )}
    </>
  );
}

export default ImdfConfigFields;
