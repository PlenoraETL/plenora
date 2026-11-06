/**
 * PreviewPanel.jsx — Tab "Preview" del ResponseConfigBuilder
 * Componente presentational: niente fetch, niente API, niente
 * useState. Riceve `previewData`, `previewError`,
 * `generatedConfig`, `generatePreview`.
 *
 * Render:
 *  - bottone "Aggiorna preview" (chiama callback parent);
 *  - banner errore se `previewError`;
 *  - tabella preview con prime 10 righe (limite documentato in
 *    `responseConfigEngine.extractRowsFromConfig`);
 *  - blocco JSON della configurazione generata.
 */
import React from 'react';

export default function PreviewPanel({
  previewData,
  previewError,
  generatedConfig,
  generatePreview,
}) {
  return (
    <div className="response-config-builder__panel">
      <div className="response-config-builder__section-header">
        <h4>👁️ Anteprima del risultato</h4>
        <button type="button"
          onClick={generatePreview}
          className="response-config-builder__btn response-config-builder__btn--primary"
        >
          🔄 Aggiorna l'anteprima
        </button>
      </div>

      {previewError && (
        <div className="response-config-builder__error">
          ❌ {previewError}
        </div>
      )}

      {previewData && previewData.length > 0 ? (
        <div className="response-config-builder__preview-table-wrapper">
          <table className="response-config-builder__preview-table">
            <thead>
              <tr>
                {Object.keys(previewData[0]).map(col => (
                  <th key={col}>{col}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {previewData.map((row, idx) => (
                <tr key={idx}>
                  {Object.values(row).map((val, vIdx) => (
                    <td key={vIdx}>
                      {val === null ? <span className="response-config-builder__null">null</span> : String(val).substring(0, 50)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
          {previewData.length >= 10 && (
            <p className="response-config-builder__preview-note">
              Mostrando le prime 10 righe...
            </p>
          )}
        </div>
      ) : (
        <div className="response-config-builder__empty-section">
          <p>Nessun dato da mostrare. Scegli i campi da estrarre e premi «Aggiorna l'anteprima».</p>
        </div>
      )}

      {/* Config JSON */}
      <div className="response-config-builder__config-preview">
        <h5>📝 Configurazione generata:</h5>
        <pre className="response-config-builder__config-json">
          {JSON.stringify(generatedConfig, null, 2)}
        </pre>
      </div>
    </div>
  );
}
