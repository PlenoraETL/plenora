/**
 * ConnectionManager.jsx — Gestione connessioni database e ArcGIS
 * Redesign Plenora: sidebar layout, hex bg, nuovo design system
 * Route: /connections
 */
import React, { useState, useRef } from 'react';
import { useToast } from '../hooks/useToast';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import {
  listConnections, createConnection, updateConnection,
  deleteConnection, testConnection
} from '../api';
import {
  listFeatureConnections, createFeatureConnection,
  updateFeatureConnection, deleteFeatureConnection, testFeatureConnection
} from '../api/features';
import { useHexCanvas } from '../utils/hexCanvas';
import AllowedGroupsSelector from '../components/shared/AllowedGroupsSelector';
import AppSidebar from '../components/AppSidebar';
import ThemeToggle from '../components/common/ThemeToggle';
import ModalWrapper, { ModalButton } from '../components/common/ModalWrapper';
import ConfirmDialog from '../components/common/ConfirmDialog';
import ConnectionSetsPanel from '../components/connections/ConnectionSetsPanel';
import { useConfirmModal } from '../hooks/useConfirmModal';
import { handleCascade409, formatCascadeDependencies } from '../api/errors';
import { can, role } from '../utils/permissions';
import { toastApiError } from '../utils/toastApiError';
import { useResource } from '../hooks/useResource';
import { useBusy } from '../hooks/useBusy';
import { ResourceError } from '../components/common/ListState';
import '../styles/ConnectionManager.css';
import { requestErrorMessage } from '../utils/errorMessages';
import { goBack } from '../utils/navigation';
import FieldInput, { FieldScope } from '../components/common/FieldInput';
import { useFieldScope } from '../hooks/useFieldBuffer';
import { integer } from '../utils/fieldCodecs';

// Porta dal campo unico (R8.4): vuota o fuori 1-65535 e' l'errore del campo.
const PORT = integer({ min: 1, max: 65535 });

/**
 * R8.7: in modifica la categoria (database / ArcGIS) e' quella della
 * connessione: un tipo dell'altra categoria non si puo' scegliere (prima
 * nasceva una connessione nuova e la vecchia restava).
 */
export const typeSelectable = (type, editingCategory) => !editingCategory || type.category === editingCategory;

