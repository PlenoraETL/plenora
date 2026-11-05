/**
 * UserManager.jsx — Gestione utenti e gruppi
 * Route: /admin/users
 * Accessibile solo ad admin e superadmin.
 * - Admin: vede e gestisce utenti del proprio gruppo
 * - Superadmin: vede tutti gli utenti + gestisce i gruppi
 */
import React, { useId, useState } from 'react';
import { useResource } from '../hooks/useResource';
import { ResourceError } from '../components/common/ListState';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { useToast } from '../hooks/useToast';
import {
  listUsers, createUser, updateUser, deleteUser, resetPassword,
  listGroups, createGroup, deleteGroup, getGroupSummary,
} from '../api/users';
import PageLayout from '../components/common/PageLayout';
import EmptyState from '../components/common/EmptyState';
import ModalWrapper, { ModalButton } from '../components/common/ModalWrapper';
import { can, role } from '../utils/permissions';
import { humanizeError, requestErrorMessage } from '../utils/errorMessages';
import { PASSWORD_MIN_LENGTH, passwordProblem } from '../utils/passwordRule';
import '../styles/UserManager.css';
import SearchField, { SearchEmpty } from '../components/common/SearchField';
import { useSearch } from '../hooks/useSearch';
/**
 * Ruoli: UNA tabella per select, badge e regole del form (Fase 5 T5, R8.20).
 * `superOnly`: solo un superadmin li assegna (backend users_router:
 * ROLES_ADMIN_OR_ABOVE in creazione).
 */
export const USER_ROLES = Object.freeze([
  { value: 'esecutore', option: '▶ Esecutore (solo eseguibili — kiosk)', badge: '▶ esecutore', superOnly: false },
  { value: 'viewer', option: '👁 Viewer (sola lettura)', badge: '👁 viewer', superOnly: false },
  { value: 'user', option: '👤 User', badge: '👤 user', superOnly: false },
  { value: 'admin', option: '🔑 Admin', badge: '🔑 admin', superOnly: true },
  { value: 'superadmin', option: '👑 Superadmin', badge: '👑 superadmin', superOnly: true },
]);

/**
 * Campi che chi scrive puo' davvero cambiare: la whitelist del backend
 * (users_router.update_user: admin solo email e is_active, superadmin anche
 * role e group_id; il body e' extra="forbid": username e password in
 * modifica darebbero 422). Un campo non
 * modificabile e' spento e non va nel payload: niente modifica persa in
 * silenzio.
 */
export function userFormRules({ isEdit, isSuperadmin }) {
  return {
    username: !isEdit,
    password: !isEdit,
    role: !isEdit || isSuperadmin,
    // In creazione e' obbligatorio (UserCreate): l'admin manda il proprio,
    // che il backend comunque impone; in modifica solo il superadmin.
    group_id: !isEdit || isSuperadmin,
    is_active: isEdit,
  };
}

/** Payload dai soli campi modificabili. */
export function userPayload(form, rules) {
  return Object.fromEntries(Object.entries(rules).filter(([, editable]) => editable).map(([k]) => [k, form[k]]));
}

