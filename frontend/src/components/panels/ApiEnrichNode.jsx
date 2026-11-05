/**
 * ApiEnrichNode.jsx — Nodo canvas per arricchimento dati
 *
 * Nodo che esegue una chiamata API esterna per ogni riga del dataset.
 * Supporta tre modalità:
 *  - API REST (connettore da ApiConnectionManager)
 *  - Processore file (connection_type='rest_file', HTTP con file in/out)
 *  - Data Source (file remoto da SharePoint/FTP)
 *
 * Mapping: colonne input → parametri API → colonne output configurabili
 * tramite ResponseConfigBuilder.
 *
 * Usato in: ArricchisciEditor
 */
import { uploadFile } from '../../api/files';
import { getApiConnection } from '../../api/apiConnections';
import React, { useState, useEffect, useMemo, useRef } from 'react';
import { Position, useReactFlow, useStore } from '@xyflow/react';
import { getColumnTypeIcon, getColumnTypeLabel, mapApiTypeToStandard } from '../../utils/columnUtils';
import { humanizeError } from '../../utils/errorMessages';
import useCanvasStore from '../../store/useCanvasStore';
import { useColumnsOf } from '../../hooks/useColumns';
import ColumnStatusNote from '../common/ColumnStatusNote';
import './ApiEnrichNode.css';
import RunHistoryModal from '../modals/RunHistoryModal';
import { dataFlowEdges } from '../../utils/runtimeBindingEdges';
import NodeHandle from '../canvas/NodeHandle';
import { nodeTitle } from '../canvas/nodeDescriptors';
import { term } from '../../utils/glossary';

/**
 * ApiEnrichNode - Nodo per arricchire dati chiamando API esterne
 * 
 * REFACTORED: il nodo arriva già configurato dal modale di selezione.
 * Nessun dropdown - mostra direttamente nome API e mapping parametri.
 * 
 * Input: dati da nodo precedente (ProjectSource, FileSource, StaticValues, altro ApiEnrich)
 * Output: dati originali + colonne da API (con tipi)
 */
