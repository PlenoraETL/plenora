/**
 * EseguibiliList.jsx — Pagina lista eseguibili (catene on-demand).
 *
 * - Mostra gli eseguibili del gruppo utente
 * - Admin può eliminare
 * - Tutti possono eseguire (apre il wizard)
 */

import React, { useState } from 'react';
import { useBusy } from '../hooks/useBusy';
import ListState, { ResourceError } from '../components/common/ListState';
import {
  updateEseguibile,
  deleteEseguibile,
} from '../api';
import { useSharedResource } from '../hooks/useSharedResource';
import { eseguibiliOf } from '../utils/sharedSelectors';
import { useAuth } from '../context/AuthContext';
import EseguibileExecuteWizard from '../components/eseguibili/EseguibileExecuteWizard';
import PublishEseguibileModal from '../components/eseguibili/PublishEseguibileModal';
import EseguibileVersionsModal from '../components/eseguibili/EseguibileVersionsModal';
import AllowedGroupsSelector from '../components/shared/AllowedGroupsSelector';
import PageLayout from '../components/common/PageLayout';
import EmptyState from '../components/common/EmptyState';
import ActionMenu from '../components/common/ActionMenu';
import BindingWarnings from '../components/eseguibili/BindingWarnings';
import { useToast } from '../hooks/useToast';
import { can } from '../utils/permissions';
import ConfirmDialog from '../components/common/ConfirmDialog';
import { useConfirmModal } from '../hooks/useConfirmModal';
import useDialog from '../hooks/useDialog';
import { toastApiError } from '../utils/toastApiError';
import './EseguibiliList.css';
import { pressable } from '../utils/pressable';
import SearchField, { SearchEmpty } from '../components/common/SearchField';
import { useSearch } from '../hooks/useSearch';
import { projectTypeChip } from '../utils/projectTypes';

const PUBLISHABLE_TYPES = ['estrai', 'manipola', 'arricchisci'];
const publishableProjects = (all) => (all?.projects || []).filter(p => PUBLISHABLE_TYPES.includes(p.project_type));

