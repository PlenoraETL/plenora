/**
 * dataSourceForm.js — schema del modulo Data Source (Fase 5 T5, R8.5).
 *
 * Una fonte per rendering, validazione e payload (utils/formSchema):
 *  - per tipo solo i suoi campi: quelli di un altro tipo rimasti nello stato
 *    non si validano e non si inviano (prima `tls`, `passive`,
 *    `host_key_policy` partivano sempre, `host` e `username` dopo un cambio
 *    di tipo);
 *  - obbligatori del tipo validati (un FTP senza host non si salva);
 *  - in modifica un campo vuoto si invia vuoto e si puo' svuotare
 *    (`root_path`, `site_id`: PUT parziale con exclude_none nel backend);
 *    un segreto vuoto vuol dire "invariato" e non si invia;
 *  - il tipo non cambia in modifica (DataSourceUpdate non ha `type`).
 */
import * as codecs from './fieldCodecs';

const isType = (...types) => (values) => types.includes(values.type);
const editHint = (text) => (_values, ctx) => (ctx.mode === 'edit' ? '(invariata)' : text);

export const DS_FIELDS = [
  { name: 'name', label: 'Nome', required: true, requiredMessage: 'Il nome è obbligatorio', placeholder: 'es. SharePoint ICSC' },
  { name: 'description', label: 'Descrizione', placeholder: 'Opzionale' },
  // Locale
  { name: 'root_path', label: 'Percorso cartella', visible: isType('local'), required: true, width: 'wide', placeholder: 'C:\\dati\\estrazioni  oppure  \\\\server\\share\\dati', hint: 'Percorso assoluto locale o UNC' },
  // FTP / SFTP
  { name: 'host', label: 'Host', visible: isType('ftp', 'sftp'), required: true, placeholder: 'ftp.server.it' },
  { name: 'port', label: 'Porta', visible: isType('ftp', 'sftp'), codec: codecs.integer({ min: 1, max: 65535, optional: true }), placeholder: (v) => (v.type === 'sftp' ? '22' : '21'), clearValue: null },
  { name: 'username', label: 'Utente', visible: isType('ftp', 'sftp'), required: isType('sftp'), placeholder: (v) => (v.type === 'ftp' ? 'anonymous' : '') },
  { name: 'password', label: 'Password', visible: isType('ftp', 'sftp'), control: 'password', secret: true, placeholder: (v, ctx) => (ctx.mode === 'edit' ? '(invariata)' : v.type === 'sftp' ? 'Lascia vuoto se usi chiave SSH' : '') },
  { name: 'private_key', label: 'Chiave SSH privata (PEM)', visible: isType('sftp'), control: 'textarea', secret: true, width: 'wide', codec: codecs.text({ trim: false }), placeholder: (_v, ctx) => (ctx.mode === 'edit' ? '(invariata — lascia vuoto per non modificare)' : '-----BEGIN RSA PRIVATE KEY-----\n...') },
  { name: 'private_key_passphrase', label: 'Passphrase chiave', visible: isType('sftp'), control: 'password', secret: true, placeholder: 'Lascia vuoto se non cifrata' },
  { name: 'host_key_policy', label: 'Policy host sconosciuti', visible: isType('sftp'), control: 'select', default: 'reject', codec: codecs.oneOf(['auto_add', 'reject']), options: [{ value: 'auto_add', label: 'Accetta automaticamente' }, { value: 'reject', label: 'Rifiuta (più sicuro)' }] },
  { name: 'root_path', key: 'root_path_remote', label: 'Cartella root', visible: isType('ftp', 'sftp'), width: 'wide', placeholder: '/upload/estrazioni' },
  { name: 'tls', label: 'TLS (FTPS)', visible: isType('ftp'), control: 'checkbox', default: false },
  { name: 'passive', label: 'Modalità passiva', visible: isType('ftp'), control: 'checkbox', default: true },
  // SharePoint
  { name: 'tenant_id', label: 'Tenant ID', visible: isType('sharepoint'), required: true, width: 'wide', placeholder: 'xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx' },
  { name: 'client_id', label: 'Client ID', visible: isType('sharepoint'), required: true, placeholder: 'xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx' },
  { name: 'client_secret', label: 'Client Secret', visible: isType('sharepoint'), control: 'password', secret: true, required: (_v, ctx) => ctx.mode !== 'edit', placeholder: editHint('') },
  {
    name: 'site_url', label: 'URL Sito', visible: isType('sharepoint'), width: 'wide',
    placeholder: 'https://tenant.sharepoint.com/sites/ICSC', hint: "Usato per individuare il sito automaticamente. Serve l'URL o il Site ID.",
    check: (v) => (!String(v.site_url || '').trim() && !String(v.site_id || '').trim() ? "Inserire 'URL Sito' o 'Site ID'" : null),
  },
  { name: 'site_id', label: 'Site ID (opzionale)', visible: isType('sharepoint'), placeholder: 'Scoperto automaticamente' },
  { name: 'drive_id', label: 'Drive ID (opzionale)', visible: isType('sharepoint'), placeholder: 'Default: Document Library' },
  { name: 'root_path', key: 'root_path_sp', label: 'Percorso root', visible: isType('sharepoint'), width: 'wide', placeholder: '/sites/ICSC/Documents/estrazioni' },
];

/** Campi con nomi univoci per lo stato (root_path compare per piu' tipi). */
export const DS_STATE_FIELDS = DS_FIELDS.filter((f, i) => DS_FIELDS.findIndex(x => x.name === f.name) === i);

export const DEFAULT_PORTS = { ftp: 21, sftp: 22 };
