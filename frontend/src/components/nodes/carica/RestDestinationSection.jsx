import React, { useState } from 'react';
import { useResource } from '../../../hooks/useResource';
import '../../common/ListState.css';
import { listApiConnections } from '../../../api/apiConnections';
import {
  REST_AUTH_FIELD, isSecretHeader, replaceSecretsWithConnector, restSecretHeaderNames,
} from '../../../utils/restSecrets';
import {
  REST_METHODS,
  REST_BATCH_MODES as REST_BATCH_MODE_VALUES,
} from '../../../constants/canvasTypes';
import FieldInput from '../../common/FieldInput';
import { integer } from '../../../utils/fieldCodecs';
import { READ_ONLY_SAFE } from '../../common/readOnlySafe';

// Fase 5 T5: numeri dal convertitore unico (utils/fieldCodecs).
const REST_BATCH = integer({ min: 1, max: 10000 });
const REST_TIMEOUT = integer({ min: 1, max: 600 });

// Re-export per non rompere CaricanNode che importa REST_METHODS da
// questo file per `isRestValid` e Preview.
export { REST_METHODS };

// Labels UI restano locali (drop-down i18n-volatile). Il valore di
// `value` deve restare in REST_BATCH_MODE_VALUES; il contract test
// FE cattura ogni divergenza.
const REST_BATCH_MODES = [
  { value: REST_BATCH_MODE_VALUES[0], label: 'A lotti' },
  { value: REST_BATCH_MODE_VALUES[1], label: 'Per riga' },
];

// Credenziali per riferimento (audit R10.8): l'utente sceglie un connettore
// API, il server ne usa l'autenticazione cifrata. Un nodo con intestazioni
// segrete nel canvas e' "da migrare" (utils/restSecrets): l'avviso e' qui,
// sempre visibile; scegliere un connettore sostituisce le righe segrete (e lo
// dice), oppure l'utente le rimuove con la ×. Niente si toglie in silenzio.
// Connettori dall'hook comune (Fase 5 T2): errore con «Riprova».
function useApiConnectors() {
  const resource = useResource(async () => {
    const data = await listApiConnections();
    return Array.isArray(data) ? data : (data?.connections || data?.items || data?.api_connections || []);
  }, { key: 'rest-connectors' });
  const status = resource.status === 'error' ? 'error' : resource.hasData ? 'ready' : 'loading';
  return { status, items: resource.data || [], error: resource.error, reload: resource.reload };
}

