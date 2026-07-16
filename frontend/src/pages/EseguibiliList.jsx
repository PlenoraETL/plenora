/**
 * EseguibiliList.jsx — Pagina lista eseguibili (catene on-demand).
 *
 * - Mostra gli eseguibili del gruppo utente
 * - Admin può eliminare
 * - Tutti possono eseguire (apre il wizard)
 */

import React, { useState, useEffect, useCallback, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { listEseguibili, deleteEseguibile, listProjects } from '../api';
import { useAuth } from '../context/AuthContext';
import EseguibileExecuteWizard from '../components/eseguibili/EseguibileExecuteWizard';
import PublishEseguibileModal from '../components/eseguibili/PublishEseguibileModal';
import EseguibileVersionsModal from '../components/eseguibili/EseguibileVersionsModal';
import AllowedGroupsSelector from '../components/shared/AllowedGroupsSelector';
import AppSidebar from '../components/AppSidebar';
import { useToast } from '../hooks/useToast';
import { role } from '../utils/permissions';
import ConfirmDialog from '../components/common/ConfirmDialog';
import { useConfirmModal } from '../hooks/useConfirmModal';
import useDialogFocus from '../hooks/useDialogFocus';
import { toastApiError } from '../utils/toastApiError';
import './EseguibiliList.css';

function EseguibiliList() {
  const navigate = useNavigate();
  const toast = useToast();
  const confirmModal = useConfirmModal();
  const { user } = useAuth();
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [selectedId, setSelectedId] = useState(null); // id dell'eseguibile in esecuzione
  const [sharingEseguibile, setSharingEseguibile] = useState(null); // modal condivisione cross-gruppo
  const [versioningEseguibile, setVersioningEseguibile] = useState(null);

  // Selettore progetto sorgente per creazione nuovo eseguibile
  const [showSelector, setShowSelector] = useState(false);
  const [projects, setProjects] = useState([]);
  const [loadingProjects, setLoadingProjects] = useState(false);
  const [projectSearch, setProjectSearch] = useState('');
  const [selectedSourceProject, setSelectedSourceProject] = useState(null);

  const isAdmin      = role.isAdminOrAbove(user);
  const isSuperadmin = role.isSuperadmin(user);

  // Focus management per i 2 dialog inline (a11y closure batch).
  const selectorDialogRef = useRef(null);
  const shareDialogRef    = useRef(null);
  useDialogFocus(showSelector,        () => setShowSelector(false),       selectorDialogRef);
  useDialogFocus(!!sharingEseguibile, () => setSharingEseguibile(null),   shareDialogRef);

  const loadItems = useCallback(async () => {
    setLoading(true);
    try {
      const data = await listEseguibili();
      setItems(data);
    } catch (e) {
      toastApiError(toast, e, 'Errore caricamento eseguibili');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadItems(); }, [loadItems]);

  const openSelector = async () => {
    setShowSelector(true);
    setLoadingProjects(true);
    setProjectSearch('');
    try {
      const all = await listProjects();
      const projects = all?.projects || [];
      setProjects(projects.filter(p => (
        ['estrai', 'manipola', 'arricchisci'].includes(p.project_type)
      )));
    } catch (e) {
      toastApiError(toast, e, 'Errore caricamento progetti');
    } finally {
      setLoadingProjects(false);
    }
  };

  const handleDelete = async (eid, name) => {
    const ok = await confirmModal.confirm({
      title: 'Elimina eseguibile',
      message: `Eliminare l'eseguibile "${name}"?`,
      confirmText: 'Elimina',
      variant: 'danger',
    });
    if (!ok) return;
    try {
      await deleteEseguibile(eid);
      toast.success('Eseguibile eliminato');
      loadItems();
    } catch (e) {
      toastApiError(toast, e, 'Errore eliminazione');
    }
  };

  return (
    <div className="esl esl--row">
      <AppSidebar counts={{ eseguibili: items.length }} />
      <div className="esl__main">
      <div className="esl__header">
        <div>
          <button type="button" className="esl__back" onClick={() => navigate('/')}>← Dashboard</button>
          <h1 className="esl__title">🚀 Eseguibili</h1>
          <p className="esl__subtitle">
            Progetti Estrai, Manipola e Arricchisci eseguibili on-demand
          </p>
        </div>
        {isAdmin && (
          <button type="button" className="esl__btn esl__btn--primary esl__btn--new" onClick={openSelector}>
            ➕ Nuovo Eseguibile
          </button>
        )}
      </div>

      {loading ? (
        <div className="esl__loading">Caricamento...</div>
      ) : items.length === 0 ? (
        <div className="esl__empty">
          <div className="esl__empty-icon">📭</div>
          <h3>Nessun eseguibile disponibile</h3>
          <p>
            {isAdmin
              ? 'Seleziona un progetto Estrai, Manipola o Arricchisci e pubblicalo come eseguibile.'
              : 'Chiedi a un amministratore del gruppo di pubblicare una catena come eseguibile.'}
          </p>
        </div>
      ) : (
        <div className="esl__grid">
          {items.map(item => {
            const canEdit = item.can_edit !== false;
            return (
              <div key={item.id} className="esl__card">
                <div className="esl__card-header">
                  <h3 className="esl__card-title">
                    {item.name}
                    {!canEdit && (
                      <span
                        title="Solo un superadmin può modificare questa risorsa"
                        className="esl__lock-icon"
                      >
                        🔒
                      </span>
                    )}
                  </h3>
                  <span className="esl__card-format">{item.default_output_format?.toUpperCase() || 'XLSX'}</span>
                </div>
                {item.description && (
                  <p className="esl__card-desc">{item.description}</p>
                )}
                <div className="esl__card-meta">
                  <span>{item.project_type?.toUpperCase()} - v{item.active_version} - {item.inputs_count} input</span>
                </div>
                <div className="esl__card-actions">
                  {isSuperadmin && canEdit && (
                    <button type="button" className="esl__btn esl__btn--ghost"
                      onClick={() => setSharingEseguibile(item)}
                      title="Gestisci condivisione cross-gruppo (solo superadmin)"
                    >🔗 Condividi</button>
                  )}
                  {isAdmin && canEdit && (
                    <button type="button" className="esl__btn esl__btn--ghost"
                      onClick={() => setVersioningEseguibile(item)}
                    >Versioni</button>
                  )}
                  {isAdmin && canEdit && (
                    <button type="button" className="esl__btn esl__btn--ghost"
                      onClick={() => handleDelete(item.id, item.name)}
                    >🗑 Elimina</button>
                  )}
                  <button type="button" className="esl__btn esl__btn--primary"
                    onClick={() => setSelectedId(item.id)}
                  >▶ Esegui</button>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {selectedId && (
        <EseguibileExecuteWizard
          eseguibileId={selectedId}
          onClose={() => setSelectedId(null)}
        />
      )}

      {/* Selettore progetto sorgente */}
      {showSelector && (
        <div className="esl__overlay" onClick={(e) => e.target === e.currentTarget && setShowSelector(false)}>
          <div
            ref={selectorDialogRef}
            className="esl__selector"
            role="dialog"
            aria-modal="true"
            aria-labelledby="esl-selector-title"
            tabIndex={-1}
          >
            <div className="esl__selector-header">
              <h2 id="esl-selector-title">Seleziona il progetto da pubblicare</h2>
              <button type="button" className="esl__selector-close" onClick={() => setShowSelector(false)} aria-label="Chiudi">×</button>
            </div>
            <div className="esl__selector-body">
              <input
                type="text"
                className="esl__selector-search"
                placeholder="Cerca progetto..."
                value={projectSearch}
                onChange={(e) => setProjectSearch(e.target.value)}
                autoFocus
              />
              {loadingProjects ? (
                <div className="esl__selector-loading">Caricamento progetti...</div>
              ) : projects.length === 0 ? (
                <div className="esl__selector-empty">
                  <p>Nessun progetto pubblicabile disponibile.</p>
                  <p className="esl__selector-hint">
                    Crea prima un progetto Estrai, Manipola o Arricchisci.
                  </p>
                </div>
              ) : (
                <div className="esl__selector-list">
                  {projects
                    .filter(p => !projectSearch || (p.name || '').toLowerCase().includes(projectSearch.toLowerCase()))
                    .map(p => (
                      <div key={p.id} className="esl__selector-item" onClick={() => {
                        setSelectedSourceProject(p);
                        setShowSelector(false);
                      }}>
                        <div className="esl__selector-item-icon">◈</div>
                        <div className="esl__selector-item-info">
                          <div className="esl__selector-item-name">{p.name}</div>
                          {p.description && <div className="esl__selector-item-desc">{p.description}</div>}
                        </div>
                        <div className="esl__selector-item-arrow">→</div>
                      </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Modal condivisione cross-gruppo */}
      {sharingEseguibile && (
        <div className="esl__overlay" onClick={(e) => e.target === e.currentTarget && setSharingEseguibile(null)}>
          <div
            ref={shareDialogRef}
            className="esl__selector esl__sharing-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="esl-share-title"
            tabIndex={-1}
          >
            <div className="esl__selector-header">
              <h2 id="esl-share-title">Condivisione — {sharingEseguibile.name}</h2>
              <button type="button" className="esl__selector-close" onClick={() => setSharingEseguibile(null)} aria-label="Chiudi">×</button>
            </div>
            <div className="esl__selector-body">
              <AllowedGroupsSelector
                embedded
                resourceType="eseguibili"
                resourceId={sharingEseguibile.id}
                disabled={sharingEseguibile.can_edit === false}
                onClose={() => setSharingEseguibile(null)}
              />
            </div>
          </div>
        </div>
      )}

      {/* Modal pubblicazione dopo selezione progetto */}
      {selectedSourceProject && (
        <PublishEseguibileModal
          project={selectedSourceProject}
          onClose={() => setSelectedSourceProject(null)}
          onSuccess={() => {
            setSelectedSourceProject(null);
            loadItems();
          }}
        />
      )}
      {versioningEseguibile && (
        <EseguibileVersionsModal
          eseguibile={versioningEseguibile}
          onClose={() => setVersioningEseguibile(null)}
          onUpdated={() => loadItems()}
        />
      )}
      <ConfirmDialog {...confirmModal} />
      </div>
    </div>
  );
}

export default EseguibiliList;
