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
 *   - deriva localmente nodeVariant/executeLabel da data.isManipulate/isArricchisci.
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
 *     `executeLabel`, `hasExecutionOptions`, `isArricchisci` + le 3 callback;
 *   - ValidationModal: stato del parent, triggerata sia da qui sia da IMDF view;
 *   - logica runValidation/onExecute/onPreview: ricevute come callback;
 *   - upstreamColumns: computata dal parent (serve useReactFlow).
 *
 * Props (8):
 *   data                         — bag dati del nodo (fileName, format, columns, ...)
 *   id                           — node id (per updateNodeData + RunHistoryModal)
 *   upstreamColumns              — colonne dai nodi collegati (calcolate nel parent)
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

const ResultNodeStandardView = memo(({
  data,
  id,
  upstreamColumns,
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

  // `format` resta nel parent perche' serve a derivare `isGeoFormat`
  // sia per il useEffect di auto-reset (CSV se non ci sono piu' colonne
  // geometriche) sia come prop al panel.
  const format = data.format || 'csv';

  // `groupByEnabled` resta nel parent perche' serve sia all'`OutputSettingsPanel`
  // (gating disabling DISTINCT) sia al gate della preview "Colonne Output".
  const groupByEnabled = data.group_by_enabled || false;

  // Formato geospaziale?
  const isGeoFormat = ['geojson', 'gpkg', 'shp'].includes(format);

  // Colonne geometriche candidate (MANIPOLA/ARRICCHISCI: upstream; ESTRAI: merge columns+upstream)
  // Per Manipola/Arricchisci si fa fallback a `data.columns` (autoritativo
  // dal canvas persistito post schema-inference) quando `upstreamColumns`
  // e' ancora vuoto al primo render — altrimenti l'auto-reset format -> csv
  // (sotto) scattava su un transient e azzerava la scelta utente di geojson.
  const geomSourceColumns = (() => {
    if (isManipulate || isArricchisci) {
      // Dopo "Colonne in uscita": la geometria esce col nome nuovo, o non esce.
      const base = upstreamColumns.length > 0 ? upstreamColumns
        : ((data.columns || []).length > 0 ? data.columns : (data.availableColumns || []));
      return applySelectColumns(base, data.output_columns, { skipMissing: true });
    }
    const cols = data.columns || [];
    if (upstreamColumns.length > 0) {
      const existingNames = new Set(cols.map(c => c.name || c.alias));
      const extraCols = upstreamColumns.filter(c => !existingNames.has(c.name));
      return [...cols, ...extraCols];
    }
    return cols;
  })();

  const availableGeomColumns = geomSourceColumns
    .filter(col => isGeometryType(col.type))
    .map(col => {
      const displayName = col.alias || col.name || col;
      return col.source_table ? `${col.source_table}.${displayName}` : displayName;
    });

  const hasGeometryColumns = availableGeomColumns.length > 0;

  // Auto-reset: se formato e' geo ma non ci sono piu' colonne geometry → CSV
  useEffect(() => {
    if (!hasGeometryColumns && isGeoFormat) {
      // Correzione automatica, non una scelta dell'utente: non dirty (R1.8).
      updateNodeData(id, { format: 'csv' }, { derived: true });
    }
  }, [hasGeometryColumns, isGeoFormat, id]);

  // Colonne disponibili per GROUP BY / output (SQL) — deduplicate per nome
  const availableColumnsRaw = (data.columns || []).map(col => ({
    name: col.alias || col.name,
    type: col.type || 'str',
    fullName: col.full_name || `${col.source_table}.${col.alias || col.name}`,
  }));
  const availableColumns = [...new Map(availableColumnsRaw.map(col => [col.name, col])).values()];

  // Colonne disponibili MANIPOLA (upstream o fallback availableColumns) — deduplicate
  const manipolaSourceCols = upstreamColumns.length > 0 ? upstreamColumns : (data.availableColumns || []);
  const manipolaColumnsRaw = manipolaSourceCols.map(col => ({
    name: col.name || col,
    type: col.type || 'str',
    fullName: col.name || col,
  }));
  const manipolaUpstream = [...new Map(manipolaColumnsRaw.map(col => [col.name, col])).values()];
  // GROUP BY / ORDER BY lavorano sulle colonne DOPO "Colonne in uscita"
  // (il backend applica output_columns prima del GROUP BY).
  const manipolaColumns = applySelectColumns(manipolaUpstream, data.output_columns, { skipMissing: true })
    .map(col => ({ ...col, fullName: col.name }));
  const isReadOnly = useCanvasStore(s => s.isReadOnly);

  // `groupByAvailableColumns` ora derivato dentro `GroupOrderHavingPanel`
  // dai props `availableColumns` / `manipolaColumns` / `isManipulate`.

  // Variante/label derivate localmente
  const nodeVariant = isArricchisci ? 'arricchisci' : (isManipulate ? 'manipola' : 'estrai');
  const config = {
    estrai: { executeLabel: 'Esegui Query' },
    manipola: { executeLabel: 'Esegui Pipeline' },
    arricchisci: { executeLabel: 'Esegui Arricchimento' },
  };
  const { executeLabel } = config[nodeVariant];

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
        {isEstrai && !groupByEnabled && <EstraiOutputColumns id={id} disabled={isReadOnly} />}
        {!isEstrai && (
          <PipelineOutputColumns id={id} data={data} upstreamColumns={manipolaUpstream} disabled={isReadOnly} />
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
      />

      <ExecuteActionsPanel
        data={data}
        id={id}
        nodeVariant={nodeVariant}
        executeLabel={executeLabel}
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