function ApiEnrichNode({ id, data }) {
  const { getNodes, getEdges } = useReactFlow();
  // Mutazioni SOLO dallo store (audit R1): il setter di React Flow in
  // modalita' controllata diventa un change `reset` che lo store non
  // considera una modifica -> i parametri non venivano mai salvati.
  const patchExecParams = useCanvasStore(s => s.updateNodeData);
// rest_file (servizio HTTP remoto) lavora file→file, non riga-per-riga.
  // Il nodo si comporta diversamente sotto il cofano (no auto-mapping colonne,
  // no calcolo output columns, ecc.).
  const isExecutable = data.connection_type === 'rest_file';
  
  // Sottoscrivi ai cambiamenti degli edge in ingresso (trigger re-render quando si collega/scollega)
  const incomingEdgeCount = useStore(
    state => dataFlowEdges(state.edges).filter(e => e.target === id).length
  );
  // Se i parametri non sono nel data (vecchio progetto), fetch dal backend
  const [fetchedApi, setFetchedApi] = useState(null);
  const [loadingApi, setLoadingApi] = useState(false);
  const [isRunHistoryOpen, setIsRunHistoryOpen] = useState(false);
  const projectId = useCanvasStore(s => s.projectId);
  const fetchedApiIdRef = useRef(null);
  
  // Usa data.parameters se presente, altrimenti fetched
  const apiParameters = data.parameters || fetchedApi?.parameters || [];
  // Se output_mapping è vuoto ma schema_fields è disponibile (auto-schema dopo prima esecuzione),
  // costruiamo l'output preview dai schema_fields con i tipi rilevati
  const rawOutputMapping = data.output_mapping || fetchedApi?.output_mapping || [];
  const rawSchemaFields = data.schema_fields || fetchedApi?.schema_fields || [];
  const apiOutputMapping = rawOutputMapping.length > 0
    ? rawOutputMapping
    : rawSchemaFields.map(f => ({ column: f.name || f.path, path: f.path, type: f.type }));
  const apiEndpoint = data.endpoint || fetchedApi?.endpoint || null;
  const staticParameters = data.static_parameters || fetchedApi?.static_parameters || {};
  
  const paramMapping = data.param_mapping || {};

  // Fetch API details se mancano parameters O api_name (retrocompatibilità + reload canvas)
  // schema_fields viene aggiornato dal loadApiDetails stesso, NON deve essere dependency
  useEffect(() => {
    if (data.api_id && (!data.parameters?.length || !data.api_name) && !data.endpoint) {
      if (fetchedApiIdRef.current === data.api_id) return; // già fetchato con successo, no loop
      loadApiDetails(data.api_id);
    }
  }, [data.api_id]);

  const loadApiDetails = async (apiId) => {
    try {
      setLoadingApi(true);
      // getApiConnection ritorna direttamente il body (data), non il
      // wrapper axios — quindi il check di presenza si fa sull'oggetto.
      const apiData = await getApiConnection(apiId);
      if (apiData) {
        // Segna come fetchato SOLO dopo il successo: in caso di errore di rete
        // il prossimo render potrà riprovare (fetchedApiIdRef rimane null/precedente)
        fetchedApiIdRef.current = apiId;
        // Solo setFetchedApi — NON un update dei nodi, che causa loop ReactFlow↔Zustand
        // I dati sono disponibili via fetchedApi per tutta la vita del componente
        setFetchedApi(apiData);
      }
    } catch (error) {
      console.error('Errore caricamento API:', error);
    } finally {
      setLoadingApi(false);
    }
  };
const connectedFileSource = useMemo(() => {
    if (!isExecutable) return null;
    const edges = dataFlowEdges(getEdges());
    const nodes = getNodes();
    const inEdge = edges.find(e => e.target === id);
    if (!inEdge) return null;
    const srcNode = nodes.find(n => n.id === inEdge.source);
    if (!srcNode || srcNode.type !== 'file_source') return null;
    return {
      id: srcNode.data?.file_source_id,
      name: srcNode.data?.fileName || srcNode.data?.file_source_id || 'File',
      // RB.4.C: backend (RB.4.B) marca i file_source non visibili al
      // gruppo del progetto. Lo propaghiamo nella validation per
      // disabilitare run/preview con messaggio operativo.
      access_denied: !!srcNode.data?.access_denied,
      access_denied_reason: srcNode.data?.access_denied_reason || null,
    };
  }, [isExecutable, id, getEdges, getNodes, incomingEdgeCount]);

  // RB.4.C: rileva se QUALSIASI nodo upstream e' marcato access_denied
  // (file_source o project_source). La validation a valle blocca
  // l'esecuzione anche per il flusso standard non-executable.
  const upstreamAccessDenied = useMemo(() => {
    const edges = dataFlowEdges(getEdges());
    const nodes = getNodes();
    const inEdge = edges.find(e => e.target === id);
    if (!inEdge) return null;
    const srcNode = nodes.find(n => n.id === inEdge.source);
    if (!srcNode?.data?.access_denied) return null;
    return {
      type: srcNode.data?.inaccessible_resource_type || srcNode.type,
      reason: srcNode.data?.access_denied_reason || 'Sorgente non accessibile al tuo gruppo',
    };
  }, [id, getEdges, getNodes, incomingEdgeCount]);
const handleExecParamChange = (key, value) => {
    // Azione utente: dirty + undo, quindi salvata e inviata all'esecuzione.
    const current = getNodes().find(n => n.id === id);
    patchExecParams(id, { exec_params: { ...(current?.data?.exec_params || {}), [key]: value } });
  };

  // REST FILE: aggiorna output_kind (tag che dice "lo zip prodotto da
  // questo Processore File ha questa struttura nota") ──
  // Usa useCanvasStore.updateNodeData per persistere nel canvas store
  // (single source of truth per save + sincronizzato con ReactFlow).
  const handleOutputKindChange = (value) => {
    useCanvasStore.getState().updateNodeData(id, {
      output_kind: value || null,
      _lastUpdate: Date.now(),
    });
  };

  // Colonne in ingresso dalla fonte unica (Blocco A, regola 4): quelle che
  // escono davvero dal nodo a monte (sorgente, Valori statici o un altro
  // arricchimento), con gli id. Le colonne in uscita le prevede il server
  // dallo stesso mapping dell'esecuzione: nessun calcolo qui, niente
  // data.columns scritto dal pannello.
  const inputResult = useColumnsOf(id, { side: 'input' });
  const inputColumns = inputResult.columns;

  // Auto-mapping quando arrivano colonne input (skip per file-processor)
  useEffect(() => {
    if (isExecutable) return;
    if (inputColumns.length === 0 || apiParameters.length === 0) return;
    
    // Leggi il mapping corrente direttamente dallo store (evita closure stale)
    const currentNode = useCanvasStore.getState().nodes.find(n => n.id === id);
    const currentMapping = currentNode?.data?.param_mapping || {};
    const hasMapping = Object.values(currentMapping).some(v => v);
    if (hasMapping) return;
    
    const newMapping = {};
    apiParameters.forEach(param => {
      if (param.mode === 'fixed') return;
      const matchingCol = inputColumns.find(c => 
        c.name.toLowerCase() === param.name.toLowerCase()
      );
      if (matchingCol) newMapping[param.name] = matchingCol.name;
    });
    
    if (Object.keys(newMapping).length > 0) {
      // Mapping proposto automaticamente: dato DERIVATO (salvato via
      // pendingSync, non rende il canvas "modificato" all'apertura).
      useCanvasStore.getState().updateNodeData(id, { param_mapping: newMapping }, { derived: true });
    }
  }, [inputColumns, apiParameters]);
  
  const handleMappingChange = (paramName, columnName) => {
    useCanvasStore.getState().updateNodeData(id, {
      param_mapping: { ...data.param_mapping, [paramName]: columnName },
      _lastUpdate: Date.now()
    });
  };
  

  
  // Validazione
  const validation = useMemo(() => {
    // RB.4.C: blocca il run se la sorgente upstream e' marcata dal backend
    // come non visibile al gruppo del progetto (canvas storico cross-tenant).
    // Defense-in-depth: il runtime check (api_connection_rest_file
    // _assert_file_source_group_access) resta fail-closed lato backend.
    if (upstreamAccessDenied) {
      return {
        valid: false,
        message: `Sorgente non accessibile: ${upstreamAccessDenied.reason}`,
      };
    }

    if (!data.api_id) {
      return { valid: false, message: 'Nessuna API configurata' };
    }

    // Executable: valido se ha api_id e file source collegato
    if (isExecutable) {
      if (!connectedFileSource) {
        return { valid: false, message: 'Collega un file in ingresso' };
      }
      if (connectedFileSource.access_denied) {
        return {
          valid: false,
          message: `File sorgente non accessibile: ${connectedFileSource.access_denied_reason || 'permesso negato'}`,
        };
      }
      return { valid: true, message: 'Pronto' };
    }

    // Nessun parametro mappabile → API autonoma, non serve sorgente
    const mappableRequired = apiParameters.filter(p => p.required && p.mode !== 'fixed');
    if (apiParameters.length === 0 || mappableRequired.length === 0) {
      return { valid: true, message: 'Pronto' };
    }

    if (inputColumns.length === 0) {
      return { valid: false, message: 'Collega una sorgente dati' };
    }

    const missingParams = mappableRequired.filter(p => !paramMapping[p.name]);
    if (missingParams.length > 0) {
      return {
        valid: false,
        message: `Mappa: ${missingParams.map(p => p.name).join(', ')}`
      };
    }

    // Warning se output_mapping è vuoto (spec OpenAPI senza schema response)
    // Il backend farà auto-flatten ma l'utente deve saperlo
    if (apiOutputMapping.length === 0 && data.api_id) {
      return { valid: true, warning: true, message: 'Pronto: campi in uscita mappati in automatico (nessuno schema)' };
    }

    return { valid: true, message: 'Pronto' };
  }, [data.api_id, isExecutable, connectedFileSource, upstreamAccessDenied, inputColumns, apiParameters, paramMapping, apiOutputMapping]);

  const execConfig = data.response || fetchedApi?.response || {};

  // ════════════════════════════════════════════════
  // RENDER: EXECUTABLE MODE
  // ════════════════════════════════════════════════
  if (isExecutable) {
    const execParams = data.exec_params || {};
    const execParameters = apiParameters.filter(p => p.name);

    return (
      <div className="api-enrich-node api-enrich-node--file-processor">
        {/* Titolo, chiusura ed eliminazione sono del pannello (NodeInspector,
            regola 6 del Blocco A). */}

        <div className="api-enrich-node__body">
          <ColumnStatusNote result={inputResult} />
          {/* Badge tipo */}
          <div className="api-enrich-node__exec-badge">
            📦 Processore File (REST)
          </div>

          {/* Tipo di uscita strutturato — solo per Processore File REST.
              Tag esplicito che dice al canvas "questo zip ha struttura nota"
              cosi' il nodo result downstream puo' specializzarsi. */}
          {data.connection_type === 'rest_file' && (
            <div className="api-enrich-node__section">
              <label className="api-enrich-node__label">📦 Tipo di uscita:</label>
              <select
                className="api-enrich-node__exec-param-input"
                value={data.output_kind || ''}
                onChange={e => handleOutputKindChange(e.target.value)}
                title="Definisce come il nodo Output si comportera' col file restituito"
              >
                <option value="">Generico (archivio zip da scaricare)</option>
                <option value="imdf">IMDF (mappe di interni)</option>
              </select>
            </div>
          )}

          {/* File extra (upload inline) per connettori rest_file con
              file_fields source='extra_input'. Il file principale viene
              dall'edge upstream come sempre. */}
          {data.connection_type === 'rest_file' && (() => {
            const fileFields = fetchedApi?.request?.file_fields
                            || data.request?.file_fields
                            || [];
            const extraFields = fileFields.filter(ff => ff.source === 'extra_input');
            if (extraFields.length === 0) return null;
            return (
              <div className="api-enrich-node__section">
                <label className="api-enrich-node__label">📎 File aggiuntivi:</label>
                {extraFields.map(ff => {
                  const current = (data.extra_files || {})[ff.name];
                  const label = ff.label || ff.name;
                  return (
                    <ExtraFileSlot
                      key={ff.name}
                      field={ff}
                      label={label}
                      current={current}
                      onUploaded={(info) => {
                        const next = { ...(data.extra_files || {}) };
                        next[ff.name] = info;
                        useCanvasStore.getState().updateNodeData(id, { extra_files: next, _lastUpdate: Date.now() });
                      }}
                      onRemoved={() => {
                        const next = { ...(data.extra_files || {}) };
                        delete next[ff.name];
                        useCanvasStore.getState().updateNodeData(id, { extra_files: Object.keys(next).length ? next : null, _lastUpdate: Date.now() });
                      }}
                    />
                  );
                })}
              </div>
            );
          })()}

          {/* File collegato */}
          <div className={`api-enrich-node__exec-file ${!connectedFileSource ? 'api-enrich-node__exec-file--empty' : ''}`}>
            {connectedFileSource ? (
              <>📄 {connectedFileSource.name}</>
            ) : (
              <>← Collega un file in ingresso</>
            )}
          </div>

          {/* Parametri opzionali */}
          {execParameters.length > 0 && (
            <div className="api-enrich-node__section">
              <label className="api-enrich-node__label">📋 Parametri:</label>
              {execParameters.map(param => (
                <div key={param.name} className="api-enrich-node__exec-param">
                  <span className="api-enrich-node__param-name">{param.name}</span>
                  <input
                    type="text"
                    value={execParams[param.name] || ''}
                    onChange={e => handleExecParamChange(param.name, e.target.value)}
                    placeholder={param.description || param.name}
                    className="api-enrich-node__exec-param-input"
                  />
                </div>
              ))}
            </div>
          )}

          {/* Output info */}
          <div className="api-enrich-node__exec-output">
            📦 Output: file {execConfig.output_extension || 'file'}
          </div>

          {/* Validation */}
          <div className={`api-enrich-node__status ${
            validation.valid && !validation.warning ? 'api-enrich-node__status--valid' : 
            validation.warning ? 'api-enrich-node__status--warning' : 'api-enrich-node__status--invalid'
          }`}>
            <span className="api-enrich-node__status-icon">{validation.valid && !validation.warning ? '✅' : '⚠️'}</span>
            <span>{validation.message}</span>
          </div>
        </div>

        <NodeHandle type="target" id="input" position={Position.Left} className="api-enrich-node__handle--input" />
        <NodeHandle type="source" id="output" position={Position.Right} className="api-enrich-node__handle--output" />
      </div>
    );
  }

  // ════════════════════════════════════════════════
  // RENDER: STANDARD API MODE
  // ════════════════════════════════════════════════

  const nodeClassName = 'api-enrich-node';

  // Parametri mappabili (non fissi)
  const mappableParams = apiParameters.filter(p => p.mode !== 'fixed' && !(p.name in staticParameters));
  const fixedParams = apiParameters.filter(p => p.mode === 'fixed' || p.name in staticParameters);

  return (
    <div className={nodeClassName}>
      {/* Titolo (nome dell'API), chiusura ed eliminazione sono del pannello
          (NodeInspector e nodeDescriptors, regola 6 del Blocco A). */}

      {/* Body */}
      <div className="api-enrich-node__body">
        <ColumnStatusNote result={inputResult} />
        {/* Loading (retrocompatibilità) */}
        {loadingApi && (
          <div className="api-enrich-node__loading">⏳ Caricamento API...</div>
        )}

        {/* API Info */}
        {apiEndpoint && !loadingApi && (
          <div className="api-enrich-node__section">
            <div className="api-enrich-node__api-info">
              <div className="api-enrich-node__api-method">
                {apiEndpoint.method} {apiEndpoint.url?.split('/').slice(-1)[0]}
              </div>
            </div>
          </div>
        )}

        {/* Parametri fissi (solo visualizzazione) */}
        {fixedParams.length > 0 && (
          <div className="api-enrich-node__section">
            <label className="api-enrich-node__label api-enrich-node__label--spacing">
              📌 Parametri fissi:
            </label>
            <div className="api-enrich-node__fixed-params">
              {fixedParams.map(param => (
                <div key={param.name} className="api-enrich-node__fixed-param">
                  <span className="api-enrich-node__fixed-name">{param.name}</span>
                  <span className="api-enrich-node__fixed-value">
                    = {staticParameters[param.name] || param.default_value || '?'}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Mappatura dei parametri */}
        {mappableParams.length > 0 && !loadingApi && (
          <div className="api-enrich-node__section">
            <label className="api-enrich-node__label api-enrich-node__label--spacing">
              📥 Mappatura dei parametri:
            </label>

            {/* Datalist condivisa per autocompletamento dei nomi colonna */}
            <datalist id={`api-enrich-cols-${id}`}>
              {inputColumns.map(col => (
                <option key={col.name} value={col.name}>
                  {getColumnTypeLabel(col.type)}
                </option>
              ))}
            </datalist>

            {mappableParams.map(param => {
              const value = paramMapping[param.name] || '';
              const isTemplate = /\{\w+\}/.test(value);
              return (
                <div key={param.name} className="api-enrich-node__mapping-row">
                  <div className="api-enrich-node__param-info">
                    <span className={`api-enrich-node__param-badge ${
                      param.required
                        ? 'api-enrich-node__param-badge--required'
                        : 'api-enrich-node__param-badge--optional'
                    }`}>
                      {param.required ? 'REQ' : 'OPT'}
                    </span>
                    <span className="api-enrich-node__param-name">{param.name}</span>
                  </div>
                  <span className="api-enrich-node__mapping-arrow">{isTemplate ? '✨' : '→'}</span>
                  <input
                    type="text"
                    list={`api-enrich-cols-${id}`}
                    value={value}
                    onChange={(e) => handleMappingChange(param.name, e.target.value)}
                    placeholder="-- colonna o modello --"
                    title={
                      isTemplate
                        ? "Modello: i segnaposto {nome_colonna} verranno sostituiti coi valori della riga"
                        : "Seleziona una colonna dei dati oppure scrivi un modello come \"{X},{Y}\""
                    }
                    className={`api-enrich-node__mapping-select ${
                      value ? 'api-enrich-node__mapping-select--mapped' : ''
                    }`}
                    style={isTemplate ? { fontFamily: 'monospace', fontSize: '11px' } : undefined}
                  />
                </div>
              );
            })}

            <div className="api-enrich-node__params-hint">
              💡 Per parametri composti puoi usare un modello come <code>{'{X},{Y}'}</code> o <code>{'{via} {civico}, {comune}'}</code>.
              I segnaposto verranno sostituiti coi valori della riga.
            </div>
          </div>
        )}

        {/* Conditional execution (execute_if) */}
        {!isExecutable && (
          <div className="api-enrich-node__section">
            <label className="api-enrich-node__label">
              ⚡ Esecuzione condizionale
            </label>
            <input
              type="text"
              className="api-enrich-node__mapping-select api-enrich-node__exec-if-input"
              value={data.execute_if || ''}
              onChange={(e) => useCanvasStore.getState().updateNodeData(id, { execute_if: e.target.value, _lastUpdate: Date.now() })}
              placeholder="es. georef_mode=CATASTO"
              title="Se impostato, il nodo viene eseguito solo quando la condizione e soddisfatta. Formato: colonna=valore o colonna!=valore. Se vuoto, il nodo viene sempre eseguito."
            />
            <div className="api-enrich-node__hint">
              Se vuoto il nodo viene sempre eseguito. Es: <code>georef_mode=CATASTO</code> o <code>flag_omi!=false</code>
            </div>
          </div>
        )}

        {/* Output preview */}
        {apiOutputMapping.length > 0 && (
          <div className="api-enrich-node__section">
            <div className="api-enrich-node__output-preview">
              <div className="api-enrich-node__output-title">
                📤 Colonne aggiunte dall'API:
              </div>
              <div className="api-enrich-node__output-chips">
                {apiOutputMapping.map((mapping, idx) => {
                  const mappedType = mapApiTypeToStandard(mapping.type);
                  const isGeo = mappedType.startsWith('geometry');
                  return (
                    <span
                      key={idx}
                      className={`api-enrich-node__output-chip ${
                        isGeo ? 'api-enrich-node__output-chip--geo' : ''
                      }`}
                      title={getColumnTypeLabel(mappedType)}
                    >
                      {getColumnTypeIcon(mappedType)} +{mapping.column}
                    </span>
                  );
                })}
              </div>
            </div>
          </div>
        )}

        {/* Input columns preview */}
        {inputColumns.length > 0 && (
          <div className="api-enrich-node__section">
            <div className="api-enrich-node__input-preview">
              <div className="api-enrich-node__input-title">
                📥 Colonne in input: {inputColumns.length}
              </div>
              <div className="api-enrich-node__input-chips">
                {inputColumns.slice(0, 8).map((col, idx) => (
                  <span
                    key={idx}
                    className="api-enrich-node__input-chip"
                    title={getColumnTypeLabel(col.type)}
                  >
                    {getColumnTypeIcon(col.type)} {col.name}
                  </span>
                ))}
                {inputColumns.length > 8 && (
                  <span className="api-enrich-node__input-more">
                    +{inputColumns.length - 8} altre
                  </span>
                )}
              </div>
            </div>
          </div>
        )}

        {/* Opzioni avanzate */}
        {!isExecutable && (
          <div className="api-enrich-node__section api-enrich-node__section--advanced">
            <div
              className="api-enrich-node__advanced-toggle"
              onClick={() => useCanvasStore.getState().updateNodeData(id, { _advOpen: !data._advOpen }, { derived: true })}
            >
              <span>⚙️ Opzioni avanzate</span>
              <span className="api-enrich-node__advanced-arrow">{data._advOpen ? '▲' : '▼'}</span>
            </div>
            {data._advOpen && (
              <div className="api-enrich-node__advanced-body">
                {/* Batch info (read-only, configurato nella connessione) */}
                {data.batch?.enabled && (
                  <div className="api-enrich-node__adv-row api-enrich-node__adv-row--mb-sm">
                    <span className="api-enrich-node__adv-key">⚡ Chiamate a lotti</span>
                    <span className="api-enrich-node__adv-badge">attivo — {data.batch?.max_size||100} righe/chiamata</span>
                  </div>
                )}
                <div className="api-enrich-node__adv-divider" />
                <label className="api-enrich-node__adv-label">
                  <input
                    type="checkbox"
                    checked={data.continue_on_error !== false}
                    onChange={e => useCanvasStore.getState().updateNodeData(id, { continue_on_error: e.target.checked, _lastUpdate: Date.now() })}
                  />
                  <span>Continua su errore</span>
                  {data.continue_on_error === false && (
                    <span className="api-enrich-node__adv-badge api-enrich-node__adv-badge--warn">fermati al primo errore</span>
                  )}
                </label>
                {data.continue_on_error !== false && (
                  <div className="api-enrich-node__adv-row">
                    <span className="api-enrich-node__adv-key" title="Ferma l'esecuzione se il numero di errori supera questa soglia (0 = mai)">Max errori tollerati</span>
                    <input
                      type="number"
                      className="api-enrich-node__adv-input"
                      value={data.max_errors || 0}
                      min={0}
                      placeholder="0 = illimitato"
                      onChange={e => useCanvasStore.getState().updateNodeData(id, { max_errors: parseInt(e.target.value) || 0, _lastUpdate: Date.now() })}
                    />
                  </div>
                )}
                <label className="api-enrich-node__adv-label">
                  <input
                    type="checkbox"
                    checked={data.skip_null_params !== false}
                    onChange={e => useCanvasStore.getState().updateNodeData(id, { skip_null_params: e.target.checked, _lastUpdate: Date.now() })}
                  />
                  <span>Salta righe con parametri null</span>
                  {data.skip_null_params === false && (
                    <span className="api-enrich-node__adv-badge api-enrich-node__adv-badge--warn">errore su null</span>
                  )}
                </label>
              </div>
            )}
          </div>
        )}


        {/* Validation status */}
        <div className={`api-enrich-node__status ${
          validation.valid && !validation.warning
            ? 'api-enrich-node__status--valid'
            : validation.warning ? 'api-enrich-node__status--warning'
            : 'api-enrich-node__status--invalid'
        }`}>
          <span className="api-enrich-node__status-icon">
            {validation.valid && !validation.warning ? '✅' : '⚠️'}
          </span>
          <span>{validation.message}</span>
        </div>
      </div>

      {/* Cronologia esecuzioni */}
      <div className="api-enrich-node__footer">
        <button
          type="button"
          className="api-enrich-node__history-btn"
          onClick={(e) => { e.stopPropagation(); setIsRunHistoryOpen(true); }} onMouseDown={(e) => e.stopPropagation()}
          title={term('run_history').description}
        >
          {term('run_history').icon} {term('run_history').name}
        </button>
      </div>
      <RunHistoryModal
        isOpen={isRunHistoryOpen}
        onClose={() => setIsRunHistoryOpen(false)}
        canvasId={projectId}
        nodeId={id}
        nodeLabel={nodeTitle('api_enrich', data)}
      />

      {/* Handles — sempre presenti, indipendentemente dal caricamento dei parametri */}
      <NodeHandle
        type="target"
        id="input"
        position={Position.Left}
        className="api-enrich-node__handle--input"
      />
      <NodeHandle
        type="source"
        id="output"
        position={Position.Right}
        className="api-enrich-node__handle--output"
      />
    </div>
  );
}

// ════════════════════════════════════════════════
// ExtraFileSlot — slot di upload inline per file_fields source='extra_input'
// ════════════════════════════════════════════════
function ExtraFileSlot({ field, label, current, onUploaded, onRemoved }) {
  // Caricare o togliere il file e' una modifica del canvas: in sola lettura
  // (isReadOnly = !can.editCanvas, useCanvasCore) l'azione non si mostra.
  const isReadOnly = useCanvasStore(s => s.isReadOnly);
  const [uploading, setUploading] = React.useState(false);
  const [error, setError] = React.useState('');
  const inputRef = React.useRef(null);

  const handleFileSelected = async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setError('');
    setUploading(true);
    try {
      // uploadFile applica size pre-flight, propagazione 413 come
      // UploadTooLargeError e lascia ad axios il Content-Type multipart.
      // L'errore catchato qui copre sia UploadTooLargeError sia AxiosError;
      // in entrambi i casi err.message e' normalizzato dall'interceptor.
      const fs = await uploadFile(
        file,
        `extra_${field.name}_${file.name}`,
        `Input aggiuntivo "${field.name}" per il passo ${term('api_enrich').name}`,
      );
      onUploaded({
        file_source_id:   fs.id,
        original_filename: fs.original_filename || file.name,
        // P1 (2026-05-26): nome canonico BE = `file_size` (vedi
        // FileSource model). `size_bytes` era sempre undefined → il
        // fallback puntava al File JS lato client invece che alla
        // dimensione effettiva salvata (divergenti su re-encode/
        // compressione upload).
        size:             fs.file_size || file.size,
      });
    } catch (err) {
      setError(humanizeError(err).message);
    } finally {
      setUploading(false);
      if (inputRef.current) inputRef.current.value = '';
    }
  };

  return (
    <div className="api-enrich-node__file-field">
      <div className="api-enrich-node__file-field-header">
        <span className="api-enrich-node__file-field-label">
          {label}
          {field.required && <span className="api-enrich-node__file-field-required">*</span>}
        </span>
        {current && !isReadOnly && (
          <button
            type="button"
            onClick={onRemoved}
            className="api-enrich-node__file-field-remove"
            title="Rimuovi file"
          >×</button>
        )}
      </div>

      {current ? (
        <div className="api-enrich-node__file-field-success">
          ✓ {current.original_filename}
          {current.size ? ` (${Math.round(current.size / 1024)} KB)` : ''}
        </div>
      ) : isReadOnly ? (
        <div className="api-enrich-node__file-field-format-hint">Nessun file (sola lettura)</div>
      ) : (
        <label className={`api-enrich-node__file-field-box${uploading ? ' api-enrich-node__file-field-box--uploading' : ''}`}>
          <input
            ref={inputRef}
            type="file"
            accept={field.accept || undefined}
            disabled={uploading}
            onChange={handleFileSelected}
            className="api-enrich-node__file-field-input--hidden"
          />
          {uploading ? 'Caricamento...' : '📁 Scegli file'}
        </label>
      )}
      {field.accept && !current && (
        <div className="api-enrich-node__file-field-format-hint">Formati: {field.accept}</div>
      )}
      {error && (
        <div className="api-enrich-node__file-field-error">{error}</div>
      )}
    </div>
  );
}

export default ApiEnrichNode;
