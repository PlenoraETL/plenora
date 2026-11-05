/**
 * AllowedGroupsSelector.jsx — Selettore riutilizzabile per `allowed_groups`.
 *
 * Permette ai superadmin di gestire il cross-group sharing di una risorsa
 * (connections, datasources, eseguibili, api_connections, arcgis_connections,
 * file_sources).
 *
 * Props:
 *   - resourceType: string — uno di VALID_RESOURCE_TYPES
 *   - resourceId:   string — id della risorsa (se assente/falsy il componente
 *                            si mostra in stato "salva prima per abilitare")
 *   - disabled?:    boolean — se true, sola lettura
 *   - embedded?:    boolean — true quando il componente è dentro un modal:
 *                             nasconde titolo/hint/bordo per evitare
 *                             "card-in-card" e duplicazione di titoli.
 *   - onClose?:     function — callback opzionale chiamata dopo un save
 *                              riuscito (utile per chiudere il modal padre).
 */
import React, { useState, useEffect, useCallback, useMemo } from 'react';
import { getShares, setShares } from '../../api/shares';
import { listGroups } from '../../api/users';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../hooks/useToast';
import { can } from '../../utils/permissions';
import { humanizeError, requestErrorMessage } from '../../utils/errorMessages';
import './AllowedGroupsSelector.css';

const MAGIC_ALL = '*';

// Solo superadmin può gestire la condivisione cross-gruppo. Il controllo sta
// in un wrapper SENZA altri hook: un `return null` prima degli hook del
// pannello violava le Rules of Hooks; se lo stesso montaggio passava da
// "senza permesso" a "con permesso" React lanciava "Rendered more hooks"
// (audit R5.6).
function AllowedGroupsSelector(props) {
  const { user } = useAuth();
  if (!can.shareResources(user)) return null;
  return <AllowedGroupsSelectorPanel {...props} />;
}