function RoleBadge({ role }) {
  const entry = USER_ROLES.find(r => r.value === role);
  return <span className={`um__badge um__badge--${role}`}>{entry?.badge || role}</span>;
}
function StatusBadge({ active }) {
  return (
    <span className={`um__badge um__badge--${active ? 'active' : 'inactive'}`}>
      {active ? '● Attivo' : '● Disabilitato'}
    </span>
  );
}
function UserAvatar({ username, role }) {
  return (
    <div className={`um__avatar um__avatar--${role}`}>
      {(username || '?')[0].toUpperCase()}
    </div>
  );
}
// MODAL — Crea/Modifica Utente
function UserModal({ user, groups, currentUser, onClose, onSave }) {
  const fid = useId();
  const isEdit = !!user;
  const [form, setForm] = useState({
    username:  user?.username  || '',
    password:  '',
    role:      user?.role      || 'user',
    group_id:  user?.group_id  || currentUser.group_id,
    is_active: user?.is_active ?? true,
  });
  const [loading, setLoading] = useState(false);
  const [error, setError]     = useState('');
  const isSuperadmin = role.isSuperadmin(currentUser);
  const rules = userFormRules({ isEdit, isSuperadmin });
  // Ruoli proponibili; il ruolo attuale c'e' sempre (anche se chi scrive non
  // puo' assegnarlo), cosi' si vede quello vero e non il primo dell'elenco.
  const roleOptions = USER_ROLES.filter(r => isSuperadmin || !r.superOnly || r.value === form.role);
  const set = (k, v) => setForm(f => ({ ...f, [k]: v }));

  const handleSubmit = async () => {
    if (!form.username.trim()) return setError('Username obbligatorio');
    // Regola unica della password (utils/passwordRule, uguale al backend).
    const pwdProblem = isEdit ? null : passwordProblem(form.password);
    if (pwdProblem) return setError(pwdProblem);
    setLoading(true); setError('');
    try {
      // Solo i campi modificabili (userFormRules = whitelist del backend).
      // La password in modifica ha la sua finestra (Reimposta password).
      await onSave(userPayload(form, rules));
      onClose();
    } catch (e) {
      setError(humanizeError(e).message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <ModalWrapper
      isOpen={true}
      onClose={onClose}
      busy={loading}
      title={isEdit ? 'Modifica utente' : 'Nuovo utente'}
      icon={isEdit ? '✏️' : '👤'}
      size="md"
      footer={
        <>
          <ModalButton variant="secondary" onClick={onClose} disabled={loading}>Annulla</ModalButton>
          <ModalButton variant="primary" onClick={handleSubmit} loading={loading}>
            {isEdit ? 'Salva' : 'Crea utente'}
          </ModalButton>
        </>
      }
    >
      <form className="um__form" onSubmit={(e) => { e.preventDefault(); handleSubmit(); }}>
          {error && <div className="um__error">{error}</div>}
          <div className="um__field">
            <label htmlFor={`${fid}-username`}>Username</label>
            <input id={`${fid}-username`} value={form.username} onChange={e => set('username', e.target.value)} placeholder="es. mario.rossi" disabled={isEdit} />
          </div>
          {!isEdit && (
            <div className="um__field">
              <label htmlFor={`${fid}-password`}>Password</label>
              <input id={`${fid}-password`} type="password" value={form.password} onChange={e => set('password', e.target.value)} placeholder={`Almeno ${PASSWORD_MIN_LENGTH} caratteri`} />
            </div>
          )}
          {isEdit && (
            <div className="um__field">
              <label htmlFor={`${fid}-password-hint`}>Password</label>
              <div id={`${fid}-password-hint`} className="um__field-hint um__field-hint--padded">
                Per cambiare la password usa il pulsante <strong>🔑 Reimposta password</strong> nella lista utenti.
              </div>
            </div>
          )}
          <div className="um__field">
            <label htmlFor={`${fid}-role`}>Ruolo</label>
            <select id={`${fid}-role`} value={form.role} onChange={e => set('role', e.target.value)} disabled={!rules.role}>
              {roleOptions.map(r => (
                <option key={r.value} value={r.value} disabled={r.superOnly && !isSuperadmin}>{r.option}</option>
              ))}
            </select>
            {isEdit && !rules.role && (
              <span className="um__field-hint">Solo un superadmin può cambiare il ruolo.</span>
            )}
            {role.isEsecutore(form) && (
              <span className="um__field-hint">
                L'esecutore vede SOLO la lista degli eseguibili del proprio gruppo (e quelli condivisi)
                e può lanciarli. Non ha accesso al resto della UI. I file caricati per l'esecuzione
                vengono cancellati subito dopo il download del risultato (privacy/GDPR).
              </span>
            )}
            {role.isViewer(form) && (
              <span className="um__field-hint">
                I viewer possono leggere e lanciare esecuzioni, ma non creare/modificare/eliminare risorse.
              </span>
            )}
            {!isSuperadmin && (
              <span className="um__field-hint">
                Solo un superadmin può creare admin/superadmin.
              </span>
            )}
          </div>
          {isSuperadmin && (
            <div className="um__field">
              <label htmlFor={`${fid}-group`}>Gruppo</label>
              <select id={`${fid}-group`} value={form.group_id} onChange={e => set('group_id', e.target.value)} disabled={!rules.group_id}>
                {groups.map(g => <option key={g.group_id} value={g.group_id}>{g.name} ({g.group_id})</option>)}
              </select>
            </div>
          )}
          {isEdit && (
            <div className="um__field">
              <label htmlFor={`${fid}-active`}>Stato</label>
              <select id={`${fid}-active`} value={form.is_active ? 'true' : 'false'} onChange={e => set('is_active', e.target.value === 'true')}>
                <option value="true">● Attivo</option>
                <option value="false">● Disabilitato</option>
              </select>
            </div>
          )}
        <button type="submit" hidden aria-hidden="true" tabIndex={-1} />
      </form>
    </ModalWrapper>
  );
}
// MODAL — Reset Password
function ResetPasswordModal({ user, onClose, onSave }) {
  const fid = useId();
  const [newPassword, setNewPassword] = useState('');
  const [loading, setLoading]         = useState(false);
  const [error, setError]             = useState('');

  const handleSubmit = async () => {
    // Regola unica della password (utils/passwordRule, uguale al backend).
    const problem = passwordProblem(newPassword);
    if (problem) return setError(problem);
    setLoading(true); setError('');
    try { await onSave(newPassword); onClose(); }
    catch (e) { setError(humanizeError(e).message); }
    finally { setLoading(false); }
  };

  return (
    <ModalWrapper
      isOpen={true}
      onClose={onClose}
      busy={loading}
      title={`Reset password — ${user.username}`}
      icon="🔑"
      size="sm"
      footer={
        <>
          <ModalButton variant="secondary" onClick={onClose} disabled={loading}>Annulla</ModalButton>
          <ModalButton variant="primary" onClick={handleSubmit} loading={loading}>
            Reimposta password
          </ModalButton>
        </>
      }
    >
      <form className="um__form" onSubmit={(e) => { e.preventDefault(); handleSubmit(); }}>
      {error && <div className="um__error">{error}</div>}
      <div className="um__field">
        <label htmlFor={`${fid}-new-password`}>Nuova password</label>
        <input id={`${fid}-new-password`} type="password" value={newPassword} onChange={e => setNewPassword(e.target.value)} placeholder={`Almeno ${PASSWORD_MIN_LENGTH} caratteri`} autoFocus />
        <span className="um__field-hint">L'utente dovrà usare questa password al prossimo accesso.</span>
      </div>
        <button type="submit" hidden aria-hidden="true" tabIndex={-1} />
      </form>
    </ModalWrapper>
  );
}
// MODAL — Crea Gruppo
function GroupModal({ onClose, onSave }) {
  const fid = useId();
  const [form, setForm] = useState({ group_id: '', name: '', description: '' });
  const [loading, setLoading] = useState(false);
  const [error, setError]     = useState('');
  const set = (k, v) => setForm(f => ({ ...f, [k]: v }));

  const handleSubmit = async () => {
    if (!form.group_id.trim()) return setError('ID gruppo obbligatorio');
    if (!form.name.trim())     return setError('Nome gruppo obbligatorio');
    if (!/^[a-z0-9_-]+$/.test(form.group_id)) return setError('ID gruppo: solo lettere minuscole, numeri, - e _');
    setLoading(true); setError('');
    try { await onSave(form); onClose(); }
    catch (e) { setError(humanizeError(e).message); }
    finally { setLoading(false); }
  };

  return (
    <ModalWrapper
      isOpen={true}
      onClose={onClose}
      busy={loading}
      title="Nuovo gruppo"
      icon="🏢"
      size="md"
      footer={
        <>
          <ModalButton variant="secondary" onClick={onClose} disabled={loading}>Annulla</ModalButton>
          <ModalButton variant="primary" onClick={handleSubmit} loading={loading}>
            Crea gruppo
          </ModalButton>
        </>
      }
    >
      <form className="um__form" onSubmit={(e) => { e.preventDefault(); handleSubmit(); }}>
      {error && <div className="um__error">{error}</div>}
      <div className="um__field">
        <label htmlFor={`${fid}-group-id`}>ID gruppo</label>
        <input id={`${fid}-group-id`} value={form.group_id} onChange={e => set('group_id', e.target.value.toLowerCase())} placeholder="es. comune-milano" />
        <span className="um__field-hint">Identificatore unico — solo lettere minuscole, numeri, - e _</span>
      </div>
      <div className="um__field">
        <label htmlFor={`${fid}-name`}>Nome</label>
        <input id={`${fid}-name`} value={form.name} onChange={e => set('name', e.target.value)} placeholder="es. Comune di Milano" />
      </div>
      <div className="um__field">
        <label htmlFor={`${fid}-description`}>Descrizione (opzionale)</label>
        <input id={`${fid}-description`} value={form.description} onChange={e => set('description', e.target.value)} placeholder="Breve descrizione..." />
      </div>
        <button type="submit" hidden aria-hidden="true" tabIndex={-1} />
      </form>
    </ModalWrapper>
  );
}
// MODAL — Conferma eliminazione
function ConfirmDeleteModal({ title, message, onClose, onConfirm }) {
  const [loading, setLoading] = useState(false);
  const [errorMsg, setErrorMsg] = useState(null);
  const handleConfirm = async () => {
    setLoading(true);
    setErrorMsg(null);
    try { await onConfirm(); onClose(); }
    catch (e) {
      // Pre-fix il catch ingoiava l'errore senza feedback: lo spinner
      // spariva e l'utente non sapeva perche' l'eliminazione fosse
      // fallita. Mostra il messaggio (axios error o nativo) inline.
      const msg = requestErrorMessage(e, 'Eliminazione fallita');
      setErrorMsg(msg);
      setLoading(false);
    }
  };
  return (
    <ModalWrapper
      isOpen={true}
      onClose={onClose}
      busy={loading}
      title={title}
      icon="⚠️"
      size="sm"
      footer={
        <>
          <ModalButton variant="secondary" onClick={onClose} disabled={loading}>Annulla</ModalButton>
          <ModalButton variant="danger" onClick={handleConfirm} loading={loading}>
            Elimina
          </ModalButton>
        </>
      }
    >
      <p className="um__cdg-message">{message}</p>
      {errorMsg && (
        <p className="um__cdg-error" style={{ color: '#dc2626', marginTop: 8 }}>
          {errorMsg}
        </p>
      )}
    </ModalWrapper>
  );
}
// MODAL — Conferma eliminazione gruppo (cascade, audit R10.3)
//
// Regole: (1) si conferma scrivendo il NOME del gruppo, come dice il testo;
// (2) prima si vede il report d'impatto del server (getGroupSummary) e
// finche' non e' caricato non si puo' confermare; se non arriva, si
// riprova ma non si elimina "al buio".
const GROUP_IMPACT = [
  ['projects', '📁', 'Progetti'],
  ['users', '👥', 'Utenti'],
  ['connections', '🔌', 'Connessioni DB'],
  ['file_sources', '📄', 'File sorgente'],
  ['datasources', '🗄️', 'DataSources'],
  ['api_connections', '🔗', 'Connettori API'],
  ['schedules', '⏱️', 'Schedule'],
];

const groupImpactItems = (summary) => GROUP_IMPACT
  .map(([key, icon, label]) => ({ key, icon, label, value: Number(summary?.[key] || 0) }));

// Riassunto del report restituito dal server dopo l'eliminazione.
const groupReportText = (report) => {
  if (!report || typeof report !== 'object') return '';
  const parts = groupImpactItems(report)
    .filter(({ key }) => key in report)
    .map(({ label, value }) => `${value} ${label.toLowerCase()}`);
  return parts.length ? ` (${parts.join(', ')})` : '';
};

function ConfirmDeleteGroupModal({ group, onClose, onConfirm }) {
  // Riepilogo d'impatto dall'hook comune: senza riepilogo non si conferma.
  const summaryResource = useResource(() => getGroupSummary(group.group_id), { key: group.group_id });
  const summary = summaryResource.data || null;
  const status = summaryResource.status === 'error' ? 'error' : summaryResource.hasData ? 'ready' : 'loading';
  const [deleting, setDeleting] = useState(false);
  const [error, setError]       = useState('');
  const [typed, setTyped]       = useState('');


  const expectedName = String(group.name || '').trim();
  const nameMatches = expectedName !== '' && typed.trim() === expectedName;
  const canConfirm = status === 'ready' && nameMatches && !deleting;

  const handleConfirm = async () => {
    if (!canConfirm) return;
    setDeleting(true); setError('');
    try { await onConfirm(); onClose(); }
    catch (e) { setError(humanizeError(e).message); setDeleting(false); }
  };

  const items = groupImpactItems(summary);
  const total = items.reduce((sum, item) => sum + item.value, 0);

  return (
    <ModalWrapper
      isOpen={true}
      onClose={onClose}
      title={`Elimina gruppo — ${group.name}`}
      icon="🗑️"
      size="md"
      busy={deleting}
      footer={
        <ModalButton variant="secondary" onClick={onClose} disabled={deleting}>Annulla</ModalButton>
      }
    >
      {error && <div className="um__error">{error}</div>}
      <div className="um__cdg-warn">
        ⚠️ Questa operazione è <strong>irreversibile</strong>. Tutti i dati del gruppo saranno eliminati permanentemente.
      </div>
      {status === 'loading' && <div className="um__cdg-loading">Caricamento del riepilogo...</div>}
      {status === 'error' && (
        <div className="um__error">
          Impossibile caricare il riepilogo del gruppo ({summaryResource.error}): senza sapere cosa verrà eliminato non si può confermare.{' '}
          <button type="button" className="um__btn um__btn--sm" onClick={() => summaryResource.reload()}>Riprova</button>
        </div>
      )}
      {status === 'ready' && (
        <>
          <div className="um__cdg-section-label">Verrà eliminato</div>
          <div className="um__cdg-list">
            {items.map(({ key, icon, label, value }) => (
              <div key={key} className="um__cdg-item">
                <span className="um__cdg-item-label">{icon} {label}</span>
                <span style={{ fontFamily: 'var(--plen-font-mono)', fontSize: 12, fontWeight: 700, color: value > 0 ? 'var(--plen-err)' : 'var(--plen-text-dim)', background: value > 0 ? 'rgba(248,113,113,0.1)' : 'var(--plen-surface-3)', border: `1px solid ${value > 0 ? 'rgba(248,113,113,0.25)' : 'var(--plen-border)'}`, borderRadius: 4, padding: '1px 8px' }}>{value}</span>
              </div>
            ))}
          </div>
          {total === 0 && <div className="um__cdg-empty-note">Il gruppo è vuoto.</div>}
          <div className="um__cdg-extra-note">+ log di esecuzione, dati intermedi e risultati delle pipeline</div>
        </>
      )}
      <div className="um__cdg-confirm-line">
        Stai eliminando il gruppo <strong className="um__cdg-confirm-strong">{group.name}</strong> ({group.group_id}). Scrivi il nome del gruppo per confermare.
      </div>
      <div className="um__cdg-input-wrap">
        <input
          value={typed}
          onChange={e => setTyped(e.target.value)}
          placeholder={group.name}
          aria-label="Nome del gruppo da eliminare"
          disabled={status !== 'ready' || deleting}
          autoFocus
          style={{ background: 'var(--um-surface-2)', border: `1px solid ${nameMatches ? 'var(--um-err)' : 'var(--um-border)'}`, borderRadius: 7, padding: '8px 11px', color: 'var(--um-text)', fontSize: 13, fontFamily: 'var(--um-font-m)', outline: 'none', width: '100%' }}
        />
        <button type="button" className="um__btn um__btn--danger" onClick={handleConfirm} disabled={!canConfirm} style={{ opacity: canConfirm ? 1 : 0.4, cursor: canConfirm ? 'pointer' : 'not-allowed' }}>
          {deleting ? '⏳ Eliminazione in corso...' : '🗑️ Elimina definitivamente'}
        </button>
      </div>
    </ModalWrapper>
  );
}
export default function UserManager() {
  const navigate    = useNavigate();
  const { user: currentUser } = useAuth();
  const toast = useToast();

  const [tab,     setTab]     = useState('users');
  const search = useSearch();

  const [modalUser,    setModalUser]    = useState(null);
  const [modalGroup,   setModalGroup]   = useState(false);
  const [resetUser,    setResetUser]    = useState(null);
  const [deleteTarget, setDeleteTarget] = useState(null);

  const isSuperadmin = role.isSuperadmin(currentUser);
  const isAdmin      = can.manageUsers(currentUser);
  // Guard della rotta: /admin/users e' protetta da <AdminRoute> in App.jsx
  // che fa il redirect PRIMA del mount. Qui nessun check ridondante.

  // Fase 5 T2 (R4.7, R4.8): utenti e gruppi dall'hook comune; spinner a
  // pagina intera solo al primo caricamento, errore con «Riprova».
  const resource = useResource(async () => {
    const [u, g] = await Promise.all([listUsers(), listGroups()]);
    return { users: Array.isArray(u) ? u : u.users || [], groups: Array.isArray(g) ? g : [] };
  }, { key: 'users-groups' });
  const users = resource.data?.users || [];
  const groups = resource.data?.groups || [];
  const loading = resource.initialLoading;
  const loadData = () => resource.reload();

  const filteredUsers = search.filter(users, u => [u.username, u.email, u.group_id]);

  const handleCreateUser  = async (data) => { await createUser(data); toast.success(`Utente "${data.username}" creato`); loadData(); };
  const handleUpdateUser  = async (id, data) => { await updateUser(id, data); toast.success('Utente aggiornato'); loadData(); };
  const handleDeleteUser  = async (user) => { await deleteUser(user.id); toast.success(`Utente "${user.username}" eliminato`); loadData(); };
  const handleResetPassword = async (userId, newPassword) => { await resetPassword(userId, { new_password: newPassword }); toast.success('Password reimpostata'); };
  const handleCreateGroup = async (data) => { await createGroup(data); toast.success(`Gruppo "${data.name}" creato`); loadData(); };
  const handleDeleteGroup = async (group) => {
    // deleteGroup restituisce il report del server (cosa e' stato eliminato).
    const res = await deleteGroup(group.group_id);
    toast.success(`Gruppo "${group.name}" eliminato${groupReportText(res?.report)}`);
    loadData();
  };

  if (loading) return (
    <PageLayout className="um" title="Utenti e gruppi">
      <div className="um__loading-inner">
        <div className="um__loading-icon">👥</div>
        <div>Caricamento...</div>
      </div>
    </PageLayout>
  );

  return (
    <PageLayout
      className="um"
      title="Utenti e gruppi"
      subtitle={role.isSuperadmin(currentUser) ? 'Tutti i gruppi' : 'Utenti del tuo gruppo'}
    >
        {resource.status === 'error' && (
          <ResourceError resource={resource} title="Utenti e gruppi non caricati" />
        )}
        <div className="um__topbar">
          {/* Tab switcher Utenti / Gruppi (sostituisce le voci nella sidebar) */}
          <div className="um__tabs">
            <button
              type="button"
              className={`um__tab${tab === 'users' ? ' um__tab--active' : ''}`}
              onClick={() => setTab('users')}
            >
              👥 Utenti
            </button>
            {can.manageGroups(currentUser) && (
              <button
                type="button"
                className={`um__tab${tab === 'groups' ? ' um__tab--active' : ''}`}
                onClick={() => setTab('groups')}
              >
                🏢 Gruppi
              </button>
            )}
          </div>
          <span className="um__topbar-badge">
            {tab === 'users' ? `${filteredUsers.length} utenti` : tab === 'groups' ? `${groups.length} gruppi` : ''}
          </span>
          {tab === 'users' && (
            <SearchField search={search} label="Cerca utente o gruppo" className="um__topbar-search" shown={filteredUsers.length} total={users.length} />
          )}
          {tab === 'users' && (
            <button className="um__btn um__btn--primary" onClick={() => setModalUser('new')}>+ Nuovo utente</button>
          )}
          {tab === 'groups' && can.manageGroups(currentUser) && (
            <button className="um__btn um__btn--primary" onClick={() => setModalGroup(true)}>+ Nuovo gruppo</button>
          )}
        </div>

        <div className="um__content">
{tab === 'users' && (
            <div className="um__table-wrap">
              <table className="um__table">
                <thead>
                  <tr>
                    <th>Utente</th>
                    <th>Ruolo</th>
                    {isSuperadmin && <th>Gruppo</th>}
                    <th>Stato</th>
                    <th className="um__th-actions">Azioni</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredUsers.length === 0 ? (
                    <tr><td colSpan={isSuperadmin ? 5 : 4}>{search.active
                      ? <SearchEmpty search={search} shown={0} />
                      : <EmptyState icon="👥" title="Nessun utente" />}</td></tr>
                  ) : filteredUsers.map(u => (
                    <tr key={u.id}>
                      <td>
                        <div className="um__avatar-cell">
                          <UserAvatar username={u.username} role={u.role} />
                          <div><div className="um__cell-name">{u.username}</div><div className="um__cell-sub">{u.id}</div></div>
                        </div>
                      </td>
                      <td><RoleBadge role={u.role} /></td>
                      {isSuperadmin && <td><span className="um__cell-group">{u.group_id}</span></td>}
                      <td><StatusBadge active={u.is_active} /></td>
                      <td>
                        <div className="um__row-actions">
                          {u.id !== currentUser?.user_id && (
                            <>
                              <button className="um__action-btn" title="Modifica" onClick={() => setModalUser(u)}>✏️</button>
                              <button className="um__action-btn" title="Reset password" onClick={() => setResetUser(u)}>🔑</button>
                              <button className="um__action-btn um__action-btn--danger" title="Elimina" onClick={() => setDeleteTarget({ type: 'user', item: u })}>🗑️</button>
                            </>
                          )}
                          {u.id === currentUser?.user_id && <span className="um__row-self">tu</span>}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
{tab === 'groups' && (
            <div className="um__groups-grid">
              {groups.length === 0 ? (
                <EmptyState className="um__empty--full" icon="🏢" title="Nessun gruppo trovato" />
              ) : groups.map(g => {
                const groupUsers = users.filter(u => u.group_id === g.group_id);
                return (
                  <div key={g.group_id} className="um__group-card">
                    <div className="um__group-card-header">
                      <div className="um__group-icon">🏢</div>
                      <div><div className="um__group-name">{g.name}</div><div className="um__group-id">{g.group_id}</div></div>
                    </div>
                    {g.description && <div className="um__group-desc">{g.description}</div>}
                    <div className="um__group-stats">
                      <span className="um__group-stat">👥 {groupUsers.length} utenti</span>
                      <span className="um__group-stat">🔑 {groupUsers.filter(role.isAdminOrAbove).length} admin</span>
                    </div>
                    <div className="um__group-actions">
                      {can.deleteGroup(currentUser, g) && (
                        <button className="um__btn um__btn--danger um__btn--sm" onClick={() => setDeleteTarget({ type: 'group', item: g })}>🗑️ Elimina</button>
                      )}
                      {g.group_id === 'default' && <span className="um__group-default-hint">Gruppo di default — non eliminabile</span>}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
{(modalUser === 'new' || (modalUser && typeof modalUser === 'object')) && (
        <UserModal user={modalUser === 'new' ? null : modalUser} groups={groups} currentUser={currentUser}
          onClose={() => setModalUser(null)}
          onSave={async (data) => { if (modalUser === 'new') await handleCreateUser(data); else await handleUpdateUser(modalUser.id, data); }} />
      )}
      {resetUser && <ResetPasswordModal user={resetUser} onClose={() => setResetUser(null)} onSave={(pwd) => handleResetPassword(resetUser.id, pwd)} />}
      {modalGroup && <GroupModal onClose={() => setModalGroup(false)} onSave={handleCreateGroup} />}
      {deleteTarget?.type === 'user' && (
        <ConfirmDeleteModal title="Elimina utente" message={`Sei sicuro di voler eliminare l'utente "${deleteTarget.item.username}"? L'operazione non è reversibile.`}
          onClose={() => setDeleteTarget(null)} onConfirm={() => handleDeleteUser(deleteTarget.item)} />
      )}
      {deleteTarget?.type === 'group' && (
        <ConfirmDeleteGroupModal group={deleteTarget.item} onClose={() => setDeleteTarget(null)} onConfirm={() => handleDeleteGroup(deleteTarget.item)} />
      )}
    </PageLayout>
  );
}
