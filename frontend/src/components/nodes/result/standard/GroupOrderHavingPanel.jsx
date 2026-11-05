/**
 * GroupOrderHavingPanel.jsx — Sezioni GROUP BY / ORDER BY / HAVING
 * del Result node.
 * Componente presentational locale: niente fetch, niente API,
 * niente global state. Tutta la persistenza passa per
 * `updateNodeData` con i payload identici all'originale.
 *
 * State locale (UI expand/collapse): `showGroupBySection` /
 * `showOrderBySection`. Sono puro UI scope-locale ai pannelli e
 * non escapano dal panel. La sezione HAVING non ha un toggle di
 * espansione (e' nested dentro GROUP BY quando attivo + ha
 * aggregazioni), quindi nessuno state locale serve.
 *
 * `aggregateFunctions` co-locata qui perche' usata solo da questo
 * panel (sia per le aggregazioni del GROUP BY sia per le
 * condizioni HAVING).
 *
 * `groupByAvailableColumns` derivata internamente da `isManipulate`
 * + (`manipolaColumns` | `availableColumns`). L'"Output previsto" usa
 * `outputColumns`, le colonne in uscita dell'Output previste dal server
 * (fonte unica, Blocco A regola 4): nessuna regola dei nomi copiata qui.
 *
 * Gating top-level: il rendering del wrapper esterno avviene solo
 * se almeno un blocco e' visibile, mantenendo il comportamento
 * dell'originale (sezione GROUP/ORDER mostrata se siamo in ESTRAI
 * o MANIPOLA *e* ci sono colonne disponibili; HAVING mostrata solo
 * se GROUP BY attivo + almeno una aggregazione).
 *
 * I CSS `result-node__*` restano in `ResultNode.css` (shared col
 * parent).
 */
import { uniqueName } from '../../../../utils/uniqueName';
import React, { useState } from 'react';
import { getColumnTypeIcon } from '../../../../utils/columnUtils';

/**
 * Voci dell'"Output previsto" del GROUP BY: [tipo ('group'|'agg'), nome].
 *  - Nessuna aggregazione: non e' un raggruppamento eseguibile, e il server
 *    restituisce tutte le colonne (non aggregate): non si mostrano come
 *    aggregazioni. Manipola: lista vuota; gli altri: le colonne di GROUP BY
 *    configurate (come prima della F2).
 *  - Con aggregazioni: i nomi veri del file previsti dal server (x_mean, non
 *    avg(x)); prima della risposta, la configurazione.
 */
export function groupPreviewItems({ isManipulate, groupByColumns = [], aggregations = [], outputColumns = [] }) {
  if (aggregations.length === 0) return isManipulate ? [] : groupByColumns.map(g => ['group', g]);
  if (outputColumns.length > 0) return outputColumns.map(c => [groupByColumns.includes(c.name) ? 'group' : 'agg', c.name]);
  return [...groupByColumns.map(g => ['group', g]),
    ...aggregations.map(agg => ['agg', agg.alias || `${agg.function}(${agg.column})`])];
}

/**
 * Alias delle aggregazioni dell'Output (Fase 5, T6, R3.17; unione con T5
 * R8.19: la regola e' questa, il suffisso viene da `uniqueName`).
 *  - L'alias generato e' `funzione_colonna` (`*` -> `all`), reso unico tra le
 *    aggregazioni del nodo (`count_all`, `count_all_2`...): mai due colonne
 *    con lo stesso nome dopo una rimozione (prima `count_${n}`).
 *  - Un alias scritto dall'utente (`alias_source: 'user'`) non si tocca piu'
 *    cambiando colonna o funzione. Gli alias senza marcatore (salvati prima)
 *    sono generati se hanno la forma generata (`count_3`, `sum_area`).
 */
const baseAlias = (agg) => `${String(agg.function || 'COUNT').toLowerCase()}_${agg.column === '*' || !agg.column ? 'all' : String(agg.column).replace(/\./g, '_')}`;

export function uniqueAlias(base, aggregations, skipIndex = -1) {
  // Il suffisso viene dalla regola unica dei nomi generati (utils/uniqueName,
  // T5 R8.19): primo libero tra quelli gia' usati, mai il conteggio.
  const taken = (aggregations || []).filter((_, i) => i !== skipIndex).map(a => a.alias);
  return uniqueName(base, taken, { bareFirst: true });
}

