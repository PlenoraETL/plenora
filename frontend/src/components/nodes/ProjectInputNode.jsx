import React, { memo, useMemo, useRef } from 'react';
import { Position } from '@xyflow/react';
import useCanvasStore from '../../store/useCanvasStore';
import { acceptsInputKind } from '../../utils/runtimeInputKinds';
import { loadSharedConnectionSets } from '../../utils/connectionSets';
import { useTransformRegistry } from '../../hooks/useTransformRegistry';
import { useResource } from '../../hooks/useResource';
import { ResourceError } from '../common/ListState';
import {
  getNodeBindableTargets,
  isParameterBindingEdge,
} from '../../utils/runtimeBindingEdges';
import './ProjectInputNode.css';
import NodeHandle from '../canvas/NodeHandle';
import { nodeTitle } from '../canvas/nodeDescriptors';
import { INPUT_KIND_LABELS, termName } from '../../utils/glossary';
import FieldInput from '../common/FieldInput';
import * as codecs from '../../utils/fieldCodecs';
import { runtimeInputCodec } from '../../utils/runtimeInputKinds';
import { useNodesContent } from '../../hooks/useColumns';
import { useConfirmOrDeny } from '../../context/ConfirmContext';
import { requestCanvasDeletion } from '../../utils/canvasDeletion';
import { READ_ONLY_SAFE, useReadOnlyLock } from '../common/readOnlySafe';

const INPUT_TYPES = Object.entries(INPUT_KIND_LABELS);

// Campi con buffer locale e un convertitore per tipo (Fase 5 T5, R8.1, N4):
// la chiave si normalizza al commit, non a ogni tasto ("file_" resta mentre
// si scrive "file_clienti"); gli elenchi tengono la virgola appena digitata.
// Stessa regola del backend per la chiave (eseguibili/models: ^[a-z][a-z0-9_]*$).
const KEY = codecs.key({ allowed: '_', startsWithLetter: true, maxLength: 80 });
const LABEL = codecs.text({ trim: false });
const NAMES = codecs.list({ item: codecs.text() });
const EXTENSIONS = codecs.list({ item: codecs.key({ allowed: '', startsWithLetter: false, maxLength: 16, optional: false }) });
const OPTIONS = codecs.list({ item: codecs.text() });
const SIZE_MB = codecs.integer({ min: 1, optional: true });
const MIN_LENGTH = codecs.integer({ min: 0, optional: true });
const MAX_LENGTH = codecs.integer({ min: 1, optional: true });
const BOUND = codecs.decimal({ optional: true });
const PATTERN = codecs.regex();

/**
 * N4: le colonne attese si ricostruiscono dai NOMI conservando tipo e
 * "facoltativo" di quelle che c'erano; solo una colonna nuova nasce
 * testo obbligatoria.
 */
export function mergeExpectedColumns(previous, names) {
  const byName = new Map((previous || []).map(column => [column.name, column]));
  return names.map(name => byName.get(name) || { name, type: 'str', required: true });
}

// Nessuna cache di modulo (audit R5.4, R1-N4, R5-N3): il registry e' quello
// condiviso (utils/transformRegistry, svuotato da resetSession) e i set di
// connessioni sono la risorsa condivisa della sessione (utils/connectionSets:
// una richiesta per N nodi, invalidata quando i set cambiano, svuotata da
// resetSession). Entrambi dagli hook comuni (Fase 5 T2): errore con «Riprova».