const ALL_TYPES = [
  { value: 'postgres', label: '🐘 PostgreSQL',   color: '#4B8BFF', colorRgb: '75,139,255',  defaultPort: 5432, defaultSchema: 'public', category: 'database' },
  { value: 'mssql',    label: '🔷 MS SQL Server', color: '#EF4444', colorRgb: '239,68,68',   defaultPort: 1433, defaultSchema: 'dbo',    category: 'database' },
  { value: 'mysql',    label: '🐬 MySQL',         color: '#4BA3D8', colorRgb: '75,163,216',  defaultPort: 3306, defaultSchema: '',       category: 'database' },
  { value: 'oracle',   label: '🔴 Oracle',        color: '#F97316', colorRgb: '249,115,22',  defaultPort: 1521, defaultSchema: '',       category: 'database' },
  { value: 'sqlite',   label: '📁 SQLite',        color: '#6B7280', colorRgb: '107,114,128', defaultPort: 0,    defaultSchema: '',       category: 'database' },
  { value: 'arcgis',   label: '🌐 ArcGIS Server', color: '#F5A623', colorRgb: '245,166,35',  defaultPort: 0,    defaultSchema: '',       category: 'arcgis'   },
];
function ConnectionManager() {
  const toast    = useToast();
  const confirmModal = useConfirmModal();
  const navigate = useNavigate();
  const { user } = useAuth();
  const canvasRef = useRef(null);
  useHexCanvas(canvasRef);

  const [showModal,         setShowModal]         = useState(false);
  const [editingConnection, setEditingConnection] = useState(null);
  const [editingCategory,   setEditingCategory]   = useState(null);
  // Test per connessione (Fase 5 T2, R9.10): la fine del test di A non
  // riabilita B mentre il suo test gira.
  const testing = useBusy();
  const [saving,            setSaving]            = useState(false);
  const fields = useFieldScope();
  const [testResults,       setTestResults]       = useState({});
  const [activeTab,         setActiveTab]         = useState('all');
  const [sharingConnection, setSharingConnection] = useState(null);  // conn in sharing modal (solo super)

  const isSuperadmin    = role.isSuperadmin(user);
  const canCreateConnection = can.createConnection(user);

  const [formData, setFormData] = useState({
    name: '', description: '', type: 'postgres',
    host: '', port: 5432, database: '', db_schema: 'public',
    username: '', password: '',
    mssql_tls_mode: 'verify',
    server_url: '', auth_type: 'public', client_id: '', client_secret: '',
  });

  // Fase 5 T2 (R4.7, R4.8): elenco dall'hook comune. Spinner a pagina
  // intera solo al primo caricamento; una delle due liste non caricata e'
  // un avviso con «Riprova» (non una lista vuota); entrambe = errore.
  const resource = useResource(async () => {
    const [dbResult, arcResult] = await Promise.allSettled([
      listConnections(),
      listFeatureConnections(),
    ]);
    if (dbResult.status === 'rejected' && arcResult.status === 'rejected') throw dbResult.reason;
    const dbData = dbResult.status === 'fulfilled' ? dbResult.value : { connections: [] };
    const arcData = arcResult.status === 'fulfilled' ? arcResult.value : { connections: [] };
    const dbConns  = (dbData.connections || []).map(c => ({ ...c, _category: 'database', _typeInfo: ALL_TYPES.find(t => t.value === c.type) || ALL_TYPES[0] }));
    const arcConns = (arcData.connections || arcData || []).map(c => ({ ...c, type: 'arcgis', _category: 'arcgis', _typeInfo: ALL_TYPES.find(t => t.value === 'arcgis') }));
    const failures = [];
    if (dbResult.status === 'rejected') failures.push(`Connessioni database non caricate: ${requestErrorMessage(dbResult.reason)}`);
    if (arcResult.status === 'rejected') failures.push(`Connessioni ArcGIS non caricate: ${requestErrorMessage(arcResult.reason)}`);
    return { connections: [...dbConns, ...arcConns], failures };
  }, { key: 'connections' });
  const allConnections = resource.data?.connections || [];
  const loadFailures = resource.data?.failures || [];
  const loadConnections = () => resource.reload();

  const handleOpenModal = (conn = null) => {
    if (conn) {
      setEditingConnection(conn);
      setEditingCategory(conn._category);
      if (conn._category === 'arcgis') {
        setFormData({ name: conn.name || '', description: '', type: 'arcgis', host: '', port: 0, database: '', db_schema: '', username: conn.username || '', password: conn.password || '', mssql_tls_mode: 'verify', server_url: conn.server_url || '', auth_type: conn.auth_type || 'public', client_id: conn.client_id || '', client_secret: conn.client_secret || '' });
      } else {
        setFormData({ name: conn.name || '', description: conn.description || '', type: conn.type || 'postgres', host: conn.host || '', port: conn.port || 5432, database: conn.database || '', db_schema: conn.db_schema || '', username: conn.username || '', password: conn.password || '', mssql_tls_mode: conn.mssql_tls_mode || 'verify', server_url: '', auth_type: 'public', client_id: '', client_secret: '' });
      }
    } else {
      setEditingConnection(null); setEditingCategory(null);
      setFormData({ name: '', description: '', type: 'postgres', host: '', port: 5432, database: '', db_schema: 'public', username: '', password: '', mssql_tls_mode: 'verify', server_url: '', auth_type: 'public', client_id: '', client_secret: '' });
    }
    setShowModal(true);
  };

  const handleCloseModal = () => { setShowModal(false); setEditingConnection(null); setEditingCategory(null); };

  const handleTypeChange = (type) => {
    const info = ALL_TYPES.find(t => t.value === type);
    if (info && !typeSelectable(info, editingConnection ? editingCategory : null)) return;
    setFormData(prev => ({ ...prev, type, port: info?.defaultPort || 0, db_schema: info?.defaultSchema || '' }));
  };

  const handleSubmit = async () => {
    if (saving) return;
    if (!fields.check()) { toast.warning('Correggi i campi segnati'); return; }
    const typeInfo = ALL_TYPES.find(t => t.value === formData.type);
    const isArcgis = typeInfo?.category === 'arcgis';
    // Una modifica aggiorna sempre la connessione aperta, mai una nuova.
    if (editingConnection && typeInfo?.category !== editingCategory) { toast.warning('Il tipo della connessione non si può cambiare in un\'altra categoria'); return; }
    if (!formData.name.trim()) { toast.warning('Il nome è obbligatorio'); return; }
    if (isArcgis && !formData.server_url.trim()) { toast.warning("URL server obbligatorio"); return; }
    if (!isArcgis && (!formData.host.trim() || !formData.database.trim())) { toast.warning('Host e database obbligatori'); return; }
    setSaving(true);
    try {
      if (isArcgis) {
        const p = { name: formData.name, server_url: formData.server_url, auth_type: formData.auth_type, username: formData.username, password: formData.password, client_id: formData.client_id, client_secret: formData.client_secret };
        editingConnection && editingCategory === 'arcgis' ? await updateFeatureConnection(editingConnection.id, p) : await createFeatureConnection(p);
      } else {
        const p = { name: formData.name, description: formData.description, type: formData.type, host: formData.host, port: formData.port, database: formData.database, db_schema: formData.db_schema, username: formData.username, password: formData.password, mssql_tls_mode: formData.type === 'mssql' ? formData.mssql_tls_mode : 'verify' };
        editingConnection && editingCategory === 'database' ? await updateConnection(editingConnection.id, p) : await createConnection(p);
      }
      handleCloseModal(); loadConnections();
    } catch (err) {
      toastApiError(toast, err, 'Errore');
    } finally { setSaving(false); }
  };

  const handleDelete = async (conn) => {
    const ok = await confirmModal.confirm({
      title: 'Elimina connessione',
      message: `Eliminare "${conn.name}"?`,
      confirmText: 'Elimina',
      variant: 'danger',
    });
    if (!ok) return;
    try {
      conn._category === 'arcgis' ? await deleteFeatureConnection(conn.id) : await deleteConnection(conn.id);
      loadConnections();
    } catch (err) {
      const cascade = handleCascade409(err);
      if (cascade) {
        toast.error(
          `Impossibile eliminare "${conn.name}": ${formatCascadeDependencies(cascade.dependencies)}. Rimuovili prima.`,
          10000
        );
        return;
      }
      // Pre-fix il fallback usava un messaggio hardcoded "Errore
      // eliminazione" che ingoiava err.message (auth fallita, rete
      // giu', BE 5xx). Propaga il message tramite l'helper condiviso.
      toastApiError(toast, err, 'Errore eliminazione connessione');
    }
  };

  const handleTest = async (conn) => {
    try {
      const r = await testing.run(conn.id, () => (conn._category === 'arcgis' ? testFeatureConnection(conn.id) : testConnection(conn.id)));
      if (!r.skipped) setTestResults(prev => ({ ...prev, [conn.id]: r.value }));
    } catch (err) {
      setTestResults(prev => ({ ...prev, [conn.id]: { success: false, message: requestErrorMessage(err) } }));
    }
  };

  const currentTypeInfo = ALL_TYPES.find(t => t.value === formData.type);
  const isArcgis = currentTypeInfo?.category === 'arcgis';

  const dbConns  = allConnections.filter(c => c._category === 'database');
  const arcConns = allConnections.filter(c => c._category === 'arcgis');
  const visibleDb  = ['arcgis', 'sets'].includes(activeTab) ? [] : dbConns;
  const visibleArc = ['database', 'sets'].includes(activeTab) ? [] : arcConns;

  if (resource.initialLoading) return (
    <div className="cm cm--loading">
      <div className="cm__loading-inner"><div className="cm__loading-icon">⬡</div>Caricamento...</div>
    </div>
  );

  return (
    <div className="cm">
<AppSidebar counts={{ connections: allConnections.length }} />
<div className="cm__main">
        <header className="cm__topbar">
          <button type="button" className="cm__back-btn" onClick={() => goBack(navigate)}>← Indietro</button>
          <div className="cm__page-title">Connessioni</div>
          <div className="cm__topbar-right">
            <ThemeToggle classPrefix="cm" />
            {canCreateConnection && (
              <button type="button" className="cm__btn cm__btn--primary" onClick={() => handleOpenModal()}>＋ Nuova Connessione</button>
            )}
          </div>
        </header>

        <div className="cm__content">
          <canvas ref={canvasRef} className="cm__hex-bg" />

          {/* Tabs */}
          <div className="cm__tabs">
            {[
              { key: 'all',      label: 'Tutte',    count: allConnections.length },
              { key: 'database', label: 'Database', count: dbConns.length },
              { key: 'arcgis',   label: 'ArcGIS',   count: arcConns.length },
              { key: 'sets',     label: 'Connection Set', count: null },
            ].map(t => (
              <button key={t.key} type="button" className={`cm__tab ${activeTab === t.key ? 'cm__tab--active' : ''}`} onClick={() => setActiveTab(t.key)}>
                {t.label} {t.count != null && <span className="cm__tab-count">{t.count}</span>}
              </button>
            ))}
          </div>

          {resource.status === 'error' && (
            <ResourceError resource={resource} title="Connessioni non caricate" />
          )}
          {loadFailures.map(message => (
            <ResourceError key={message} compact title="Elenco incompleto" message={message} onRetry={resource.reload} />
          ))}
          {resource.status === 'error' && !resource.hasData ? null : allConnections.length === 0 && activeTab !== 'sets' ? (
            <EmptyState
              onNew={() => handleOpenModal()}
              canCreate={canCreateConnection}
            />
          ) : (
            <>
              {visibleDb.length > 0 && (
                <ConnTable
                  title="Connessioni Database"
                  subtitle="PostgreSQL, MS SQL Server, Oracle, MySQL, SQLite"
                  connections={visibleDb}
                  testResults={testResults}
                  isTesting={testing.isBusy}
                  onTest={handleTest}
                  onEdit={handleOpenModal}
                  onDelete={handleDelete}
                  onShare={setSharingConnection}
                  canEditOf={(c) => can.editConnection(user, c)}
                  canShare={can.shareResources(user)}
                  arcgis={false}
                />
              )}
              {visibleArc.length > 0 && (
                <ConnTable
                  title="Connessioni ArcGIS"
                  subtitle="Feature Server e Map Service"
                  connections={visibleArc}
                  testResults={testResults}
                  isTesting={testing.isBusy}
                  onTest={handleTest}
                  onEdit={handleOpenModal}
                  onDelete={handleDelete}
                  onShare={setSharingConnection}
                  canEditOf={(c) => can.editConnection(user, c)}
                  canShare={can.shareResources(user)}
                  arcgis={true}
                />
              )}
              {activeTab === 'sets' && (
                <ConnectionSetsPanel
                  connections={dbConns.filter(connection => (
                    !isSuperadmin || connection.group_id === user?.group_id
                  ))}
                  canEdit={canCreateConnection}
                  canEditItem={(item) => can.editConnection(user, item)}
                  toast={toast}
                  confirm={confirmModal.confirm}
                />
              )}
              {/* Test errors */}
              {Object.entries(testResults).map(([id, result]) => {
                if (!result || result.success) return null;
                const conn = allConnections.find(c => c.id === id);
                if (!conn) return null;
                return (
                  <div key={id} className="cm__test-error">
                    <strong>✗ {conn.name}:</strong> {result.message}
                  </div>
                );
              })}
            </>
          )}
        </div>
      </div>
{/* ── MODAL ── */}
      {showModal && (
        <ModalWrapper
          isOpen={true}
          onClose={handleCloseModal}
          title={editingConnection ? 'Modifica Connessione' : 'Nuova Connessione'}
          icon={editingConnection ? '✏️' : '＋'}
          size="md"
          busy={saving}
          footer={
            <>
              <ModalButton variant="secondary" onClick={handleCloseModal} disabled={saving}>Annulla</ModalButton>
              <ModalButton variant="primary" onClick={handleSubmit} loading={saving}>
                {editingConnection ? 'Salva Modifiche' : 'Crea Connessione'}
              </ModalButton>
            </>
          }
        >
          <FieldScope scope={fields}>
            {/* Tipo */}
            <div className="cm__modal-field">
              <label className="cm__modal-label">Tipo</label>
              <div className="cm__db-types">
                {ALL_TYPES.map(t => (
                  <button
                    key={t.value} type="button"
                    className={`cm__db-type-btn ${formData.type === t.value ? 'cm__db-type-btn--on' : ''}`}
                    style={formData.type === t.value ? { borderColor: t.color, background: `rgba(${t.colorRgb},0.10)`, color: t.color } : {}}
                    onClick={() => handleTypeChange(t.value)}
                    disabled={!typeSelectable(t, editingConnection ? editingCategory : null)}
                    title={!typeSelectable(t, editingConnection ? editingCategory : null) ? 'In modifica il tipo resta nella stessa categoria' : undefined}
                  >
                    {t.label}
                  </button>
                ))}
              </div>
            </div>

            {/* Nome */}
            <div className="cm__modal-field">
              <label className="cm__modal-label">Nome connessione *</label>
              <input type="text" className="cm__modal-input" value={formData.name} onChange={e => setFormData({ ...formData, name: e.target.value })} placeholder="Es. 'Archibus ICSC'" autoFocus />
            </div>

            {/* Descrizione (solo DB) */}
            {!isArcgis && (
              <div className="cm__modal-field">
                <label className="cm__modal-label">Descrizione</label>
                <input type="text" className="cm__modal-input" value={formData.description} onChange={e => setFormData({ ...formData, description: e.target.value })} placeholder="Descrizione opzionale..." />
              </div>
            )}

            {/* Campi DB */}
            {!isArcgis && (
              <>
                <div className="cm__form-row cm__form-row--2-1">
                  <div className="cm__modal-field cm__modal-field--inline">
                    <label className="cm__modal-label">Host *</label>
                    <input type="text" className="cm__modal-input" value={formData.host} onChange={e => setFormData({ ...formData, host: e.target.value })} placeholder="localhost" />
                  </div>
                  <div className="cm__modal-field cm__modal-field--inline">
                    <label className="cm__modal-label">Porta</label>
                    <FieldInput className="cm__modal-input" aria-label="Porta" codec={PORT} value={formData.port} onCommit={port => setFormData(f => ({ ...f, port }))} />
                  </div>
                </div>
                <div className="cm__form-row cm__form-row--2-1 cm__form-row--mt-md">
                  <div className="cm__modal-field cm__modal-field--inline">
                    <label className="cm__modal-label">Database *</label>
                    <input type="text" className="cm__modal-input" value={formData.database} onChange={e => setFormData({ ...formData, database: e.target.value })} placeholder="nome_database" />
                  </div>
                  <div className="cm__modal-field cm__modal-field--inline">
                    <label className="cm__modal-label">Schema</label>
                    <input type="text" className="cm__modal-input" value={formData.db_schema} onChange={e => setFormData({ ...formData, db_schema: e.target.value })} placeholder={formData.type === 'mssql' ? 'dbo' : 'public'} />
                  </div>
                </div>
                <div className="cm__form-row cm__form-row--1-1 cm__form-row--mt-md">
                  <div className="cm__modal-field cm__modal-field--inline">
                    <label className="cm__modal-label">Username</label>
                    <input type="text" className="cm__modal-input" value={formData.username} onChange={e => setFormData({ ...formData, username: e.target.value })} />
                  </div>
                  <div className="cm__modal-field cm__modal-field--inline">
                    <label className="cm__modal-label">Password</label>
                    <input type="password" className="cm__modal-input" value={formData.password} onChange={e => setFormData({ ...formData, password: e.target.value })} placeholder="••••••••" />
                  </div>
                </div>
                {formData.type === 'mssql' && (
                  <div className="cm__modal-field cm__form-row--mt-md">
                    <label className="cm__modal-label">Verifica certificato TLS</label>
                    <select
                      className="cm__modal-input"
                      value={formData.mssql_tls_mode}
                      disabled={!isSuperadmin}
                      onChange={e => setFormData({ ...formData, mssql_tls_mode: e.target.value })}
                    >
                      <option value="verify">Verifica certificato (consigliato)</option>
                      <option value="trust_server_certificate">Accetta certificato non attendibile</option>
                    </select>
                    {formData.mssql_tls_mode === 'trust_server_certificate' && (
                      <div className="cm__modal-hint" role="alert">
                        Attenzione: la connessione resta cifrata, ma l’identità del server non viene verificata. Usare solo per server interni conosciuti.
                      </div>
                    )}
                    {!isSuperadmin && <div className="cm__modal-hint">Solo un superadmin può modificare questa impostazione.</div>}
                  </div>
                )}
              </>
            )}

            {/* Campi ArcGIS */}
            {isArcgis && (
              <>
                <div className="cm__modal-field">
                  <label className="cm__modal-label">URL Server ArcGIS *</label>
                  <input type="text" className="cm__modal-input" value={formData.server_url} onChange={e => setFormData({ ...formData, server_url: e.target.value })} placeholder="https://server/arcgis/rest/services" />
                  <div className="cm__modal-hint">ArcGIS Online: https://www.arcgis.com</div>
                </div>
                <div className="cm__modal-field">
                  <label className="cm__modal-label">Autenticazione</label>
                  <div className="cm__auth-types">
                    {[
                      { value: 'public', label: '🔓 Pubblico', color: '#34C17A', rgb: '52,193,122' },
                      { value: 'token',  label: '🔐 Token',    color: '#F5A623', rgb: '245,166,35' },
                      { value: 'oauth2', label: '🔑 OAuth2',   color: '#4B8BFF', rgb: '75,139,255' },
                    ].map(a => (
                      <button key={a.value} type="button"
                        className={`cm__db-type-btn ${formData.auth_type === a.value ? 'cm__db-type-btn--on' : ''}`}
                        style={formData.auth_type === a.value ? { borderColor: a.color, background: `rgba(${a.rgb},0.10)`, color: a.color } : {}}
                        onClick={() => setFormData({ ...formData, auth_type: a.value })}
                      >{a.label}</button>
                    ))}
                  </div>
                </div>
                {formData.auth_type === 'token' && (
                  <div className="cm__form-row cm__form-row--1-1">
                    <div className="cm__modal-field cm__modal-field--inline"><label className="cm__modal-label">Username</label><input type="text" className="cm__modal-input" value={formData.username} onChange={e => setFormData({ ...formData, username: e.target.value })} /></div>
                    <div className="cm__modal-field cm__modal-field--inline"><label className="cm__modal-label">Password</label><input type="password" className="cm__modal-input" value={formData.password} onChange={e => setFormData({ ...formData, password: e.target.value })} placeholder="••••••••" /></div>
                  </div>
                )}
                {formData.auth_type === 'oauth2' && (
                  <div className="cm__form-row cm__form-row--1-1">
                    <div className="cm__modal-field cm__modal-field--inline"><label className="cm__modal-label">Client ID</label><input type="text" className="cm__modal-input" value={formData.client_id} onChange={e => setFormData({ ...formData, client_id: e.target.value })} placeholder="Es. PrKizU4tc3X..." /></div>
                    <div className="cm__modal-field cm__modal-field--inline"><label className="cm__modal-label">Client Secret</label><input type="password" className="cm__modal-input" value={formData.client_secret} onChange={e => setFormData({ ...formData, client_secret: e.target.value })} placeholder="••••••••" /></div>
                  </div>
                )}
              </>
            )}
          </FieldScope>
        </ModalWrapper>
      )}
{sharingConnection && (
        <ModalWrapper
          isOpen={true}
          onClose={() => setSharingConnection(null)}
          title="Condividi connessione"
          icon="🔗"
          subtitle={sharingConnection.name}
          size="md"
        >
          <AllowedGroupsSelector
            embedded
            resourceType={sharingConnection._category === 'arcgis' ? 'arcgis_connections' : 'connections'}
            resourceId={sharingConnection.id}
            onClose={() => setSharingConnection(null)}
          />
        </ModalWrapper>
      )}
      <ConfirmDialog {...confirmModal} />
    </div>
  );
}
function EmptyState({ onNew, canCreate = true }) {
  return (
    <div className="cm__empty">
      <div className="cm__empty-icon">🔌</div>
      <div className="cm__empty-title">Nessuna connessione</div>
      <div className="cm__empty-text">
        {canCreate
          ? 'Crea la tua prima connessione per iniziare ad estrarre dati'
          : 'Nessuna connessione disponibile. Contatta un amministratore.'}
      </div>
      {canCreate && (
        <button type="button" className="cm__btn cm__btn--primary cm__btn--mt-md" onClick={onNew}>＋ Crea connessione</button>
      )}
    </div>
  );
}
function ConnTable({ title, subtitle, connections, testResults, isTesting: isTestingOf, onTest, onEdit, onDelete, onShare, canEditOf, canShare, arcgis }) {
  return (
    <div className="cm__section">
      <div className="cm__section-header">
        <div className="cm__section-title">{title}</div>
        <div className="cm__section-sub">{subtitle}</div>
      </div>
      <div className="cm__table-wrap">
        <table className="cm__table">
          <thead>
            <tr>
              <th>Nome</th>
              <th>Tipo</th>
              <th>{arcgis ? 'URL Server' : 'Host / Database'}</th>
              <th>{arcgis ? 'Autenticazione' : 'Schema'}</th>
              <th>Stato</th>
              <th className="cm__th-actions">Azioni</th>
            </tr>
          </thead>
          <tbody>
            {connections.map(conn => {
              const info       = conn._typeInfo;
              const testResult = testResults[conn.id];
              const isTesting  = isTestingOf(conn.id);
              const canEdit    = canEditOf(conn);
              return (
                <tr key={conn.id} className="cm__tr">
                  <td>
                    <div className="cm__conn-name">
                      {conn.name}
                      {conn.can_edit === false && (
                        <span
                          title="Solo un superadmin può modificare questa risorsa"
                          className="cm__field-help-icon"
                        >
                          🔒
                        </span>
                      )}
                    </div>
                    {conn.description && <div className="cm__conn-desc">{conn.description}</div>}
                  </td>
                  <td>
                    <span className="cm__type-badge" style={{ color: info?.color, background: `rgba(${info?.colorRgb},0.08)`, borderColor: `rgba(${info?.colorRgb},0.22)` }}>
                      {info?.label || conn.type}
                    </span>
                  </td>
                  <td>
                    {arcgis ? (
                      <span className="cm__conn-detail">{conn.server_url}</span>
                    ) : (
                      <>
                        <div className="cm__conn-detail">{conn.host}:{conn.port}</div>
                        <div className="cm__conn-detail-sub">{conn.database}</div>
                      </>
                    )}
                  </td>
                  <td>
                    <span className="cm__conn-detail">
                      {arcgis ? (conn.auth_type || 'public') : (conn.db_schema || '—')}
                    </span>
                  </td>
                  <td>
                    {testResult ? (
                      <span className={`cm__status-dot ${testResult.success ? 'cm__status-ok' : 'cm__status-err'}`}>
                        {testResult.success ? 'OK' : 'Errore'}
                      </span>
                    ) : (
                      <span className="cm__status-dot cm__status-none">Non testata</span>
                    )}
                  </td>
                  <td>
                    <div className="cm__actions">
                      <button type="button" className="cm__btn cm__btn--ghost cm__btn--sm" onClick={() => onTest(conn)} disabled={isTesting}>
                        {isTesting ? '⏳' : '🔌 Test'}
                      </button>
                      {canEdit && (
                        <button type="button" className="cm__btn cm__btn--ghost cm__btn--sm" onClick={() => onEdit(conn)}>✏️ Modifica</button>
                      )}
                      {canShare && (
                        <button
                          type="button"
                          className="cm__btn cm__btn--ghost cm__btn--sm"
                          title="Gestisci condivisione cross-gruppo"
                          aria-label="Gestisci condivisione cross-gruppo"
                          onClick={() => onShare(conn)}
                        >🔗</button>
                      )}
                      {canEdit && (
                        <button type="button" className="cm__btn cm__btn--danger cm__btn--sm" onClick={() => onDelete(conn)} aria-label="Elimina connessione">🗑</button>
                      )}
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
export default ConnectionManager;
