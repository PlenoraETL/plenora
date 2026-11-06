import EmptyState from '../common/EmptyState';
import React, { useRef, useState } from 'react';
import { useSharedResource } from '../../hooks/useSharedResource';
import { connectionSetsOf } from '../../utils/sharedSelectors';
import { ResourceError } from '../common/ListState';
import {
  createConnectionSet,
  deleteConnectionSet,

  updateConnectionSet,
} from '../../api/connections';
import ModalWrapper, { ModalButton } from '../common/ModalWrapper';
import { invalidateConnectionSets } from '../../utils/connectionSets';
import { toastApiError } from '../../utils/toastApiError';
import FieldInput, { FieldScope } from '../common/FieldInput';
import { useFieldScope } from '../../hooks/useFieldBuffer';
import * as codecs from '../../utils/fieldCodecs';
import './ConnectionSetsPanel.css';
import { describeConnectionTest } from '../../utils/connectionTestStatus';

const emptyForm = () => ({
  name: '',
  description: '',
  selector_label: 'Cliente',
  members: [],
});

// Fase 5 T5 (R8.2): la chiave e' un campo con buffer locale (FieldInput):
// si normalizza al commit, non a ogni tasto, quindi "_", "-" e "." finali
// restano mentre si scrive. Stessa regola del backend
// (connections/models.ConnectionSetMemberInput: ^[A-Za-z0-9][A-Za-z0-9_.-]*$,
// max 120). Ogni riga ha un id stabile: il focus non si perde.
const KEY_RULE = { allowed: '_.-', startsWithLetter: false };
const SELECTOR_KEY = codecs.key({ ...KEY_RULE, maxLength: 120, optional: true });
const keyFromLabel = label => codecs.slugify(label, KEY_RULE).slice(0, 120);

