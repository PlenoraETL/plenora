/**
 * ResultNodeStandardView — unica view del ResultNode (per ogni upstream).
 *
 * (Post-collasso IMDF a una-run non esiste piu' una vista IMDF dedicata:
 * il bundle e' un artefatto JSON come gli altri.)
 *
 * Owna:
 *   - dispatch a `OutputSettingsPanel` (filename + format + geo + distinct + limit);
 *   - "Colonne in uscita" (OutputColumnsSection, Fase 4 M5), passata come
 *     children al panel per preservare l'ordine visivo;
 *   - dispatch a `GroupOrderHavingPanel` (GROUP BY + aggregazioni + ORDER BY + HAVING);
 *   - dispatch a `ExecuteActionsPanel` (execution mode + bottoni Valida/Preview/
 *     Esegui + footer cronologia + RunHistoryModal);
 *   - deriva localmente nodeVariant da data.isManipulate/isArricchisci.
 *
 * NON owna:
 *   - editor filename + scelta formato + DISTINCT + LIMIT: gestiti da
 *     `OutputSettingsPanel`; il parent passa i flag derivati
 *     `isGeoFormat`, `availableGeomColumns`, `hasGeometryColumns` come props;
 *   - GROUP BY / ORDER BY / HAVING + relativi handler su updateNodeData:
 *     gestiti da `GroupOrderHavingPanel`; il parent
 *     passa `availableColumns`, `manipolaColumns`, `isEstrai`, `isManipulate`
 *     come props;
 *   - executionMode + isRunHistoryOpen useState + RunHistoryModal: gestiti da
 *     `ExecuteActionsPanel`; il parent passa `nodeVariant`,
 *     `hasExecutionOptions`, `isArricchisci` + le 3 callback;
 *   - ValidationModal: stato del parent, triggerata sia da qui sia da IMDF view;
 *   - logica runValidation/onExecute/onPreview: ricevute come callback;
 *   - le colonne: arrivano dal parent, che le legge dalla fonte unica
 *     (hooks/useColumns, Blocco A regola 4). Qui nessun dato derivato
 *     viene letto da `data` (data.columns / availableColumns non esistono piu').
 *
 * Props:
 *   data                         — bag dati del nodo (fileName, format, output_columns, ...)
 *   id                           — node id (per updateNodeData + RunHistoryModal)
 *   upstreamColumns              — colonne in ingresso, con gli id (Estrai: anche source_table)
 *   outputColumns                — colonne in uscita previste dal server
 *   columnsResult                — esito di columnsOf per l'ingresso (stato, errore, known)
 *   hasOnPreview, hasOnExecute   — sottoscrizioni reattive dal parent (gating bottoni)
 *   onValidate()                 — handler Valida (parent apre ValidationModal)
 *   onExecute(options?)          — handler Esegui; options={force_recalculate: bool}
 *   onPreview()                  — handler Preview (parent fa validate + run)
 *
 * Il default di onExecute senza options e' gestito dal parent (force_recalculate=false),
 * cosi' ImdfView puo' continuare a chiamare onExecute() senza conoscere executionMode.
 */
import React, { useEffect, memo } from 'react';
import useCanvasStore from '../../../store/useCanvasStore';
import { isGeometryType } from '../../../utils/columnUtils';
import OutputSettingsPanel from './standard/OutputSettingsPanel';
import GroupOrderHavingPanel from './standard/GroupOrderHavingPanel';
import ExecuteActionsPanel from './standard/ExecuteActionsPanel';
import { EstraiOutputColumns, PipelineOutputColumns } from './standard/OutputColumnsSection';
import { applySelectColumns } from '../../../utils/columnPlans';
import ColumnStatusNote from '../../common/ColumnStatusNote';
import { columnsConfirmed, outputColumnsAsRead } from '../../../utils/columnSource';

const NONE = [];

