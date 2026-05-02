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
import AllowedGroupsSelector from '../components/shared/AllowedGroupsSelector';
import AppSidebar from '../components/AppSidebar';
import { useToast } from '../hooks/useToast';
import { role } from '../utils/permissions';
import ConfirmDialog from '../components/common/ConfirmDialog';
import { useConfirmModal } from '../hooks/useConfirmModal';
import useDialogFocus from '../hooks/useDialogFocus';
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

  // Selettore progetto sorgente per creazione nuovo eseguibile
  const [showSelector, setShowSelector] = useState(false);
  const [arricchisciProjects, setArricchisciProjects] = useState([]);
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
      toast.error('Errore caricamento eseguibili: ' + (e?.response?.data?.detail || e.message));
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
      setArricchisciProjects(all.filter(p => p.project_type === 'arricchisci'));
    } catch (e) {
      toast.error('Errore caricamento progetti: ' + (e?.response?.data?.detail || e.message));
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
      toast.error('Errore eliminazione: ' + (e?.response?.data?.detail || e.message));
    }
  };

  return (
    <div className="esl" style={{ display: 'flex' }}>
      <AppSidebar counts={{ eseguibili: items.length }} />
      <div style={{ flex: 1, minWidth: 0 }}>
      <div className="esl__header">
        <div>
          <button type="button" className="esl__back" onClick={() => navigate('/')}>← Dashboard</button>
          <h1 className="esl__title">🚀 Eseguibili</h1>
          <p className="esl__subtitle">
            Catene di arricchimento eseguibili on-demand — carica un file e ottieni il risultato
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
              ? 'Vai nella Dashboard, seleziona un progetto Arricchisci e clicca "📢 Pubblica come Eseguibile" sulla card.'
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
                        style={{ marginLeft: 6, opacity: 0.7, cursor: 'help', fontSize: '0.85em' }}
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
                  <span>📊 {item.expected_columns_count} colonne attese</span>
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
              <h2 id="esl-selector-title">Seleziona il progetto Arricchisci da pubblicare</h2>
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
              ) : arricchisciProjects.length === 0 ? (
                <div className="esl__selector-empty">
                  <p>Nessun progetto Arricchisci disponibile.</p>
                  <p className="esl__selector-hint">
                    Crea prima un progetto Arricchisci dalla Dashboard, poi torna qui per pubblicarlo.
                  </p>
                </div>
              ) : (
                <div className="esl__selector-list">
                  {arricchisciProjects
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
            className="esl__selector"
            style={{ maxWidth: 560 }}
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
      <ConfirmDialog {...confirmModal} />
      </div>
    </div>
  );
}

export default EseguibiliList;