// canEdit = can.createConnection, canEditItem = can.editConnection sulla
// riga (ConnectionManager): le azioni non permesse non si mostrano.
function ConnectionSetsPanel({ connections, canEdit, canEditItem = () => false, toast, confirm }) {
  // Fase 5 T2 (R4.8): elenco dall'hook comune, errore con «Riprova» (mai
  // "Nessun Connection Set configurato" per un errore).
  // UI-1: la stessa risorsa condivisa dei nodi (utils/sharedResources).
  const resource = useSharedResource('connectionSets', { select: connectionSetsOf });
  const sets = resource.data || [];
  const loading = resource.initialLoading;
  const load = () => resource.reload();
  const [editing, setEditing] = useState(null);
  const [showEditor, setShowEditor] = useState(false);
  const [form, setForm] = useState(emptyForm);
  const [saving, setSaving] = useState(false);
  const fields = useFieldScope();
  const rowSeq = useRef(0);
  const nextRowId = () => { rowSeq.current += 1; return `m${rowSeq.current}`; };


  const open = item => {
    setShowEditor(true);
    setEditing(item || null);
    setForm(item ? {
      name: item.name,
      description: item.description || '',
      selector_label: item.selector_label || 'Cliente',
      // Chiave esistente: resta quella (keyAuto false).
      members: (item.members || []).map(member => ({ ...member, _rid: nextRowId(), keyAuto: false })),
    } : emptyForm());
  };

  const close = () => {
    if (saving) return;
    setEditing(null);
    setShowEditor(false);
    setForm(emptyForm());
  };

  const addMember = () => setForm(current => ({
    ...current,
    members: [...current.members, {
      _rid: nextRowId(),
      // La chiave segue l'etichetta finche' non la si scrive a mano.
      keyAuto: true,
      selector_key: '',
      label: '',
      connection_id: connections[0]?.id || '',
      active: true,
      sort_order: current.members.length,
    }],
  }));

  const updateMember = (index, patch) => setForm(current => ({
    ...current,
    members: current.members.map((member, memberIndex) => (
      memberIndex === index ? { ...member, ...patch } : member
    )),
  }));

  const removeMember = index => setForm(current => ({
    ...current,
    members: current.members.filter((_member, memberIndex) => memberIndex !== index),
  }));

  const save = async () => {
    if (!form.name.trim()) {
      toast.warning('Il nome del Connection Set è obbligatorio');
      return;
    }
    if (!fields.check()) {
      toast.warning('Correggi le chiavi segnate');
      return;
    }
    const invalid = form.members.find(member => (
      !member.selector_key || !member.label.trim() || !member.connection_id
    ));
    if (invalid) {
      toast.warning('Completa chiave, etichetta e connessione di ogni cliente');
      return;
    }
    const keys = form.members.map(member => member.selector_key);
    const duplicate = keys.find((k, i) => keys.indexOf(k) !== i);
    if (duplicate) {
      toast.warning(`La chiave «${duplicate}» è usata da più clienti`);
      return;
    }
    setSaving(true);
    try {
      const payload = {
        ...form,
        name: form.name.trim(),
        selector_label: form.selector_label.trim() || 'Cliente',
        members: form.members.map((member, index) => ({
          selector_key: member.selector_key,
          label: member.label.trim(),
          connection_id: member.connection_id,
          active: member.active !== false,
          sort_order: index,
        })),
      };
      if (editing) await updateConnectionSet(editing.id, payload);
      else await createConnectionSet(payload);
      invalidateConnectionSets();
      setEditing(null);
      setShowEditor(false);
      setForm(emptyForm());
      await load();
      toast.success(editing ? 'Connection Set aggiornato' : 'Connection Set creato');
    } catch (error) {
      toastApiError(toast, error, 'Errore salvataggio Connection Set');
    } finally {
      setSaving(false);
    }
  };

  const remove = async item => {
    const accepted = await confirm({
      title: 'Elimina Connection Set',
      message: `Eliminare “${item.name}”? I progetti che lo usano dovranno essere riconfigurati.`,
      confirmText: 'Elimina',
      variant: 'danger',
    });
    if (!accepted) return;
    try {
      await deleteConnectionSet(item.id);
      invalidateConnectionSets();
      await load();
    } catch (error) {
      toastApiError(toast, error, 'Errore eliminazione Connection Set');
    }
  };

  return (
    <section className="cm__section cset">
      <div className="cm__section-header cset__header">
        <div>
          <div className="cm__section-title">Connection Set</div>
          <div className="cm__section-sub">
            Instradamento sicuro cliente → connessione per i progetti Estrai
          </div>
        </div>
        {canEdit && (
          <button type="button" className="cm__btn cm__btn--primary" onClick={() => open(null)}>
            ＋ Nuovo set
          </button>
        )}
      </div>

      {/* Errore sempre visibile: anche sopra i dati gia' caricati. */}
      <ResourceError resource={resource} title="Connection Set non caricati" />

      {loading ? (
        <div className="cset__empty">Caricamento set…</div>
      ) : resource.status === 'error' && !resource.hasData ? null : sets.length === 0 ? (
        // S3, G2: stato vuoto centrato con l'azione, come le altre schede.
        <EmptyState
          icon="🧩"
          title="Nessun Connection Set configurato"
          text="Un Connection Set instrada ogni cliente alla sua connessione nei progetti Estrai."
          action={canEdit && (
            <button type="button" className="cm__btn cm__btn--primary" onClick={() => open(null)}>＋ Nuovo set</button>
          )}
        />
      ) : (
        <div className="cset__grid">
          {sets.map(item => (
            <article className="cset__card" key={item.id}>
              <div>
                <strong>{item.name}</strong>
                <p>{item.description || `Selettore: ${item.selector_label}`}</p>
              </div>
              <span className="cset__count">
                {(item.members || []).filter(member => member.active).length} connessioni
              </span>
              {canEditItem(item) && (
                <div className="cset__actions">
                  <button type="button" onClick={() => open(item)}>Modifica</button>
                  <button type="button" className="cset__danger" onClick={() => remove(item)}>Elimina</button>
                </div>
              )}
            </article>
          ))}
        </div>
      )}

      {showEditor && (
        <ModalWrapper
          isOpen
          onClose={close}
          title={editing ? 'Modifica Connection Set' : 'Nuovo Connection Set'}
          icon="⇄"
          size="lg"
          busy={saving}
          footer={(
            <>
              <ModalButton variant="secondary" onClick={close} disabled={saving}>Annulla</ModalButton>
              <ModalButton variant="primary" onClick={save} loading={saving}>Salva set</ModalButton>
            </>
          )}
        >
          <div className="cset__form-grid">
            <label>
              <span>Nome *</span>
              <input value={form.name} onChange={event => setForm(current => ({ ...current, name: event.target.value }))} placeholder="Database clienti" />
            </label>
            <label>
              <span>Etichetta selettore</span>
              <input value={form.selector_label} onChange={event => setForm(current => ({ ...current, selector_label: event.target.value }))} placeholder="Cliente" />
            </label>
          </div>
          <label className="cset__wide-field">
            <span>Descrizione</span>
            <input value={form.description} onChange={event => setForm(current => ({ ...current, description: event.target.value }))} />
          </label>

          <div className="cset__members-head">
            <div>
              <strong>Clienti e connessioni</strong>
              <small>La chiave è il valore inviato dall’input runtime.</small>
            </div>
            <button type="button" className="cm__btn" onClick={addMember}>＋ Aggiungi cliente</button>
          </div>
          <FieldScope scope={fields}>
          <div className="cset__members">
            {form.members.map((member, index) => (
              <div className="cset__member" key={member._rid}>
                <input
                  value={member.label}
                  placeholder="Etichetta cliente"
                  aria-label="Etichetta cliente"
                  onChange={event => {
                    const label = event.target.value;
                    updateMember(index, member.keyAuto
                      ? { label, selector_key: keyFromLabel(label) }
                      : { label });
                  }}
                />
                <span className="cset__key-field">
                  <FieldInput
                    codec={SELECTOR_KEY}
                    value={member.selector_key}
                    placeholder="chiave_cliente"
                    aria-label="Chiave cliente"
                    onCommit={selectorKey => updateMember(index, { selector_key: selectorKey, keyAuto: false })}
                  />
                </span>
                <select
                  value={member.connection_id}
                  onChange={event => updateMember(index, { connection_id: event.target.value })}
                >
                  <option value="">Seleziona connessione…</option>
                  {connections.map(connection => (
                    <option key={connection.id} value={connection.id}>
                      {connection.name} · {connection.type} · {describeConnectionTest(connection.last_test).label}
                    </option>
                  ))}
                </select>
                <label className="cset__active">
                  <input
                    type="checkbox"
                    checked={member.active !== false}
                    onChange={event => updateMember(index, { active: event.target.checked })}
                  />
                  Attivo
                </label>
                <button type="button" className="cset__remove" onClick={() => removeMember(index)}>×</button>
              </div>
            ))}
            {form.members.length === 0 && (
              <div className="cset__empty">Aggiungi almeno un cliente al set.</div>
            )}
          </div>
          </FieldScope>
        </ModalWrapper>
      )}
    </section>
  );
}

export default ConnectionSetsPanel;