const ResultNodeStandardView = memo(({
  data,
  id,
  upstreamColumns = NONE,
  outputColumns = NONE,
  columnsResult = null,
  hasOnPreview,
  hasOnExecute,
  onValidate,
  onExecute,
  onPreview,
}) => {
  const updateNodeData = useCanvasStore(s => s.updateNodeData);

  // Tipi progetto (derivati da data)
  const isManipulate = data.isManipulate || false;
  const isArricchisci = data.isArricchisci || false;
  const isEstrai = !isManipulate && !isArricchisci;
  const hasExecutionOptions = isManipulate || isArricchisci;
  // Variante = tipo di progetto (stili, e come il backend legge output_columns).
  const nodeVariant = isArricchisci ? 'arricchisci' : (isManipulate ? 'manipola' : 'estrai');
  // "Colonne in uscita" come le legge il backend di questo tipo di progetto:
  // per id solo dove risolve per id (columnSource.PROJECT_COLUMN_CAPABILITIES).
  const outputConfig = outputColumnsAsRead(data.output_columns, nodeVariant);

  // `format` resta nel parent perche' serve a derivare `isGeoFormat`
  // sia per il useEffect di auto-reset (CSV se non ci sono piu' colonne
  // geometriche) sia come prop al panel.
  const format = data.format || 'csv';

  // `groupByEnabled` resta nel parent perche' serve sia all'`OutputSettingsPanel`
  // (gating disabling DISTINCT) sia al gate della preview "Colonne Output".
  const groupByEnabled = data.group_by_enabled || false;

  // Formato geospaziale?
  const isGeoFormat = ['geojson', 'gpkg', 'shp'].includes(format);

  // Colonne geometriche candidate. Manipola/Arricchisci: l'ingresso dopo
  // "Colonne in uscita" (la geometria esce col nome nuovo, o non esce);
  // Estrai: le colonne delle tabelle e dei layer a monte (tabella.colonna).
  const geomSourceColumns = (isManipulate || isArricchisci)
    ? applySelectColumns(upstreamColumns, outputConfig, { skipMissing: true })
    : upstreamColumns;

  const availableGeomColumns = geomSourceColumns
    .filter(col => isGeometryType(col.type))
    .map(col => {
      const displayName = col.alias || col.name || col;
      return col.source_table ? `${col.source_table}.${displayName}` : displayName;
    });

  const hasGeometryColumns = availableGeomColumns.length > 0;

  // Auto-reset: se formato e' geo ma non ci sono piu' colonne geometry → CSV.
  // Solo su colonne confermate dal server: prima della risposta (o con un
  // errore di rete) le colonne mancano solo per ora, e la scelta resta.
  const confirmed = columnsConfirmed(columnsResult);
  useEffect(() => {
    if (confirmed && !hasGeometryColumns && isGeoFormat) {
      // Correzione automatica, non una scelta dell'utente: non dirty (R1.8).
      updateNodeData(id, { format: 'csv' }, { derived: true });
    }
  }, [confirmed, hasGeometryColumns, isGeoFormat, id]);

  // Colonne disponibili per GROUP BY / ORDER BY (Estrai, SQL): le colonne
  // delle tabelle a monte, nomi senza doppioni.
  const availableColumnsRaw = isEstrai ? upstreamColumns.map(col => ({
    name: col.name,
    type: col.type || 'str',
    fullName: col.source_table ? `${col.source_table}.${col.name}` : col.name,
  })) : NONE;
  const availableColumns = [...new Map(availableColumnsRaw.map(col => [col.name, col])).values()];

  // Colonne a monte di Manipola (con gli id), senza doppioni per nome.
  const manipolaUpstream = [...new Map(upstreamColumns.map(col => [col.name, col])).values()];
  // GROUP BY / ORDER BY lavorano sulle colonne DOPO "Colonne in uscita"
  // (il backend applica output_columns prima del GROUP BY).
  const manipolaColumns = applySelectColumns(manipolaUpstream, outputConfig, { skipMissing: true })
    .map(col => ({ ...col, fullName: col.name }));
  const isReadOnly = useCanvasStore(s => s.isReadOnly);

  // `groupByAvailableColumns` ora derivato dentro `GroupOrderHavingPanel`
  // dai props `availableColumns` / `manipolaColumns` / `isManipulate`.

  return (
    <>
      <OutputSettingsPanel
        data={data}
        id={id}
        updateNodeData={updateNodeData}
        isEstrai={isEstrai}
        groupByEnabled={groupByEnabled}
        availableGeomColumns={availableGeomColumns}
        hasGeometryColumns={hasGeometryColumns}
        isGeoFormat={isGeoFormat}
      >
        {/* Colonne in uscita (Fase 4, M5): griglia "Seleziona e rinomina".
            Estrai: sulle colonne delle tabelle (senza GROUP BY attivo);
            Manipola/Arricchisci: output_columns, applicate prima del GROUP BY. */}
        <ColumnStatusNote result={columnsResult} />
        {isEstrai && !groupByEnabled && <EstraiOutputColumns id={id} disabled={isReadOnly} />}
        {!isEstrai && (
          <PipelineOutputColumns id={id} data={data} mode={nodeVariant} upstreamColumns={manipolaUpstream} disabled={isReadOnly} />
        )}
      </OutputSettingsPanel>

      <GroupOrderHavingPanel
        data={data}
        id={id}
        updateNodeData={updateNodeData}
        isEstrai={isEstrai}
        isManipulate={isManipulate}
        availableColumns={availableColumns}
        manipolaColumns={manipolaColumns}
        outputColumns={outputColumns}
      />

      <ExecuteActionsPanel
        data={data}
        id={id}
        nodeVariant={nodeVariant}
        hasExecutionOptions={hasExecutionOptions}
        isArricchisci={isArricchisci}
        hasOnPreview={hasOnPreview}
        hasOnExecute={hasOnExecute}
        onValidate={onValidate}
        onPreview={onPreview}
        onExecute={onExecute}
      />
    </>
  );
});

ResultNodeStandardView.displayName = 'ResultNodeStandardView';

export default ResultNodeStandardView;