export function isGeneratedAlias(agg) {
  if (agg?.alias_source === 'user') return false;
  const alias = agg?.alias || '';
  if (!alias) return true;
  if (/^count_\d+$/.test(alias)) return true;
  const base = baseAlias(agg);
  if (alias === base) return true;
  return alias.startsWith(`${base}_`) && /^\d+$/.test(alias.slice(base.length + 1));
}

const AGGREGATE_FUNCTIONS = [
  { value: 'COUNT', label: 'COUNT', desc: 'Conta' },
  { value: 'SUM', label: 'SUM', desc: 'Somma' },
  { value: 'AVG', label: 'AVG', desc: 'Media' },
  { value: 'MIN', label: 'MIN', desc: 'Minimo' },
  { value: 'MAX', label: 'MAX', desc: 'Massimo' },
];

export default function GroupOrderHavingPanel({
  data,
  id,
  updateNodeData,
  isEstrai,
  isManipulate,
  availableColumns,
  manipolaColumns,
  outputColumns = [],
}) {
  const [showGroupBySection, setShowGroupBySection] = useState(false);
  const [showOrderBySection, setShowOrderBySection] = useState(false);

  const groupByEnabled = data.group_by_enabled || false;
  const groupByColumns = data.group_by_columns || [];
  const aggregations = data.aggregations || [];
  const orderByEnabled = data.order_by_enabled || false;
  const orderByColumns = data.order_by_columns || [];
  const havingEnabled = data.having_enabled || false;
  const havingConditions = data.having_conditions || [];

  const groupByAvailableColumns = isManipulate ? manipolaColumns : availableColumns;
  // Colonne configurate che non sono piu' tra quelle disponibili (sparite o
  // rinominate a monte): si segnalano, non si mostrano come valide (R3.17).
  const availableNames = new Set(groupByAvailableColumns.map(c => c.name));
  const missing = (name) => Boolean(name) && name !== '*' && groupByAvailableColumns.length > 0 && !availableNames.has(name);
  const labelOf = (col) => col.label || col.name;
  const showGroupOrderGate =
    (isEstrai || isManipulate) &&
    (availableColumns.length > 0 || manipolaColumns.length > 0);
  const showHavingGate = groupByEnabled && aggregations.length > 0;

  const handleToggleGroupBy = () => {
    // Spegnere il GROUP BY toglie anche l'HAVING (che senza GROUP BY non
    // entra nella query): prima restava nei dati e riappariva (R3.17).
    updateNodeData(id, {
      group_by_enabled: !groupByEnabled,
      group_by_columns: groupByEnabled ? [] : groupByColumns,
      aggregations: groupByEnabled ? [] : aggregations,
      ...(groupByEnabled ? { having_enabled: false, having_conditions: [] } : {}),
    });
  };
  const handleAddGroupByColumn = (columnName) => {
    if (!columnName || groupByColumns.includes(columnName)) return;
    updateNodeData(id, {
      group_by_enabled: true,
      group_by_columns: [...groupByColumns, columnName],
      aggregations,
    });
  };
  const handleRemoveGroupByColumn = (columnName) => {
    updateNodeData(id, {
      group_by_enabled: groupByEnabled,
      group_by_columns: groupByColumns.filter(c => c !== columnName),
      aggregations,
    });
  };
  const handleAddAggregation = () => {
    const newAgg = {
      column: '*',
      function: 'COUNT',
      alias: uniqueAlias('count_all', aggregations),
    };
    updateNodeData(id, {
      group_by_enabled: true,
      group_by_columns: groupByColumns,
      aggregations: [...aggregations, newAgg],
    });
  };
  const handleUpdateAggregation = (index, field, value) => {
    const newAggregations = [...aggregations];
    const before = newAggregations[index];
    newAggregations[index] = { ...before, [field]: value };
    if (field === 'alias') {
      // Scritto dall'utente: da qui in poi non si rigenera (vuoto = generato).
      newAggregations[index] = { ...newAggregations[index], alias_source: value ? 'user' : undefined };
    } else if ((field === 'column' || field === 'function') && isGeneratedAlias(before)) {
      newAggregations[index].alias = uniqueAlias(baseAlias(newAggregations[index]), newAggregations, index);
    }
    updateNodeData(id, {
      group_by_enabled: groupByEnabled,
      group_by_columns: groupByColumns,
      aggregations: newAggregations,
    });
  };
  const handleRemoveAggregation = (index) => {
    updateNodeData(id, {
      group_by_enabled: groupByEnabled,
      group_by_columns: groupByColumns,
      aggregations: aggregations.filter((_, i) => i !== index),
    });
  };

  const handleToggleOrderBy = () => {
    updateNodeData(id, {
      order_by_enabled: !orderByEnabled,
      order_by_columns: orderByEnabled ? [] : orderByColumns,
    });
  };
  const handleAddOrderByColumn = (columnName) => {
    if (!columnName || orderByColumns.some(c => c.column === columnName)) return;
    updateNodeData(id, {
      order_by_enabled: true,
      order_by_columns: [...orderByColumns, { column: columnName, direction: 'ASC' }],
    });
  };
  const handleRemoveOrderByColumn = (index) => {
    updateNodeData(id, {
      order_by_columns: orderByColumns.filter((_, i) => i !== index),
    });
  };
  const handleToggleOrderByDirection = (index) => {
    const newColumns = [...orderByColumns];
    newColumns[index] = {
      ...newColumns[index],
      direction: newColumns[index].direction === 'ASC' ? 'DESC' : 'ASC',
    };
    updateNodeData(id, { order_by_columns: newColumns });
  };

  const handleToggleHaving = () => {
    updateNodeData(id, {
      having_enabled: !havingEnabled,
      having_conditions: havingEnabled ? [] : havingConditions,
    });
  };
  const handleAddHavingCondition = () => {
    updateNodeData(id, {
      having_enabled: true,
      having_conditions: [...havingConditions, {
        function: 'COUNT', column: '*', operator: '>', value: '0',
      }],
    });
  };
  const handleUpdateHavingCondition = (index, field, value) => {
    const newConditions = [...havingConditions];
    newConditions[index] = { ...newConditions[index], [field]: value };
    updateNodeData(id, { having_conditions: newConditions });
  };
  const handleRemoveHavingCondition = (index) => {
    updateNodeData(id, {
      having_conditions: havingConditions.filter((_, i) => i !== index),
    });
  };

  return (
    <>
      {/* GROUP BY — ESTRAI e MANIPOLA */}
      {showGroupOrderGate && (
        <div className="result-node__section result-node__section--bordered">
          <div
            className="result-node__groupby-header"
            onClick={() => setShowGroupBySection(!showGroupBySection)}
          >
            <div className="result-node__label result-node__label--clickable">
              <span>📊 GROUP BY (Raggruppamento)</span>
              {groupByEnabled && (
                <span className="result-node__groupby-badge">Attivo</span>
              )}
            </div>
            <span className="result-node__groupby-toggle">
              {showGroupBySection ? '▼' : '▶'}
            </span>
          </div>

          {showGroupBySection && (
            <div className="result-node__groupby-content">
              <label className="result-node__checkbox-label">
                <input
                  type="checkbox"
                  checked={groupByEnabled}
                  onChange={handleToggleGroupBy}
                  className="result-node__checkbox"
                />
                <span>Abilita raggruppamento</span>
              </label>

              {groupByEnabled && (
                <>
                  <div className="result-node__groupby-section">
                    <div className="result-node__label">Raggruppa per:</div>

                    {groupByColumns.length > 0 && (
                      <div className="result-node__groupby-selected">
                        {groupByColumns.map((col, idx) => (
                          <span key={idx} className={`result-node__groupby-tag${missing(col) ? ' result-node__groupby-tag--missing' : ''}`}
                            title={missing(col) ? 'Colonna non più disponibile a monte' : undefined}>
                            {missing(col) ? `⚠️ ${col}` : col}
                            <button type="button"
                              onClick={() => handleRemoveGroupByColumn(col)}
                              className="result-node__groupby-tag-remove"
                            >
                              ×
                            </button>
                          </span>
                        ))}
                      </div>
                    )}

                    <select
                      value=""
                      onChange={(e) => handleAddGroupByColumn(e.target.value)}
                      className="result-node__groupby-dropdown"
                    >
                      <option value="">+ Aggiungi colonna...</option>
                      {groupByAvailableColumns
                        .filter(col => !groupByColumns.includes(col.name))
                        .map((col, idx) => (
                          <option key={idx} value={col.name}>
                            {getColumnTypeIcon(col.type)} {labelOf(col)}
                          </option>
                        ))}
                    </select>
                  </div>

                  <div className="result-node__groupby-section">
                    <div className="result-node__label">Aggregazioni:</div>

                    {aggregations.map((agg, idx) => (
                      <div key={idx} className="result-node__aggregation-row">
                        <select
                          value={agg.function}
                          onChange={(e) => handleUpdateAggregation(idx, 'function', e.target.value)}
                          className="result-node__agg-select result-node__agg-select--func"
                        >
                          {AGGREGATE_FUNCTIONS.map(f => (
                            <option key={f.value} value={f.value}>{f.label}</option>
                          ))}
                        </select>

                        <select
                          value={agg.column}
                          onChange={(e) => handleUpdateAggregation(idx, 'column', e.target.value)}
                          className="result-node__agg-select result-node__agg-select--col"
                        >
                          <option value="*">* (tutte)</option>
                          {missing(agg.column) && <option value={agg.column}>⚠️ {agg.column} (non più disponibile)</option>}
                          {groupByAvailableColumns.map((col, colIdx) => (
                            <option key={colIdx} value={col.name}>{labelOf(col)}</option>
                          ))}
                        </select>

                        <input
                          type="text"
                          value={agg.alias || ''}
                          onChange={(e) => handleUpdateAggregation(idx, 'alias', e.target.value)}
                          placeholder="alias"
                          className="result-node__agg-alias"
                        />

                        <button type="button"
                          onClick={() => handleRemoveAggregation(idx)}
                          className="result-node__agg-remove"
                          title="Rimuovi aggregazione"
                        >
                          ×
                        </button>
                      </div>
                    ))}

                    <button type="button"
                      onClick={handleAddAggregation}
                      className="result-node__add-agg-btn"
                    >
                      + Aggiungi aggregazione
                    </button>
                  </div>

                  {(groupByColumns.length > 0 || aggregations.length > 0) && (
                    <div className="result-node__groupby-preview">
                      <div className="result-node__label">📋 Output previsto:</div>
                      <div className="result-node__groupby-preview-list">
                        {/* groupPreviewItems: nomi previsti dal server (x_mean, non
                            avg(x)), solo con aggregazioni. */}
                        {groupPreviewItems({ isManipulate, groupByColumns, aggregations, outputColumns }).map(([kind, label], idx) => (
                          <span key={`${kind}-${idx}`} className={`result-node__preview-col result-node__preview-col--${kind === 'group' ? 'group' : 'agg'}`}>
                            {label}
                          </span>
                        ))}
                      </div>
                    </div>
                  )}
                </>
              )}
            </div>
          )}
        </div>
      )}

      {/* ORDER BY — ESTRAI e MANIPOLA */}
      {showGroupOrderGate && (
        <div className="result-node__section result-node__section--bordered">
          <div
            className="result-node__groupby-header"
            onClick={() => setShowOrderBySection(!showOrderBySection)}
          >
            <div className="result-node__label result-node__label--clickable">
              <span>↕️ ORDER BY (Ordinamento)</span>
              {orderByEnabled && orderByColumns.length > 0 && (
                <span className="result-node__groupby-badge">Attivo</span>
              )}
            </div>
            <span className="result-node__groupby-toggle">
              {showOrderBySection ? '▼' : '▶'}
            </span>
          </div>

          {showOrderBySection && (
            <div className="result-node__groupby-content">
              <label className="result-node__checkbox-label">
                <input
                  type="checkbox"
                  checked={orderByEnabled}
                  onChange={handleToggleOrderBy}
                  className="result-node__checkbox"
                />
                <span>Abilita ordinamento</span>
              </label>

              {orderByEnabled && (
                <>
                  {orderByColumns.length > 0 && (
                    <div className="result-node__groupby-selected">
                      {orderByColumns.map((item, idx) => (
                        <span key={idx} className={`result-node__groupby-tag${missing(item.column) ? ' result-node__groupby-tag--missing' : ''}`}
                          title={missing(item.column) ? 'Colonna non più disponibile a monte' : undefined}>
                          {missing(item.column) ? `⚠️ ${item.column}` : item.column}
                          <button type="button"
                            onClick={() => handleToggleOrderByDirection(idx)}
                            className="result-node__orderby-dir-btn"
                            title={`Cambia direzione (${item.direction})`}
                          >
                            {item.direction === 'ASC' ? '↑' : '↓'}
                          </button>
                          <button type="button"
                            onClick={() => handleRemoveOrderByColumn(idx)}
                            className="result-node__groupby-tag-remove"
                          >
                            ×
                          </button>
                        </span>
                      ))}
                    </div>
                  )}

                  <select
                    value=""
                    onChange={(e) => handleAddOrderByColumn(e.target.value)}
                    className="result-node__groupby-dropdown"
                  >
                    <option value="">+ Aggiungi colonna...</option>
                    {groupByAvailableColumns
                      .filter(col => !orderByColumns.some(c => c.column === col.name))
                      .map((col, idx) => (
                        <option key={idx} value={col.name}>
                          {getColumnTypeIcon(col.type)} {labelOf(col)}
                        </option>
                      ))}
                  </select>
                </>
              )}
            </div>
          )}
        </div>
      )}

      {/* HAVING — solo quando GROUP BY attivo e ci sono aggregazioni */}
      {showHavingGate && (
        <div className="result-node__section result-node__section--bordered">
          <div className="result-node__groupby-content">
            <label className="result-node__checkbox-label">
              <input
                type="checkbox"
                checked={havingEnabled}
                onChange={handleToggleHaving}
                className="result-node__checkbox"
              />
              <span>🔍 HAVING (Filtro su aggregati)</span>
            </label>

            {havingEnabled && (
              <div className="result-node__groupby-section">
                {havingConditions.map((cond, idx) => (
                  <div key={idx} className="result-node__aggregation-row">
                    <select
                      value={cond.function}
                      onChange={(e) => handleUpdateHavingCondition(idx, 'function', e.target.value)}
                      className="result-node__agg-select result-node__agg-select--func"
                    >
                      {AGGREGATE_FUNCTIONS.map(f => (
                        <option key={f.value} value={f.value}>{f.label}</option>
                      ))}
                    </select>

                    <select
                      value={cond.column}
                      onChange={(e) => handleUpdateHavingCondition(idx, 'column', e.target.value)}
                      className="result-node__agg-select result-node__agg-select--col"
                    >
                      <option value="*">* (tutte)</option>
                      {missing(cond.column) && <option value={cond.column}>⚠️ {cond.column} (non più disponibile)</option>}
                      {groupByAvailableColumns.map((col, colIdx) => (
                        <option key={colIdx} value={col.name}>{labelOf(col)}</option>
                      ))}
                    </select>

                    <select
                      value={cond.operator}
                      onChange={(e) => handleUpdateHavingCondition(idx, 'operator', e.target.value)}
                      className="result-node__agg-select result-node__agg-select--func"
                    >
                      <option value=">">&gt;</option>
                      <option value=">=">&gt;=</option>
                      <option value="<">&lt;</option>
                      <option value="<=">&lt;=</option>
                      <option value="=">=</option>
                      <option value="!=">!=</option>
                    </select>

                    <input
                      type="text"
                      value={cond.value}
                      onChange={(e) => handleUpdateHavingCondition(idx, 'value', e.target.value)}
                      placeholder="valore"
                      className="result-node__agg-alias"
                    />

                    <button type="button"
                      onClick={() => handleRemoveHavingCondition(idx)}
                      className="result-node__agg-remove"
                      title="Rimuovi condizione"
                    >
                      ×
                    </button>
                  </div>
                ))}

                <button type="button"
                  onClick={handleAddHavingCondition}
                  className="result-node__add-agg-btn"
                >
                  + Aggiungi condizione HAVING
                </button>
              </div>
            )}
          </div>
        </div>
      )}
    </>
  );
}