function AllowedGroupsSelectorPanel({
  resourceType,
  resourceId,
  disabled = false,
  embedded = false,
  onClose = null,
}) {
  const toast = useToast();

  const [loading, setLoading] = useState(false);
  const [saving, setSaving]   = useState(false);
  const [error, setError]     = useState(null);

  const [allGroups, setAllGroups]             = useState([]);
  const [initialSelected, setInitialSelected] = useState([]); // snapshot da backend
  const [selected, setSelected]               = useState([]);   // stato corrente editabile
  const [allChecked, setAllChecked]           = useState(false);
  const [search, setSearch]                   = useState('');
  // Le condivisioni attuali devono essere note prima di poterle modificare:
  // se getShares fallisce, una selezione vuota salvata sostituirebbe quelle
  // esistenti (audit R10.6). Finche' false, il pannello e' in sola lettura.
  const [sharesLoaded, setSharesLoaded]       = useState(false);

  // Gruppo proprietario della RISORSA, dal server (risposta delle
  // condivisioni): non il gruppo dell'utente, che per un superadmin e' un
  // altro (audit R10.7). Finche' non e' noto il pannello e' bloccato.
  const [ownerGroupId, setOwnerGroupId]       = useState(null);

  const selectableGroups = useMemo(
    () => (allGroups || []).filter(g => g.group_id !== ownerGroupId && g.group_id !== MAGIC_ALL),
    [allGroups, ownerGroupId]
  );

  const filteredGroups = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return selectableGroups;
    return selectableGroups.filter(g =>
      (g.name || '').toLowerCase().includes(q) ||
      (g.group_id || '').toLowerCase().includes(q)
    );
  }, [selectableGroups, search]);

  const load = useCallback(async () => {
    if (!resourceType || !resourceId) return;
    setLoading(true);
    setError(null);
    setSharesLoaded(false);
    try {
      // Promise.allSettled: gestiamo entrambi gli errori in modo coerente.
      // Prima il code path catch silenzioso su listGroups + throw su getShares
      // produceva stati incoerenti (gruppi vuoti senza errore visibile, oppure
      // errore globale che oscurava un fail parziale).
      const [groupsRes, sharesRes] = await Promise.allSettled([
        listGroups(),
        getShares(resourceType, resourceId),
      ]);

      // Gruppi: se fallisce, mostriamo l'errore ma non blocchiamo la UI
      if (groupsRes.status === 'fulfilled') {
        setAllGroups(Array.isArray(groupsRes.value) ? groupsRes.value : []);
      } else {
        console.error('[AllowedGroupsSelector] listGroups fallita:', groupsRes.reason);
        setAllGroups([]);
        setError(`Caricamento gruppi fallito: ${humanizeError(groupsRes.reason).message}`);
      }

      // REGOLA (Fase 5, correzione dopo la conferma): il pannello si sblocca
      // SOLO con le condivisioni attuali E il gruppo proprietario noto dal
      // server. Il 404 del backend vuol dire "risorsa non trovata" (shares
      // router), non "nessuna condivisione"; una risposta senza
      // owner_group_id non dice quale gruppo escludere: in entrambi i casi il
      // pannello resta bloccato con l'errore e «Riprova».
      const ownerFromServer = sharesRes.status === 'fulfilled' ? (sharesRes.value?.owner_group_id || null) : null;
      if (sharesRes.status === 'fulfilled' && ownerFromServer) {
        const ids = Array.isArray(sharesRes.value?.group_ids) ? sharesRes.value.group_ids : [];
        const hasAll = ids.includes(MAGIC_ALL);
        setAllChecked(hasAll);
        setSelected(ids.filter(id => id !== MAGIC_ALL));
        setInitialSelected(ids);
        setOwnerGroupId(ownerFromServer);
        setSharesLoaded(true);
      } else if (sharesRes.status === 'fulfilled') {
        setOwnerGroupId(null);
        setError(prev => prev || 'Gruppo proprietario della risorsa non noto: la condivisione resta bloccata.');
      } else if (sharesRes.reason?.response?.status === 404) {
        // Lo status HTTP e' l'unico segnale affidabile: l'interceptor
        // riscrive `message` con il testo del backend.
        setOwnerGroupId(null);
        setError(prev => prev || 'Risorsa non trovata: la condivisione resta bloccata.');
      } else {
        console.error('[AllowedGroupsSelector] getShares fallita:', sharesRes.reason);
        setError(prev => prev || `Caricamento condivisioni fallito: ${humanizeError(sharesRes.reason).message}`);
      }
    } finally {
      setLoading(false);
    }
  }, [resourceType, resourceId]);

  useEffect(() => { load(); }, [load]);

  const locked = disabled || saving || !sharesLoaded;

  const toggleGroup = (groupId) => {
    if (locked) return;
    setSelected(prev =>
      prev.includes(groupId) ? prev.filter(g => g !== groupId) : [...prev, groupId]
    );
  };

  const handleSelectAllVisible = () => {
    if (locked) return;
    const visibleIds = filteredGroups.map(g => g.group_id);
    const allVisibleSelected = visibleIds.every(id => selected.includes(id));
    if (allVisibleSelected) {
      setSelected(prev => prev.filter(id => !visibleIds.includes(id)));
    } else {
      setSelected(prev => Array.from(new Set([...prev, ...visibleIds])));
    }
  };

  const handleToggleAll = (checked) => {
    if (locked) return;
    setAllChecked(checked);
  };

  const handleCancel = () => {
    const hasAll = initialSelected.includes(MAGIC_ALL);
    setAllChecked(hasAll);
    setSelected(initialSelected.filter(id => id !== MAGIC_ALL));
    setError(null);
  };

  const handleSave = async () => {
    if (locked) return;
    setSaving(true);
    setError(null);
    try {
      const payload = allChecked ? [MAGIC_ALL] : selected;
      await setShares(resourceType, resourceId, payload);
      setInitialSelected(payload);
      toast.success('Condivisione aggiornata');
      if (onClose) onClose();
    } catch (e) {
      const msg = requestErrorMessage(e, 'Errore salvataggio condivisione');
      setError(msg);
      toast.error(msg);
    } finally {
      setSaving(false);
    }
  };

  // Se non c'è ancora l'id, mostriamo un placeholder
  if (!resourceId) {
    return (
      <div className={`ags ags--placeholder${embedded ? ' ags--embedded' : ''}`}>
        {!embedded && <div className="ags__title">Condivisione cross-gruppo</div>}
        <div className="ags__hint">
          Salva prima la risorsa per poter configurare la condivisione con altri gruppi.
        </div>
      </div>
    );
  }

  const dirty =
    allChecked !== initialSelected.includes(MAGIC_ALL) ||
    JSON.stringify([...selected].sort()) !==
      JSON.stringify([...initialSelected.filter(id => id !== MAGIC_ALL)].sort());

  const visibleSelectedCount = filteredGroups.filter(g => selected.includes(g.group_id)).length;
  const totalSelected = allChecked ? selectableGroups.length : selected.length;

  return (
    <div className={`ags${embedded ? ' ags--embedded' : ''}`}>
      {!embedded && (
        <>
          <div className="ags__title">Condivisione cross-gruppo</div>
          <div className="ags__hint">
            I membri dei gruppi selezionati potranno leggere e usare questa risorsa,
            ma non modificarla o eliminarla.
          </div>
        </>
      )}

      {loading ? (
        <div className="ags__loading">Caricamento gruppi…</div>
      ) : (
        <>
          {error && (
            <div className="ags__error" role="alert">
              {error}
              {!sharesLoaded && (
                <>
                  {' '}
                  <button type="button" className="ags__retry" onClick={load}>Riprova</button>
                </>
              )}
            </div>
          )}

          {/* Toggle "tutti i gruppi" */}
          <label className={`ags__all-toggle${allChecked ? ' ags__all-toggle--on' : ''}`}>
            <input
              type="checkbox"
              checked={allChecked}
              disabled={locked}
              onChange={(e) => handleToggleAll(e.target.checked)}
            />
            <div className="ags__all-toggle-text">
              <strong>Condividi con tutti i gruppi</strong>
              <span>Ogni gruppo presente e futuro avrà accesso in lettura.</span>
            </div>
          </label>

          {/* La lista è sempre visibile. Quando "tutti i gruppi" è attivo, viene
              dimmata e resa informativa: l'utente capisce che può deselezionarla
              per scegliere singoli gruppi invece di far sparire l'intera sezione. */}
          <div className={`ags__list-section${allChecked ? ' ags__list-section--muted' : ''}`}>
            {allChecked && (
              <div className="ags__list-hint">
                Deseleziona "Condividi con tutti i gruppi" per scegliere singoli gruppi.
              </div>
            )}

            {/* Header lista: contatore + select-all visibili */}
            <div className="ags__list-header">
              <div className="ags__list-counter">
                {allChecked
                  ? 'Tutti i gruppi hanno accesso'
                  : totalSelected === 0
                    ? 'Nessun gruppo selezionato'
                    : `${totalSelected} ${totalSelected === 1 ? 'gruppo selezionato' : 'gruppi selezionati'}`}
              </div>
              {selectableGroups.length > 0 && (
                <button
                  type="button"
                  className="ags__list-toggle-all"
                  onClick={handleSelectAllVisible}
                  disabled={locked || allChecked || filteredGroups.length === 0}
                >
                  {filteredGroups.length > 0 &&
                   filteredGroups.every(g => selected.includes(g.group_id))
                    ? 'Deseleziona visibili'
                    : 'Seleziona visibili'}
                </button>
              )}
            </div>

            {/* Search */}
            {selectableGroups.length > 5 && (
              <input
                type="text"
                className="ags__search"
                placeholder="Cerca gruppo per nome…"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                disabled={locked || allChecked}
              />
            )}

            {/* Lista */}
            <div className="ags__list">
              {selectableGroups.length === 0 ? (
                <div className="ags__empty">
                  Nessun altro gruppo disponibile.
                </div>
              ) : filteredGroups.length === 0 ? (
                <div className="ags__empty">
                  Nessun gruppo corrisponde a "{search}".
                </div>
              ) : (
                filteredGroups.map(g => {
                  const checked = selected.includes(g.group_id);
                  return (
                    <label
                      key={g.group_id}
                      className={`ags__item${checked ? ' ags__item--on' : ''}${allChecked ? ' ags__item--disabled' : ''}`}
                      title={g.group_id}
                    >
                      <input
                        type="checkbox"
                        checked={checked}
                        disabled={locked || allChecked}
                        onChange={() => toggleGroup(g.group_id)}
                      />
                      <div className="ags__item-body">
                        <div className="ags__item-name">{g.name || g.group_id}</div>
                        {g.name && g.name !== g.group_id && (
                          <div className="ags__item-id">{g.group_id}</div>
                        )}
                      </div>
                      {checked && <span className="ags__item-check">✓</span>}
                    </label>
                  );
                })
              )}
            </div>
          </div>

          {!disabled && (
            <div className="ags__actions">
              <button
                type="button"
                className="ags__btn ags__btn--ghost"
                onClick={handleCancel}
                disabled={locked || !dirty}
              >
                Annulla
              </button>
              <button
                type="button"
                className="ags__btn ags__btn--primary"
                onClick={handleSave}
                disabled={locked || !dirty}
              >
                {saving ? 'Salvataggio…' : 'Salva condivisione'}
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
}

export default AllowedGroupsSelector;