function EseguibiliList() {
  const toast = useToast();
  const confirmModal = useConfirmModal();
  const { user } = useAuth();
  // Fase 5 T2 (R4.8, R9.11): elenco dall'hook comune (errore con
  // «Riprova»); rinomina per eseguibile (useBusy): la fine del salvataggio
  // di A non chiude la rinomina di B.
  // UI-1: risorse condivise (utils/sharedResources): eseguibili e progetti
  // pubblicabili dalla stessa risposta della dashboard (C9).
  const resource = useSharedResource('eseguibili', { select: eseguibiliOf });
  const items = resource.data || [];
  const setItems = resource.setData;
  const loadItems = () => resource.reload();
  const renameBusy = useBusy();
  const [selectedId, setSelectedId] = useState(null); // id dell'eseguibile in esecuzione
  const [sharingEseguibile, setSharingEseguibile] = useState(null); // modal condivisione cross-gruppo
  const [versioningEseguibile, setVersioningEseguibile] = useState(null);
  const [renaming, setRenaming] = useState(null); // { id, value }
  const renamingId = renaming?.id ?? null;
  const renameValue = renaming?.value ?? '';
  const setRenameValue = (value) => setRenaming(cur => (cur ? { ...cur, value } : cur));

  // Selettore progetto sorgente per creazione nuovo eseguibile
  const [showSelector, setShowSelector] = useState(false);
  const projectsResource = useSharedResource('projects', { select: publishableProjects, enabled: showSelector });
  const projects = projectsResource.data || [];
  const loadingProjects = projectsResource.initialLoading;
  const projectSearch = useSearch();
  const shownProjects = projectSearch.filter(projects, p => [p.name, p.description]);
  const [selectedSourceProject, setSelectedSourceProject] = useState(null);

  const canCreate    = can.createEseguibile(user);

  // I 2 dialog inline passano dallo stack unico delle finestre (hooks/useDialog).
  const selectorDialog = useDialog({ isOpen: showSelector,        onClose: () => setShowSelector(false) });
  const shareDialog    = useDialog({ isOpen: !!sharingEseguibile, onClose: () => setSharingEseguibile(null) });

  const openSelector = () => {
    projectSearch.clear();
    setShowSelector(true);
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

  const startRename = (item) => {
    if (renameBusy.isBusy(item.id)) return;
    setRenaming({ id: item.id, value: item.name || '' });
  };

  const cancelRename = () => {
    if (renamingId && renameBusy.isBusy(renamingId)) return;
    setRenaming(null);
  };
  // Chiude la rinomina di QUESTO eseguibile, non quella aperta dopo su un altro.
  const closeRenameOf = (id) => setRenaming(cur => (cur?.id === id ? null : cur));

  const commitRename = async (item) => {
    const name = renameValue.trim();
    if (!name) {
      toast.error('Il nome non può essere vuoto');
      return;
    }
    if (name === item.name) {
      closeRenameOf(item.id);
      return;
    }
    try {
      const r = await renameBusy.run(item.id, () => updateEseguibile(item.id, { name }));
      if (r.skipped) return;
      setItems(current => (current || []).map(existing => (
        existing.id === item.id ? { ...existing, ...r.value, name } : existing
      )));
      closeRenameOf(item.id);
      toast.success('Eseguibile rinominato');
    } catch (e) {
      toastApiError(toast, e, 'Errore rinomina');
    }
  };

  return (
    <PageLayout
      className="esl"
      title="Eseguibili"
      subtitle="Progetti Estrai, Manipola e Arricchisci eseguibili on-demand"
      actions={canCreate && (
        <button type="button" className="esl__btn esl__btn--primary esl__btn--new" onClick={openSelector}>
          ＋ Nuovo Eseguibile
        </button>
      )}
    >

      <ListState
        resource={resource}
        loading={<div className="esl__loading">Caricamento...</div>}
        errorTitle="Eseguibili non caricati"
        empty={(
          <EmptyState
            icon="📭"
            title="Nessun eseguibile disponibile"
            text={canCreate
              ? 'Seleziona un progetto Estrai, Manipola o Arricchisci e pubblicalo come eseguibile.'
              : 'Chiedi a un amministratore del gruppo di pubblicare un progetto come eseguibile.'}
            action={canCreate && (
              <button type="button" className="esl__btn esl__btn--primary" onClick={openSelector}>＋ Nuovo Eseguibile</button>
            )}
          />
        )}
      >
        <div className="esl__grid">
          {items.map(item => {
            const canEdit = can.manageEseguibile(user, item);
            const canShare = can.shareResources(user) && item.can_edit !== false;
            return (
              <div key={item.id} className="esl__card">
                <div className="esl__card-header">
                  {renamingId === item.id ? (
                    <form
                      className="esl__rename-form"
                      onSubmit={(event) => {
                        event.preventDefault();
                        commitRename(item);
                      }}
                    >
                      <input
                        className="esl__rename-input"
                        value={renameValue}
                        onChange={(event) => setRenameValue(event.target.value)}
                        onKeyDown={(event) => {
                          if (event.key === 'Escape') cancelRename();
                        }}
                        maxLength={255}
                        aria-label={`Nuovo nome per ${item.name}`}
                        autoFocus
                        disabled={renameBusy.isBusy(item.id)}
                      />
                      <button type="submit" className="esl__rename-action" disabled={renameBusy.isBusy(item.id)} aria-label="Salva nome">✓</button>
                      <button type="button" className="esl__rename-action" onClick={cancelRename} disabled={renameBusy.isBusy(item.id)} aria-label="Annulla rinomina">×</button>
                    </form>
                  ) : (
                    <h3 className="esl__card-title">
                      {item.name}
                      {item.can_edit === false && (
                        <span
                          title="Solo un superadmin può modificare questa risorsa"
                          className="esl__lock-icon"
                        >
                          🔒
                        </span>
                      )}
                    </h3>
                  )}
                  <span className="esl__card-format">{item.default_output_format?.toUpperCase() || 'XLSX'}</span>
                </div>
                {item.description && (
                  <p className="esl__card-desc">{item.description}</p>
                )}
                {canEdit && <BindingWarnings warnings={item.binding_warnings} />}
                <div className="esl__card-meta">
                  <span>{projectTypeChip(item.project_type)} · v{item.active_version} · {item.inputs_count} input</span>
                </div>
                <div className="esl__card-actions">
                  <ActionMenu
                    label={`Azioni per ${item.name}`}
                    items={[
                      canShare && { label: '🔗 Condividi', onSelect: () => setSharingEseguibile(item), title: 'Gestisci condivisione cross-gruppo (solo superadmin)' },
                      canEdit && { label: '✎ Rinomina', onSelect: () => startRename(item) },
                      canEdit && { label: 'Versioni', onSelect: () => setVersioningEseguibile(item) },
                      canEdit && { label: '🗑 Elimina', onSelect: () => handleDelete(item.id, item.name), danger: true },
                    ]}
                  />
                  <button type="button" className="esl__btn esl__btn--primary"
                    onClick={() => setSelectedId(item.id)}
                  >▶ Esegui</button>
                </div>
              </div>
            );
          })}
        </div>
      </ListState>

      {selectedId && (
        <EseguibileExecuteWizard
          eseguibileId={selectedId}
          onClose={() => setSelectedId(null)}
        />
      )}

      {/* Selettore progetto sorgente */}
      {showSelector && (
        <div className="esl__overlay" {...selectorDialog.overlayProps}>
          <div
            ref={selectorDialog.ref}
            className="esl__selector"
            {...selectorDialog.dialogProps}
          >
            <div className="esl__selector-header">
              <h2 id={selectorDialog.titleId}>Seleziona il progetto da pubblicare</h2>
              <button type="button" className="esl__selector-close" {...selectorDialog.closeButtonProps}>×</button>
            </div>
            <div className="esl__selector-body">
              <SearchField
                search={projectSearch}
                label="Cerca progetto"
                className="esl__selector-search"
                shown={shownProjects.length}
                total={projects.length}
                autoFocus
              />
              {loadingProjects ? (
                <div className="esl__selector-loading">Caricamento progetti...</div>
              ) : projectsResource.status === 'error' ? (
                <ResourceError resource={projectsResource} title="Progetti non caricati" />
              ) : projects.length === 0 ? (
                <div className="esl__selector-empty">
                  <p>Nessun progetto pubblicabile disponibile.</p>
                  <p className="esl__selector-hint">
                    Crea prima un progetto Estrai, Manipola o Arricchisci.
                  </p>
                </div>
              ) : (
                <div className="esl__selector-list">
                  <SearchEmpty search={projectSearch} shown={shownProjects.length} />
                  {shownProjects
                    .map(p => (
                      <div key={p.id} className="esl__selector-item" {...pressable(() => {
                        setSelectedSourceProject(p);
                        setShowSelector(false);
                      })}>
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
        <div className="esl__overlay" {...shareDialog.overlayProps}>
          <div
            ref={shareDialog.ref}
            className="esl__selector esl__sharing-modal"
            {...shareDialog.dialogProps}
          >
            <div className="esl__selector-header">
              <h2 id={shareDialog.titleId}>Condivisione — {sharingEseguibile.name}</h2>
              <button type="button" className="esl__selector-close" {...shareDialog.closeButtonProps}>×</button>
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
    </PageLayout>
  );
}

export default EseguibiliList;