function ProjectInputNode({ id, data }) {
  const updateNodeData = useCanvasStore(s => s.updateNodeData);
  // Nodi senza posizioni (R9-U N6): un trascinamento non ridisegna il form.
  const nodes = useNodesContent();
  const edges = useCanvasStore(s => s.edges);
  const editGraph = useCanvasStore(s => s.editGraph);
  const confirm = useConfirmOrDeny();
  const updateEdge = useCanvasStore(s => s.updateEdge);
  const isReadOnly = useCanvasStore(s => s.isReadOnly);
  const mode = useCanvasStore(s => s.mode);
  const kind = data.input_kind || 'file';
  const registryResource = useTransformRegistry({ enabled: kind !== 'file' });
  const registry = registryResource.data || null;
  const registryStatus = registry ? 'ready' : registryResource.status;
  const setsResource = useResource(loadSharedConnectionSets, { key: 'connection-sets', enabled: kind === 'connection' });
  const connectionSets = setsResource.data || [];
  const bindingEdges = useMemo(
    () => edges.filter(edge => isParameterBindingEdge(edge) && edge.source === id),
    [edges, id],
  );
  const columnNames = useMemo(
    () => (data.expected_columns || []).map(column => column.name),
    [data.expected_columns],
  );


  // Undo per azione (R1.6): una patch-oggetto e' raggruppata per campo dallo
  // store (digitare nell'etichetta = un Ctrl+Z); le azioni composte passano
  // da editGraph (una sola azione).
  const update = changes => updateNodeData(id, changes);
  const updateBinding = (edgeId, targetField) => {
    const target = bindingEdges.find(edge => edge.id === edgeId)?.target;
    const targetNode = nodes.find(node => node.id === target);
    const option = getNodeBindableTargets(targetNode, registry)
      .find(item => item.value === targetField);
    const directParameter = option
      && option.value === `config.${option.parameter}`
      ? option.parameter
      : '';
    updateEdge(edgeId, edge => ({
      targetHandle: directParameter ? `parameter:${directParameter}` : undefined,
      data: {
        ...edge.data,
        target_field: targetField,
        target_parameter: option?.parameter || '',
      },
    }));
  };
  // La × e' una cancellazione dal canvas: regola unica (utils/canvasDeletion),
  // conferma e niente in sola lettura.
  const removeBinding = edge => {
    const targetNode = nodes.find(node => node.id === edge.target);
    const targetLabel = targetNode ? nodeTitle(targetNode.type, targetNode.data) : edge.target;
    return requestCanvasDeletion({
      confirm,
      edges: [edge],
      dialog: {
        title: 'Rimuovi collegamento',
        message: `Rimuovere il collegamento di questo input a «${targetLabel}»? Il parametro torna al valore scritto nel passo. Puoi annullare con Ctrl+Z.`,
        confirmText: 'Elimina',
        variant: 'danger',
      },
      perform: () => useCanvasStore.getState().removeEdges(edge.id),
    });
  };
  // Cambio di tipo: i collegamenti del tipo vecchio si tolgono nella STESSA
  // azione del cambio (un Ctrl+Z ripristina tipo e collegamenti, R1.12/R1.6).
  const changeKind = nextKind => editGraph(({ nodes: all, edges: allEdges }) => ({
    edges: allEdges.filter(edge => edge.source !== id),
    nodes: all.map(node => (node.id !== id ? node : {
      ...node,
      data: {
        ...node.data,
        input_kind: nextKind,
        bindings: [],
        connection_set_id: nextKind === 'connection' ? (node.data?.connection_set_id || '') : null,
      },
    })),
  }));
  // Dai dati CORRENTI dello store. Undo: la digitazione in un limite e' un
  // solo Ctrl+Z (lo decide lo store dal campo di testo, store/typingAction).
  const updateValidation = changes => updateNodeData(id, d => ({
    validation: { ...(d.validation || {}), ...changes },
  }));
  const sizeMb = data.validation?.max_size_bytes ? Math.round(data.validation.max_size_bytes / 1048576) : null;
  // Il valore predefinito passa dal convertitore del tipo dell'input (la
  // stessa regola dell'esecuzione: limiti, opzioni, espressione regolare).
  const defaultCodec = runtimeInputCodec({ input_kind: kind, validation: data.validation || {} });
  // N11 + regola della sola lettura (common/readOnlySafe): spenti i controlli
  // di modifica, attivi i «Riprova».
  const lockRef = useRef(null);
  useReadOnlyLock(lockRef, isReadOnly);

  return (
    <div className="pin">
      {/* Titolo, chiusura ed eliminazione sono del pannello (NodeInspector,
          regola 6 del Blocco A). N11: in sola lettura i controlli di modifica
          sono spenti davvero (useReadOnlyLock), non solo i select. */}
      <div ref={lockRef} className="pin__fieldset" data-readonly={isReadOnly ? 'true' : undefined}>

      <label className="pin__field">
        <span>Etichetta</span>
        <FieldInput className="nodrag" codec={LABEL} value={data.label || ''} onCommit={label => update({ label })} />
      </label>
      <label className="pin__field">
        <span>Chiave</span>
        <FieldInput
          className="nodrag"
          codec={KEY}
          value={data.input_key || ''}
          onCommit={inputKey => update({ input_key: inputKey })}
          placeholder="es. file_clienti"
        />
      </label>
      <label className="pin__field">
        <span>Tipo</span>
        <select className="nodrag" value={kind} disabled={isReadOnly} onChange={event => changeKind(event.target.value)}>
          {/* Solo i tipi che questa modalita' esegue (utils/runtimeInputKinds);
              un tipo gia' scelto resta visibile per poterlo cambiare. */}
          {INPUT_TYPES.filter(([value]) => value === kind || acceptsInputKind(mode, value))
            .map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
      </label>
      <label className="pin__check nodrag">
        <input type="checkbox" checked={data.required !== false} onChange={event => update({ required: event.target.checked })} />
        Obbligatorio
      </label>

      {kind === 'connection' ? (
        <>
          <label className="pin__field">
            <span>Set di connessioni</span>
            <select
              className="nodrag"
              value={data.connection_set_id || ''}
              disabled={isReadOnly}
              onChange={event => update({
                connection_set_id: event.target.value || null,
                default_value: null,
              })}
            >
              <option value="">{setsResource.initialLoading ? 'Caricamento set…' : 'Seleziona un set…'}</option>
              {connectionSets.map(item => (
                <option key={item.id} value={item.id}>
                  {item.name} ({item.members?.filter(member => member.active).length || 0})
                </option>
              ))}
            </select>
          </label>
          <label className="pin__field">
            <span>Valore predefinito (facoltativo)</span>
            <select
              className="nodrag"
              value={data.default_value ?? ''}
              disabled={isReadOnly || !data.connection_set_id}
              onChange={event => update({ default_value: event.target.value || null })}
            >
              <option value="">Nessun valore predefinito</option>
              {(connectionSets.find(item => item.id === data.connection_set_id)?.members || [])
                .filter(member => member.active)
                .map(member => (
                  <option key={member.selector_key} value={member.selector_key}>
                    {member.label}
                  </option>
                ))}
            </select>
          </label>
          {setsResource.status === 'error' && (
            <ResourceError compact resource={setsResource} title="Set di connessioni non caricati" />
          )}
          <p className="pin__binding-hint">
            Questo input seleziona la connessione dell’intero progetto Estrai.
          </p>
        </>
      ) : kind === 'file' ? (
        <>
          <label className="pin__check nodrag">
            <input type="checkbox" checked={Boolean(data.multiple)} onChange={event => update({ multiple: event.target.checked })} />
            Consenti piu' file (unione per righe)
          </label>
          <label className="pin__field">
            <span>Colonne attese</span>
            <FieldInput
              className="nodrag"
              codec={NAMES}
              value={columnNames}
              placeholder="codice, nome, data"
              onCommit={names => update({ expected_columns: mergeExpectedColumns(data.expected_columns, names) })}
            />
          </label>
          <label className="pin__field">
            <span>Estensioni</span>
            <FieldInput
              className="nodrag"
              codec={EXTENSIONS}
              value={data.validation?.accepted_extensions || []}
              placeholder="csv, xlsx, gpkg"
              onCommit={extensions => updateValidation({ accepted_extensions: extensions })}
            />
          </label>
          <label className="pin__field">
            <span>Dimensione massima (MB)</span>
            <FieldInput
              className="nodrag"
              codec={SIZE_MB}
              value={sizeMb}
              placeholder="limite globale"
              onCommit={mb => updateValidation({ max_size_bytes: mb === null ? null : mb * 1048576 })}
            />
          </label>
        </>
      ) : (
        <>
          <label className="pin__field">
            <span>Valore predefinito</span>
            {kind === 'boolean' ? (
              <select className="nodrag" value={data.default_value === true ? 'true' : data.default_value === false ? 'false' : ''} onChange={event => update({ default_value: event.target.value === '' ? null : event.target.value === 'true' })}>
                <option value="">Nessuno</option>
                <option value="true">Sì</option>
                <option value="false">No</option>
              </select>
            ) : (
              <FieldInput
                key={kind}
                className="nodrag"
                type={kind === 'date' ? 'date' : kind === 'datetime' ? 'datetime-local' : 'text'}
                codec={defaultCodec}
                value={data.default_value ?? null}
                onCommit={value => update({ default_value: value ?? null })}
              />
            )}
          </label>
          {kind === 'select' && (
            <label className="pin__field">
              <span>Opzioni (separate da virgola)</span>
              <FieldInput
                className="nodrag"
                codec={OPTIONS}
                value={data.validation?.options || []}
                onCommit={options => updateValidation({ options })}
              />
            </label>
          )}
          {kind === 'text' && (
            <div className="pin__validation-grid">
              <label className="pin__field"><span>Lunghezza minima</span><FieldInput className="nodrag" codec={MIN_LENGTH} value={data.validation?.min_length ?? null} onCommit={v => updateValidation({ min_length: v })} /></label>
              <label className="pin__field"><span>Lunghezza massima</span><FieldInput className="nodrag" codec={MAX_LENGTH} value={data.validation?.max_length ?? null} onCommit={v => updateValidation({ max_length: v })} /></label>
              <label className="pin__field pin__field--wide"><span>Espressione regolare</span><FieldInput className="nodrag" codec={PATTERN} value={data.validation?.pattern || null} onCommit={v => updateValidation({ pattern: v || null })} placeholder="es. [A-Z]{3}" /></label>
            </div>
          )}
          {(kind === 'number' || kind === 'integer') && (
            <div className="pin__validation-grid">
              <label className="pin__field"><span>Valore minimo</span><FieldInput className="nodrag" codec={BOUND} value={data.validation?.minimum ?? null} onCommit={v => updateValidation({ minimum: v })} /></label>
              <label className="pin__field"><span>Valore massimo</span><FieldInput className="nodrag" codec={BOUND} value={data.validation?.maximum ?? null} onCommit={v => updateValidation({ maximum: v })} /></label>
            </div>
          )}
          <div className="pin__binding">
            <div className="pin__binding-title">Collegamenti al canvas</div>
            {bindingEdges.length === 0 && (
              <p className="pin__binding-hint pin__binding-hint--warning">
                Trascina il collegamento sul nodo da configurare oppure usa
                «{termName('runtime_input')}» accanto a un suo parametro.
              </p>
            )}
            {bindingEdges.map(edge => {
              const targetNode = nodes.find(node => node.id === edge.target);
              const options = getNodeBindableTargets(targetNode, registry);
              // Nome del passo come nel resto dell'interfaccia (nodeDescriptors).
              const targetLabel = targetNode ? nodeTitle(targetNode.type, targetNode.data) : edge.target;
              const currentField = edge.data?.target_field || '';
              const awaitingRegistry = targetNode?.type === 'transform'
                && registryStatus !== 'ready';
              return (
                <div className="pin__binding-row" key={edge.id}>
                  <div className="pin__binding-target">
                    <span>Destinazione</span>
                    <strong>{targetLabel}</strong>
                    <button type="button" className="nodrag" disabled={isReadOnly} onClick={() => removeBinding(edge)} title="Rimuovi collegamento">×</button>
                  </div>
                  <label className="pin__field">
                    <span>Parametro configurabile</span>
                    <select
                      className="nodrag"
                      value={currentField}
                      onChange={event => updateBinding(edge.id, event.target.value)}
                      disabled={isReadOnly || !targetNode || awaitingRegistry || options.length === 0}
                    >
                      <option value="">Seleziona il parametro…</option>
                      {currentField && !options.some(item => item.value === currentField) && (
                        <option value={currentField}>{currentField} (vecchio formato)</option>
                      )}
                      {options.map(option => (
                        <option key={option.value} value={option.value}>{option.label}</option>
                      ))}
                    </select>
                  </label>
                  {awaitingRegistry && registryStatus !== 'error' && (
                    <p className="pin__binding-hint">
                      Caricamento parametri configurabili…
                    </p>
                  )}
                  {awaitingRegistry && registryStatus === 'error' && (
                    <p className="pin__binding-hint pin__binding-hint--error">
                      Impossibile caricare l'elenco dei parametri.{' '}
                      <button type="button" {...READ_ONLY_SAFE} className="nodrag list-state__retry" onClick={() => registryResource.reload()}>Riprova</button>
                    </p>
                  )}
                  {targetNode && !awaitingRegistry && options.length === 0 && (
                    <p className="pin__binding-hint pin__binding-hint--error">
                      Il passo collegato non ha parametri collegabili a un input runtime.
                    </p>
                  )}
                  {!awaitingRegistry && !currentField && options.length > 0 && (
                    <p className="pin__binding-hint pin__binding-hint--warning">
                      Seleziona il parametro da valorizzare.
                    </p>
                  )}
                </div>
              );
            })}
          </div>
        </>
      )}

      </div>

      {kind !== 'connection' && (
        <NodeHandle
          type="source"
          position={Position.Right}
          id={kind === 'file' ? 'output' : 'parameter'}
          className="pin__handle"
          isConnectable={!isReadOnly}
        />
      )}
    </div>
  );
}

export default memo(ProjectInputNode);
