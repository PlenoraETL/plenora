/**
 * DataSourceManager.jsx — Gestione Data Sources
 * Redesign Plenora design system
 * Route: /datasources
 */
import React, { useState } from 'react';
import { useToast } from '../hooks/useToast';
import { useAuth } from '../context/AuthContext';
import {
  listDataSources, createDataSource, updateDataSource,
  deleteDataSource, testDataSource, listFiles,
} from '../api/datasources';
import { useHexCanvas } from '../utils/hexCanvas';
import AllowedGroupsSelector from '../components/shared/AllowedGroupsSelector';
import PageLayout from '../components/common/PageLayout';
import EmptyState from '../components/common/EmptyState';
import { handleCascade409, formatCascadeDependencies } from '../api/errors';
import { toastApiError } from '../utils/toastApiError';
import { humanizeError, requestErrorMessage } from '../utils/errorMessages';
import useDialog from '../hooks/useDialog';
import { can } from '../utils/permissions';
import { useResource } from '../hooks/useResource';
import { useBusy } from '../hooks/useBusy';
import '../styles/DataSourceManager.css';
import SchemaFields from '../components/common/SchemaFields';
import { formErrors, formPayload, initialValues } from '../utils/formSchema';
import { DS_FIELDS, DS_STATE_FIELDS, DEFAULT_PORTS } from '../utils/dataSourceForm';
import { formatBytes as formatSize, formatDateTime as formatDate } from '../utils/format';
import { pressable } from '../utils/pressable';
import ConnectionTestStatus from '../components/connections/ConnectionTestStatus';
import { lastTestFromResult, withLastTest } from '../utils/connectionTestStatus';
import SearchField, { SearchEmpty } from '../components/common/SearchField';
import { useSearch } from '../hooks/useSearch';
const DS_TYPES = [
  { value:'local',      label:'Cartella locale',   icon:'📂', color:'#94A3B8', rgb:'148,163,184', desc:'Disco locale o percorso UNC' },
  { value:'ftp',        label:'FTP',               icon:'📁', color:'#60A5FA', rgb:'96,165,250',  desc:'File Transfer Protocol (FTPS)' },
  { value:'sftp',       label:'SFTP',              icon:'🔐', color:'#34D399', rgb:'52,211,153',  desc:'SSH File Transfer Protocol' },
  { value:'sharepoint', label:'SharePoint Online', icon:'☁️', color:'#4B8BFF', rgb:'75,139,255',  desc:'Microsoft Graph API (OAuth2)' },
];
const TYPE_INFO = Object.fromEntries(DS_TYPES.map(t => [t.value, t]));
// Stato del modulo: il testo dei campi dello schema unico (utils/dataSourceForm).
const emptyForm = (type = 'local') => ({ type, ...initialValues(DS_STATE_FIELDS) });
export default function DataSourceManager() {
  const toast    = useToast();
  const { user } = useAuth();
  const canvasRef = React.useRef(null);
  useHexCanvas(canvasRef);

  // Fase 5 T2 (R4.5, R4.7, R9.10): elenco e browser dall'hook comune (id
  // di richiesta: la cartella mostrata e' sempre l'ultima chiesta), test ed
  // eliminazioni per Data Source (useBusy).
  const resource = useResource(async () => (await listDataSources()).datasources || [], { key: 'datasources', errorMessage: (e) => humanizeError(e).message });
  const allDatasources = resource.data || [];
  const search = useSearch();
  const datasources = search.filter(allDatasources, ds => [ds.name, ds.description, ds.host, ds.site_url, ds.root_path, TYPE_INFO[ds.type]?.label]);
  const loading = resource.initialLoading;
  const error = resource.error;
  const load = () => resource.reload();
  const busy = useBusy();
  const [showModal,     setShowModal]     = useState(false);
  const [editing,       setEditing]       = useState(null);
  const [formData,      setFormData]      = useState(emptyForm);
  const [fieldErrors,   setFieldErrors]   = useState({});
  const [saving,        setSaving]        = useState(false);
  const [formError,     setFormError]     = useState(null);
  const [browserDs,     setBrowserDs]     = useState(null);
  const [browserPath,   setBrowserPath]   = useState('');
  const [confirmDelete, setConfirmDelete] = useState(null);
  const [sharingDatasource, setSharingDatasource] = useState(null);  // ds in sharing modal (solo super)

  // I 4 dialog inline passano dallo stack unico delle finestre (hooks/useDialog):
  // Esc, focus, overlay, scroll e "invio in corso".
  const editDialog = useDialog({
    isOpen: showModal,
    onClose: () => { setShowModal(false); setEditing(null); setFormError(null); },
    busy: saving,
  });
  const browserDialog = useDialog({
    isOpen: !!browserDs,
    onClose: () => { setBrowserDs(null); setBrowserPath(''); },
  });
  const confirmDialog = useDialog({
    isOpen: !!confirmDelete,
    onClose: () => setConfirmDelete(null),
    busy: Boolean(confirmDelete && busy.isBusy(`delete:${confirmDelete.id}`)),
    closeOnOverlay: false,
  });
  const shareDialog = useDialog({ isOpen: !!sharingDatasource, onClose: () => setSharingDatasource(null) });

  const canShare       = can.shareResources(user);
  const canCreateDs    = can.createDataSource(user);

  const browser = useResource(
    () => listFiles(browserDs.id, { path: browserPath }),
    { key: { ds: browserDs?.id || null, path: browserPath }, enabled: Boolean(browserDs), errorMessage: requestErrorMessage },
  );
  const browserFiles = browser.data || [];
  const browserLoading = browser.loading;
  const browserError = browser.error;

  const openNew = () => { setEditing(null); setFormData(emptyForm()); setFieldErrors({}); setFormError(null); setShowModal(true); };
  const openEdit = ds => {
    setEditing(ds);
    // I segreti partono vuoti ("invariati"); gli altri dal Data Source.
    setFormData({ type: ds.type || 'local', ...initialValues(DS_STATE_FIELDS, ds) });
    setFieldErrors({}); setFormError(null); setShowModal(true);
  };
  const closeModal = () => { setShowModal(false); setEditing(null); setFormError(null); };
  // In modifica il tipo non cambia (il backend non lo aggiorna).
  const handleTypeChange = type => { if (editing) return; setFormData(prev => ({ ...prev, type, port: DEFAULT_PORTS[type] ? String(DEFAULT_PORTS[type]) : '' })); setFieldErrors({}); };
  const handleField = (f, v) => { setFormData(prev => ({ ...prev, [f]: v })); setFieldErrors(prev => (prev[f] ? { ...prev, [f]: null } : prev)); };
  const formCtx = { mode: editing ? 'edit' : 'create' };

  const handleSave = async () => {
    // Validazione e payload dallo schema: solo i campi del tipo scelto.
    const errs = formErrors(DS_FIELDS, formData, formCtx);
    setFieldErrors(errs);
    if (Object.keys(errs).length > 0) { setFormError('Correggi i campi segnati.'); return; }
    setSaving(true); setFormError(null);
    const payload = formPayload(DS_FIELDS, formData, formCtx);
    if (!editing) payload.type = formData.type;
    try {
      if (editing) await updateDataSource(editing.id, payload); else await createDataSource(payload);
      closeModal(); await load();
    } catch (e) { setFormError(humanizeError(e).message); }
    finally { setSaving(false); }
  };

  // S3: l'esito lo salva il backend e torna nella lista (`last_test`);
  // qui si mostra subito quello ricevuto e poi si ricarica la lista.
  const handleTest = async id => {
    if (busy.isBusy(`test:${id}`)) return;
    try {
      const r = await busy.run(`test:${id}`, () => testDataSource(id));
      if (r.skipped) return;
      resource.setData(list => withLastTest(list, id, lastTestFromResult(r.value, user?.username)));
      resource.reload();
    } catch (e) { toastApiError(toast, e, 'Test del Data Source non eseguito'); }
  };

  // Pre-fix (P1 frontend-pages): handleDelete async senza disabling
  // del bottone → utente puo' cliccare "Elimina" piu' volte mentre
  // l'API e' in corso, generando N DELETE concorrenti sullo stesso ID.
  // Post-fix: eliminazione per Data Source (useBusy): bottone spento durante l'await.
  const handleDelete = async id => {
    try {
      const r = await busy.run(`delete:${id}`, () => deleteDataSource(id));
      if (r.skipped) return;
      setConfirmDelete(null);
      await load();
    } catch (e) {
      const cascade = handleCascade409(e);
      if (cascade) {
        const dsName = confirmDelete?.name || id;
        toast.error(
          `Impossibile eliminare "${dsName}": ${formatCascadeDependencies(cascade)}. Rimuovili prima.`,
          10000
        );
        return;
      }
      toastApiError(toast, e, 'Errore');
    }
  };

  const openBrowser = ds => { setBrowserPath(''); setBrowserDs(ds); };
  // Stessa cartella = ricarica; altra cartella = nuova richiesta (vince l'ultima).
  const browseFiles = (dsId, path) => {
    if (path === browserPath) browser.reload();
    else setBrowserPath(path);
  };
  const closeBrowser = () => { setBrowserDs(null); setBrowserPath(''); };

  return (
    <PageLayout
      className="dsm"
      bare
      title="Data Source"
      actions={canCreateDs && (
        <button type="button" className="dsm__btn dsm__btn--primary" onClick={openNew}>＋ Nuovo Data Source</button>
      )}
    >

        <div className="dsm__content">
          <canvas ref={canvasRef} className="dsm__hex-bg" />

          {error && (
            <div className="dsm__error-banner" role="alert">⚠️ {error} <button type="button" onClick={load}>Riprova</button></div>
          )}

          {allDatasources.length > 0 && (
            <SearchField search={search} label="Cerca Data Source" className="dsm__search" shown={datasources.length} total={allDatasources.length} />
          )}
          {loading ? (
            <div className="dsm__loading"><div className="dsm__loading-icon">⬡</div>Caricamento Data Source...</div>
          ) : error && !resource.hasData ? null : datasources.length === 0 && search.active ? (
            <SearchEmpty search={search} shown={0} className="dsm__search-empty" />
          ) : datasources.length === 0 ? (
            <EmptyState
              icon="🗄️"
              title="Nessun Data Source configurato"
              text="Aggiungi un Data Source per collegare Plenora a SharePoint, FTP, SFTP o cartelle di rete."
              action={canCreateDs && (
                <button type="button" className="dsm__btn dsm__btn--primary" onClick={openNew}>＋ Nuovo Data Source</button>
              )}
            />
          ) : (
            <div className="dsm__grid">
              {datasources.map(ds => {
                const ti = TYPE_INFO[ds.type] || {};
                const isTesting  = busy.isBusy(`test:${ds.id}`);
                const canEdit    = can.editDataSource(user, ds);
                return (
                  <div key={ds.id} className="dsm__card">
                    <div className="dsm__card-bar" style={{ background: ti.color }} />
                    <div className="dsm__card-inner">
                      <div className="dsm__card-header">
                        <div className="dsm__card-type" style={{ color: ti.color, background: `rgba(${ti.rgb},.10)` }}>
                          {ti.icon} {ti.label}
                        </div>
                        <div className="dsm__card-actions">
                          <button type="button" className="dsm__action-btn" onClick={() => openBrowser(ds)} title="Sfoglia" aria-label="Sfoglia file del data source">📂</button>
                          {canEdit && (
                            <button type="button" className="dsm__action-btn" onClick={() => openEdit(ds)} title="Modifica" aria-label="Modifica data source">✏️</button>
                          )}
                          {canShare && (
                            <button type="button" className="dsm__action-btn" onClick={() => setSharingDatasource(ds)} title="Gestisci condivisione cross-gruppo" aria-label="Gestisci condivisione cross-gruppo">🔗</button>
                          )}
                          {canEdit && (
                            <button type="button" className="dsm__action-btn dsm__action-btn--danger" onClick={() => setConfirmDelete(ds)} title="Elimina" aria-label="Elimina data source">🗑</button>
                          )}
                          {ds.can_edit === false && (
                            <span
                              className="dsm__lock-badge dsm__field-help-icon"
                              title="Solo un superadmin può modificare questa risorsa"
                            >
                              🔒
                            </span>
                          )}
                        </div>
                      </div>
                      <div className="dsm__card-name">{ds.name}</div>
                      {ds.description && <div className="dsm__card-desc">{ds.description}</div>}
                      <div className="dsm__card-meta">
                        {ds.host       && <span className="dsm__meta-item">🖥 {ds.host}{ds.port ? `:${ds.port}` : ''}</span>}
                        {ds.site_url   && <span className="dsm__meta-item">🌐 {ds.site_url}</span>}
                        {ds.root_path  && <span className="dsm__meta-item">📁 {ds.root_path}</span>}
                        {ds.username   && <span className="dsm__meta-item">👤 {ds.username}</span>}
                      </div>
                      <div className="dsm__card-footer">
                        <button type="button"
                          className={`dsm__test-btn${isTesting?' dsm__test-btn--loading':''}`}
                          onClick={() => handleTest(ds.id)} disabled={isTesting}>
                          {isTesting ? '⏳ Test in corso...' : '🔌 Testa connessione'}
                        </button>
                        <ConnectionTestStatus lastTest={ds.last_test} />
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>

      {/* MODAL CREA/MODIFICA */}
      {showModal && (
        <div className="dsm__overlay" {...editDialog.overlayProps}>
          <div
            ref={editDialog.ref}
            className="dsm__modal"
            {...editDialog.dialogProps}
          >
            <div className="dsm__modal-header">
              <div className="dsm__modal-title" id={editDialog.titleId}>{editing ? `Modifica — ${editing.name}` : 'Nuovo Data Source'}</div>
              <button type="button" className="dsm__modal-close" {...editDialog.closeButtonProps}>✕</button>
            </div>
            <div className="dsm__modal-body">

              {/* Tipo */}
              <div className="dsm__field-group">
                <div className="dsm__label">Tipo *</div>
                <div className="dsm__type-grid">
                  {DS_TYPES.map(t => (
                    <button type="button" key={t.value}
                      className={`dsm__type-btn${formData.type===t.value?' dsm__type-btn--active':''}`}
                      style={formData.type===t.value?{borderColor:t.color,background:`rgba(${t.rgb},.08)`}:{}}
                      onClick={() => handleTypeChange(t.value)}
                      disabled={Boolean(editing) && formData.type !== t.value}
                      title={editing && formData.type !== t.value ? 'Il tipo non si cambia in modifica' : undefined}>
                      <span className="dsm__type-icon">{t.icon}</span>
                      <span className="dsm__type-label">{t.label}</span>
                      <span className="dsm__type-desc">{t.desc}</span>
                    </button>
                  ))}
                </div>
              </div>

              {formData.type === 'sharepoint' && (
                <div className="dsm__sp-info">💡 Richiede un'app Azure AD con permessi <strong>Sites.ReadWrite.All</strong> e <strong>Files.ReadWrite.All</strong>.</div>
              )}
              {/* Campi dallo schema unico (utils/dataSourceForm): quelli del
                  tipo scelto, gli stessi che si validano e si inviano. */}
              <SchemaFields
                fields={DS_FIELDS}
                values={formData}
                ctx={formCtx}
                errors={fieldErrors}
                onChange={handleField}
                classes={{
                  grid: 'dsm__schema-grid', field: 'dsm__field', label: 'dsm__label', input: 'dsm__input',
                  textarea: 'dsm__textarea', select: 'dsm__select', hint: 'dsm__hint', check: 'dsm__check-label',
                }}
              />

              {formError && <div className="dsm__form-error">❌ {formError}</div>}
            </div>
            <div className="dsm__modal-footer">
              <button type="button" className="dsm__btn dsm__btn--ghost" onClick={closeModal} disabled={saving}>Annulla</button>
              <button type="button" className="dsm__btn dsm__btn--primary" onClick={handleSave} disabled={saving}>
                {saving ? '⏳ Salvataggio...' : editing ? '💾 Aggiorna' : '✅ Crea Data Source'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* MODAL BROWSER */}
      {browserDs && (
        <div className="dsm__overlay" {...browserDialog.overlayProps}>
          <div
            ref={browserDialog.ref}
            className="dsm__modal dsm__modal--browser"
            {...browserDialog.dialogProps}
          >
            <div className="dsm__modal-header">
              <div className="dsm__modal-title" id={browserDialog.titleId}>
                📂 {browserDs.name}
                {browserPath && <span className="dsm__browser-path"> / {browserPath}</span>}
              </div>
              <div className="dsm__inline-row">
                <button type="button" className="dsm__btn dsm__btn--ghost dsm__btn--sm" onClick={() => browseFiles(browserDs.id, browserPath)} disabled={browserLoading} aria-label="Aggiorna elenco file">🔄</button>
                <button type="button" className="dsm__modal-close" {...browserDialog.closeButtonProps}>✕</button>
              </div>
            </div>
            <div className="dsm__modal-body">
              {browserPath && (
                <div className="dsm__breadcrumb">
                  <button type="button" onClick={() => browseFiles(browserDs.id, '')}>root</button>
                  {browserPath.split('/').filter(Boolean).map((seg, i, arr) => {
                    const path = arr.slice(0, i+1).join('/');
                    return (
                      <React.Fragment key={path}>
                        <span className="dsm__breadcrumb-sep">/</span>
                        <button type="button" onClick={() => browseFiles(browserDs.id, path)}>{seg}</button>
                      </React.Fragment>
                    );
                  })}
                </div>
              )}
              {browserLoading && <div className="dsm__browser-loading">⏳ Caricamento...</div>}
              {browserError  && <div className="dsm__browser-error">❌ {browserError}</div>}
              {!browser.initialLoading && !browserError && browser.hasData && (
                browserFiles.length === 0 ? (
                  <div className="dsm__browser-empty">Nessun file trovato in questa cartella</div>
                ) : (
                  <table className="dsm__file-table">
                    <thead>
                      <tr>
                        <th>Nome</th><th>Tipo</th><th>Dimensione</th><th>Modificato</th>
                      </tr>
                    </thead>
                    <tbody>
                      {browserFiles.map(f => (
                        <tr key={f.path}
                          className={f.type==='folder'?'dsm__file-row--folder':''}
                          {...pressable(f.type==='folder'?()=>browseFiles(browserDs.id,f.path):undefined)}
                          style={f.type==='folder'?{cursor:'pointer'}:{}}>
                          <td className="dsm__file-name">
                            <span className="dsm__file-icon">{f.type==='folder'?'📁':f.type==='csv'?'📊':f.type==='xlsx'?'📗':f.type==='geojson'||f.type==='gpkg'?'🗺️':'📄'}</span>
                            {f.type==='folder'?<span className="dsm__folder-link">{f.name}</span>:f.name}
                          </td>
                          <td><span className={`dsm__file-type${f.type==='folder'?' dsm__file-type--folder':''}`}>{f.type==='folder'?'Cartella':f.type.toUpperCase()}</span></td>
                          <td>{f.type==='folder'?'—':formatSize(f.size)}</td>
                          <td>{formatDate(f.modified_date)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )
              )}
            </div>
          </div>
        </div>
      )}

      {/* CONFIRM DELETE */}
      {confirmDelete && (
        <div className="dsm__overlay" {...confirmDialog.overlayProps}>
          <div
            ref={confirmDialog.ref}
            className="dsm__confirm"
            {...confirmDialog.dialogProps}
          >
            <div className="dsm__confirm-title" id={confirmDialog.titleId}>Elimina Data Source</div>
            <div className="dsm__confirm-text">Sei sicuro di voler eliminare <strong>{confirmDelete.name}</strong>?</div>
            <div className="dsm__confirm-warn">⚠️ Verranno eliminati anche i dati di scan state associati.</div>
            <div className="dsm__confirm-actions">
              <button
                type="button"
                className="dsm__btn dsm__btn--ghost"
                onClick={() => setConfirmDelete(null)}
                disabled={busy.isBusy(`delete:${confirmDelete.id}`)}
              >Annulla</button>
              <button
                type="button"
                className="dsm__btn dsm__btn--danger"
                onClick={() => handleDelete(confirmDelete.id)}
                disabled={busy.isBusy(`delete:${confirmDelete.id}`)}
              >{busy.isBusy(`delete:${confirmDelete.id}`) ? 'Eliminazione…' : 'Elimina'}</button>
            </div>
          </div>
        </div>
      )}

      {/* SHARE MODAL (solo superadmin) */}
      {sharingDatasource && (
        <div className="dsm__overlay" {...shareDialog.overlayProps}>
          <div
            ref={shareDialog.ref}
            className="dsm__modal dsm__type-info"
            {...shareDialog.dialogProps}
          >
            <div className="dsm__modal-header">
              <div className="dsm__modal-title" id={shareDialog.titleId}>🔗 Condividi Data Source — {sharingDatasource.name}</div>
              <button type="button" className="dsm__modal-close" {...shareDialog.closeButtonProps}>✕</button>
            </div>
            <div className="dsm__modal-body">
              <AllowedGroupsSelector
                embedded
                resourceType="datasources"
                resourceId={sharingDatasource.id}
                onClose={() => setSharingDatasource(null)}
              />
            </div>
          </div>
        </div>
      )}
    </PageLayout>
  );
}
