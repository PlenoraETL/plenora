/**
 * FileManager/PreviewModal.jsx — modale anteprima righe del file (rows
 * + columns dal backend), tabellare. Differente dal `PreviewModal`
 * generico delle pipeline (`components/modals/PreviewModal.jsx`):
 * questo e' specifico per il file source standalone.
 */

import React from 'react';
import ModalWrapper, { ModalButton } from '../../components/common/ModalWrapper';

function PreviewModal({ file, data, onClose }) {
  // columns può essere [{name, type}, ...] oppure ['col1', 'col2', ...]
  const colNames = (data.columns || []).map(c => (typeof c === 'object' ? c.name : c));
  return (
    <ModalWrapper
      isOpen={true}
      onClose={onClose}
      title={`Preview — ${file.name}`}
      icon="👁"
      subtitle={`Prime ${data.rows?.length || 0} righe · ${data.total_rows?.toLocaleString()} totali`}
      size="xl"
      footer={<ModalButton variant="primary" onClick={onClose}>Chiudi</ModalButton>}
    >
      {data.rows?.length > 0 && (
        <div className="fm__preview-table-wrap">
          <table className="fm__preview-table">
            <thead>
              <tr>{colNames.map(col => <th key={col}>{col}</th>)}</tr>
            </thead>
            <tbody>
              {data.rows.map((row, i) => (
                // key combinata (indice + hash leggero della prima cella)
                // per stabilita' rispetto a rimaneggiamenti di rows.
                <tr key={`row-${i}-${String(row[colNames[0]] ?? '').slice(0, 16)}`}>
                  {colNames.map(col => <td key={col}>{row[col] != null ? String(row[col]) : '—'}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </ModalWrapper>
  );
}

export default PreviewModal;
