/**
 * PublishEseguibileModal.jsx — Modal per pubblicare un progetto arricchisci
 * come eseguibile on-demand.
 *
 * Accessibile solo agli admin. Il backend valida i permessi e il tipo progetto.
 */

import React, { useState } from 'react';
import { createEseguibile } from '../../api';
import { useToast } from '../../hooks/useToast';
import ModalWrapper, { ModalButton } from '../common/ModalWrapper';
import { humanizeError } from '../../utils/errorMessages';
import './PublishEseguibileModal.css';

// Preset di icone comuni — l'utente può sceglierne una o digitarne un'altra
const ICON_PRESETS = ['▶', '⚡', '🔍', '📊', '🗺️', '🏠', '🏢', '🌍', '📁', '🔧', '⚙️', '🛰️'];

function PublishEseguibileModal({ project, onClose, onSuccess }) {
  const toast = useToast();
  const [name, setName] = useState(project.name || '');
  const [description, setDescription] = useState('');
  const [outputFormat, setOutputFormat] = useState('xlsx');
  const [outputName, setOutputName] = useState('');
  const [icon, setIcon] = useState('▶');
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
      });
      toast.success(`Eseguibile "${result.name}" pubblicato`);
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
      closeOnOverlay={!submitting}
      closeOnEsc={!submitting}
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
            Gli utenti del gruppo potranno eseguirlo caricando un file con le colonne richieste.
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
              placeholder="Descrivi brevemente cosa fa questa catena..."
              rows={3}
              maxLength={500}
            />
          </div>

          <div className="pem-row">
            <div className="pem-field">
              <label>Formato output di default</label>
              <select value={outputFormat} onChange={(e) => setOutputFormat(e.target.value)}>
                <option value="xlsx">Excel (.xlsx)</option>
                <option value="csv">CSV (.csv)</option>
                <option value="json">JSON (.json)</option>
                <option value="geojson">GeoJSON (.geojson)</option>
                <option value="zip">ZIP (.zip)</option>
                <option value="pdf">PDF (.pdf)</option>
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
        </div>
    </ModalWrapper>
  );
}

export default PublishEseguibileModal;
