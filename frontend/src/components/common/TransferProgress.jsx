/**
 * TransferProgress — barra di avanzamento di un caricamento o di uno
 * scaricamento (UI-3, U17). Riceve `progress` da hooks/useTransferProgress:
 * con la dimensione nota percentuale e byte («Caricamento 45% · 4,5 MB di
 * 10 MB»), senza solo i byte trasferiti. Nulla senza trasferimento in corso.
 */
import React from 'react';
import { formatBytes } from '../../utils/format';
import './TransferProgress.css';

export default function TransferProgress({ progress, verb = 'Trasferimento' }) {
  if (!progress) return null;
  const { loaded, total, percent } = progress;
  const text = percent === null
    ? `${verb}: ${formatBytes(loaded)}`
    : `${verb} ${percent}% · ${formatBytes(loaded)} di ${formatBytes(total)}`;
  return (
    <div className="transfer-progress" role="status" aria-live="polite">
      <div
        className="transfer-progress__bar"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percent ?? undefined}
        aria-label={verb}
      >
        <span
          className={`transfer-progress__fill${percent === null ? ' transfer-progress__fill--unknown' : ''}`}
          style={percent === null ? undefined : { width: `${percent}%` }}
        />
      </div>
      <span className="transfer-progress__text">{text}</span>
    </div>
  );
}