function RestDestinationSection({
  restUrl,
  restMethod,
  restBatchMode,
  restBatchSize,
  restTimeoutSeconds,
  restHeaders,
  restAuthApiId = '',
  restBodyMapping,
  availableColumns,
  showMapping,
  onToggleMapping,
  showAdvanced,
  onToggleAdvanced,
  onUpdate,
  onMultiUpdate = null,
}) {
  const [replacedNotice, setReplacedNotice] = useState(null);
  const secretNames = restSecretHeaderNames({ rest_headers: restHeaders });
  const applyUpdates = (updates) => {
    if (onMultiUpdate) onMultiUpdate(updates);
    else Object.entries(updates).forEach(([field, value]) => onUpdate(field, value));
  };
  const handleAuthChange = (apiId) => {
    if (apiId && secretNames.length) {
      // Scelta esplicita: le credenziali vengono dal connettore, le righe
      // segrete del canvas vengono sostituite (e il nodo lo dice).
      const { headers, replaced } = replaceSecretsWithConnector(restHeaders);
      applyUpdates({ [REST_AUTH_FIELD]: apiId, rest_headers: headers });
      setReplacedNotice(replaced);
      return;
    }
    onUpdate(REST_AUTH_FIELD, apiId);
  };
  const handleRestHeaderChange = (index, field, value) => {
    const nextHeaders = [...restHeaders];
    nextHeaders[index] = { ...nextHeaders[index], [field]: value };
    onUpdate('rest_headers', nextHeaders);
  };

  const handleAddRestHeader = () => {
    onUpdate('rest_headers', [...restHeaders, { name: '', value: '' }]);
  };
  const connectors = useApiConnectors();
  const selectedMissing = restAuthApiId && connectors.status === 'ready'
    && !connectors.items.some((item) => item.id === restAuthApiId);

  const handleRemoveRestHeader = (index) => {
    onUpdate('rest_headers', restHeaders.filter((_, i) => i !== index));
  };

  const handleRestBodyMappingChange = (index, field, value) => {
    const nextMapping = [...restBodyMapping];
    nextMapping[index] = { ...nextMapping[index], [field]: value };
    onUpdate('rest_body_mapping', nextMapping);
  };

  const handleAddRestBodyMapping = () => {
    onUpdate('rest_body_mapping', [...restBodyMapping, { source: '', target: '', required: false }]);
  };

  const handleRemoveRestBodyMapping = (index) => {
    onUpdate('rest_body_mapping', restBodyMapping.filter((_, i) => i !== index));
  };

  return (
    <>
      <div className="carica-node__section">
        <label className="carica-node__label">Endpoint REST</label>
        <input
          type="url"
          value={restUrl}
          onChange={(e) => onUpdate('rest_url', e.target.value)}
          placeholder="https://api.example.com/items"
          className="carica-node__input"
        />
      </div>

      <div className="carica-node__section">
        <label className="carica-node__label" htmlFor="carica-rest-auth">Autenticazione</label>
        <select
          id="carica-rest-auth"
          value={restAuthApiId}
          onChange={(e) => handleAuthChange(e.target.value)}
          className="carica-node__select"
        >
          <option value="">Nessuna</option>
          {selectedMissing && <option value={restAuthApiId}>Connettore non disponibile ({restAuthApiId})</option>}
          {connectors.items.map((item) => (
            <option key={item.id} value={item.id}>Connettore API: {item.name}</option>
          ))}
        </select>
        <div className="carica-node__hint">
          {connectors.status === 'error'
            ? <>Elenco dei connettori non disponibile: {connectors.error} <button type="button" {...READ_ONLY_SAFE} className="nodrag list-state__retry" onClick={() => connectors.reload()}>Riprova</button></>
            : 'Le credenziali restano nel connettore API, cifrate sul server, e partono solo verso l’indirizzo del connettore: il canvas ne salva solo il riferimento.'}
        </div>
        {secretNames.length > 0 && (
          <div className="carica-node__hint carica-node__hint--warn" role="alert" data-testid="rest-secrets-migrate">
            Da migrare: questo nodo ha credenziali nel canvas ({secretNames.join(', ')}). Scegli un connettore
            API qui sopra (le sostituisce) oppure rimuovi le righe con la × tra le intestazioni.
            Finché restano, il progetto non si salva e non si esegue.
          </div>
        )}
        {replacedNotice && replacedNotice.length > 0 && secretNames.length === 0 && (
          <div className="carica-node__hint" role="status">
            Intestazioni sostituite dal connettore: {replacedNotice.join(', ')}. Annulla (Ctrl+Z) per ripristinarle.
          </div>
        )}
      </div>

      <div className="carica-node__section">
        <label className="carica-node__label">Metodo</label>
        <select
          value={restMethod}
          onChange={(e) => onUpdate('rest_method', e.target.value)}
          className="carica-node__select"
        >
          {REST_METHODS.map(method => (
            <option key={method} value={method}>{method}</option>
          ))}
        </select>
      </div>

      <div className="carica-node__section">
        <div
          className="carica-node__section-header"
          onClick={onToggleMapping}
        >
          <span>Mappatura del corpo</span>
          <span className="carica-node__toggle">{showMapping ? '▼' : '▶'}</span>
        </div>

        {showMapping && (
          <div className="carica-node__mapping">
            <div className="carica-node__mapping-actions">
              <button type="button" onClick={handleAddRestBodyMapping} className="carica-node__btn carica-node__btn--secondary">
                + Campo
              </button>
            </div>
            {restBodyMapping.length === 0 ? (
              <div className="carica-node__hint">
                Nessuna mappatura: il server invia le righe con le colonne disponibili.
              </div>
            ) : (
              <div className="carica-node__mapping-list">
                {restBodyMapping.map((mapping, idx) => (
                  <div key={idx} className="carica-node__mapping-row">
                    <select
                      value={mapping.source || ''}
                      onChange={(e) => handleRestBodyMappingChange(idx, 'source', e.target.value)}
                      className="carica-node__mapping-select"
                    >
                      <option value="">Colonna...</option>
                      {availableColumns.map((col, colIdx) => {
                        const originalName = col.name || col;
                        const displayName = col.alias ? col.alias + ' (' + originalName + ')' : originalName;
                        return <option key={colIdx} value={originalName}>{displayName}</option>;
                      })}
                    </select>
                    <span className="carica-node__mapping-arrow">→</span>
                    <input
                      type="text"
                      value={mapping.target || ''}
                      onChange={(e) => handleRestBodyMappingChange(idx, 'target', e.target.value)}
                      placeholder="Campo JSON..."
                      className="carica-node__mapping-input"
                    />
                    <button type="button" onClick={() => handleRemoveRestBodyMapping(idx)} className="carica-node__mapping-remove">
                      ×
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      <div className="carica-node__section">
        <div
          className="carica-node__section-header"
          onClick={onToggleAdvanced}
        >
          <span>Intestazioni HTTP e lotti</span>
          <span className="carica-node__toggle">{showAdvanced ? '▼' : '▶'}</span>
        </div>

        {showAdvanced && (
          <div className="carica-node__advanced">
            <div className="carica-node__advanced-row">
              <label>Invio:</label>
              <select
                value={restBatchMode}
                onChange={(e) => onUpdate('rest_batch_mode', e.target.value)}
                className="carica-node__select carica-node__select--small"
              >
                {REST_BATCH_MODES.map(mode => (
                  <option key={mode.value} value={mode.value}>{mode.label}</option>
                ))}
              </select>
            </div>
            <div className="carica-node__advanced-row">
              <label>Righe per lotto:</label>
              {/* R8.4: campo unico, niente default silenzioso. */}
              <FieldInput
                codec={REST_BATCH}
                value={restBatchSize}
                onCommit={(v) => onUpdate('rest_batch_size', v)}
                aria-label="Righe per lotto"
                className="carica-node__input carica-node__input--small"
              />
            </div>
            <div className="carica-node__advanced-row">
              <label>Attesa massima (s):</label>
              <FieldInput
                codec={REST_TIMEOUT}
                value={restTimeoutSeconds}
                onCommit={(v) => onUpdate('rest_timeout_seconds', v)}
                aria-label="Attesa massima (s)"
                className="carica-node__input carica-node__input--small"
              />
            </div>
            <div className="carica-node__rest-header-list">
              <div className="carica-node__mapping-actions">
                <button type="button" onClick={handleAddRestHeader} className="carica-node__btn carica-node__btn--secondary">
                  + Intestazione
                </button>
              </div>
              {restHeaders.map((header, idx) => {
                const secret = isSecretHeader(header);
                return (
                  <div key={idx} className="carica-node__mapping-row-wrap">
                    <div className="carica-node__mapping-row">
                      <input
                        type="text"
                        value={header.name || ''}
                        onChange={(e) => handleRestHeaderChange(idx, 'name', e.target.value)}
                        placeholder="Intestazione"
                        className="carica-node__mapping-input"
                      />
                      {secret ? (
                        <span className="carica-node__mapping-input carica-node__mapping-input--locked">
                          {header.redacted ? 'valore non nella copia locale' : 'credenziale da migrare'}
                        </span>
                      ) : (
                        <input
                          type="text"
                          value={header.value || ''}
                          onChange={(e) => handleRestHeaderChange(idx, 'value', e.target.value)}
                          placeholder="Valore"
                          className="carica-node__mapping-input"
                        />
                      )}
                      <button type="button" onClick={() => handleRemoveRestHeader(idx)} className="carica-node__mapping-remove" aria-label="Rimuovi intestazione">
                        ×
                      </button>
                    </div>
                    {secret && (
                      <div className="carica-node__hint carica-node__hint--warn" role="alert">
                        Credenziale nel canvas: scegli un connettore API in «Autenticazione» o rimuovi la riga (×). Finché resta, il progetto non si salva.
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </div>
    </>
  );
}

export default RestDestinationSection;
