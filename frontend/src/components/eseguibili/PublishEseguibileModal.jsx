/**
 * PublishEseguibileModal.jsx — Modal per pubblicare un progetto
 * come eseguibile on-demand.
 *
 * Accessibile solo agli admin. Il backend valida i permessi e il tipo progetto.
 */

import React, { useState } from 'react';
import { DEFAULT_OUTPUT_FORMAT, outputFormatOptions } from '../../constants/outputFormats';
import { createEseguibile } from '../../api';
import { useToast } from '../../hooks/useToast';
import ModalWrapper, { ModalButton } from '../common/ModalWrapper';
import { humanizeError } from '../../utils/errorMessages';
import { peekShared } from '../../utils/sharedCache';
import { eseguibiliOf } from '../../utils/sharedSelectors';
import { dashboardFeatureFromList, toastDashboardWarnings } from '../../utils/eseguibileDashboard';
import './PublishEseguibileModal.css';

// Preset di icone comuni — l'utente può sceglierne una o digitarne un'altra
const ICON_PRESETS = ['▶', '⚡', '🔍', '📊', '🗺️', '🏠', '🏢', '🌍', '📁', '🔧', '⚙️', '🛰️'];

function PublishEseguibileModal({ project, onClose, onSuccess }) {
  const toast = useToast();
  const [name, setName] = useState(project.name || '');
  const [description, setDescription] = useState('');
  const [outputFormat, setOutputFormat] = useState(DEFAULT_OUTPUT_FORMAT);
  const [outputName, setOutputName] = useState('');
  const [icon, setIcon] = useState('▶');
  // Dashboard Grafana (11/11/2026): la casella c'e' se la funzione e'
  // attiva, letto dalla lista degli Eseguibili gia' in cache (la carica la
  // pagina che apre questa finestra: nessuna richiesta qui). Lista vuota o
  // non in cache = non si sa: si mostra e un server spento risponde
  // `grafana_disabled`, col suo testo.
  const [featureOn] = useState(() => dashboardFeatureFromList(eseguibiliOf(peekShared('eseguibili')?.data)));
  const [createDashboard, setCreateDashboard] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);

  const handleSubmit = async () => {
    if (!name.trim()) {
      setError('Il nome è obbligatorio');
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const result = await createEseguibile({
        source_project_id: project.id,
        name: name.trim(),
        description: description.trim(),
        default_output_format: outputFormat,
        default_output_name: outputName.trim() || null,
        icon: icon.trim() || null,
        create_dashboard: featureOn !== false && createDashboard,
      });
      toast.success(`Eseguibile "${result.name}" pubblicato`);
      // Dashboard non creata (Grafana non risponde) o colonne da controllare.
      toastDashboardWarnings(toast, result?.dashboard_warnings);
      onSuccess && onSuccess(result);
      onClose();
    } catch (e) {
      setError(humanizeError(e).message);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <ModalWrapper
      isOpen={true}
      onClose={onClose}
      title="Pubblica come Eseguibile"
      icon="📢"
      size="md"
      busy={submitting}
      footer={
        <>
          <ModalButton variant="secondary" onClick={onClose} disabled={submitting}>Annulla</ModalButton>
          <ModalButton variant="primary" onClick={handleSubmit} loading={submitting} disabled={!name.trim()}>
            📢 Pubblica
          </ModalButton>
        </>
      }
    >
        <div className="pem-body">
          <p className="pem-info">
            Il progetto <strong>{project.name}</strong> verrà pubblicato come eseguibile on-demand.
            Il form di esecuzione verrà generato dagli Input progetto presenti nel canvas.
            Se non ci sono input, il progetto userà la configurazione pubblicata.
          </p>

          {error && <div className="pem-error">❌ {error}</div>}

          <div className="pem-field">
            <label>Nome pubblico *</label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Es. OMI Roma - Estrazione valori"
              maxLength={100}
            />
          </div>

          <div className="pem-field">
            <label>Descrizione</label>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Descrivi brevemente cosa fa questo progetto..."
              rows={3}
              maxLength={500}
            />
          </div>

          <div className="pem-row">
            <div className="pem-field">
              <label>Formato predefinito del risultato</label>
              <select value={outputFormat} onChange={(e) => setOutputFormat(e.target.value)}>
                {/* Catalogo unico dei formati (constants/outputFormats, R8.11). */}
                {outputFormatOptions(outputFormat).map(f => (
                  <option key={f.value} value={f.value} disabled={f.disabled}>{f.label}</option>
                ))}
              </select>
            </div>
            <div className="pem-field">
              <label>Nome file di default</label>
              <input
                type="text"
                value={outputName}
                onChange={(e) => setOutputName(e.target.value)}
                placeholder="risultato (senza estensione)"
              />
            </div>
          </div>

          <div className="pem-field">
            <label>Icona</label>
            <div className="pem-icon-picker">
              {ICON_PRESETS.map((emo) => (
                <button
                  type="button"
                  key={emo}
                  className={`pem-icon-preset ${icon === emo ? 'pem-icon-preset--active' : ''}`}
                  onClick={() => setIcon(emo)}
                  title={`Usa ${emo}`}
                >
                  {emo}
                </button>
              ))}
              <input
                type="text"
                className="pem-icon-custom"
                value={icon}
                onChange={(e) => setIcon(e.target.value)}
                maxLength={4}
                placeholder="▶"
                title="Oppure digita un carattere/emoji personalizzato"
              />
            </div>
          </div>

          {featureOn !== false && (
            <label className="pem-check">
              <input
                type="checkbox"
                checked={createDashboard}
                onChange={(e) => setCreateDashboard(e.target.checked)}
              />
              <span>
                <strong>📊 Crea anche una dashboard</strong>
                <span className="pem-check__hint">
                  Una dashboard Grafana generata dalle colonne del risultato: chi esegue la apre dalla finestra dei risultati, chi gestisce l&apos;Eseguibile la modifica in Grafana.
                </span>
              </span>
            </label>
          )}
        </div>
    </ModalWrapper>
  );
}

export default PublishEseguibileModal;
