/**
 * SelectColumnsPanel — operazione "Seleziona e rinomina" (Fase 4, M5;
 * backend B5): la griglia condivisa sulle colonne a monte.
 */
import React, { useMemo } from 'react';
import ColumnsGrid from './ColumnsGrid';
import { resolveSelectNames } from '../../../utils/columnPlans';
import { gridConfig, gridRows, savedSelectionError } from './panelConfig';

export default function SelectColumnsPanel({ config, updateConfig, inputColumns = [], disabled }) {
  const rows = useMemo(() => gridRows(inputColumns, config.columns), [inputColumns, config.columns]);
  return (
    <ColumnsGrid
      rows={rows}
      disabled={disabled}
      savedError={savedSelectionError(inputColumns, resolveSelectNames(inputColumns, config.columns))}
      onChange={next => updateConfig({ columns: gridConfig(next, inputColumns) })}
      note="Un solo passo per tenere, rinominare e ordinare. Le colonne nuove a monte compaiono qui da sole."
    />
  );
}
