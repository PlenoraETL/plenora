/**
 * icons.js — UNICA tabella delle icone per significato (UI-4, U24).
 *
 * Un significato = un'icona, un'icona = un significato. Le tabelle dei nomi
 * visibili (glossario degli editor, voci di navigazione, tipi di progetto)
 * prendono l'icona da qui per significato: prima 💾 voleva dire sia «Salva»
 * sia «Carica su database» sia «Backup», 📁 sia «Output» sia «Data Source»,
 * 🔍 sia «Valida» sia «Cerca». Il test tests/shared/icons.contract.test.js
 * vieta i doppioni e le icone scritte a mano nelle tabelle.
 */
export const ICON = Object.freeze({
  // ── Tipi di progetto ─────────────────────────────────────────────────
  estrai: '↓',
  manipola: '⇆',
  arricchisci: '◈',

  // ── Oggetti dei dati ─────────────────────────────────────────────────
  table: '🗂️',
  view: '🪟',
  arcgis_layer: '🗺️',
  project_result: '📊',
  file: '📄',
  datasource: '🗄️',
  static_values: '📝',
  runtime_input: '🎛️',
  operation: '⚙️',
  join: '🔗',
  api: '📡',
  output: '🏁',
  load_db: '📤',
  load_arcgis: '🌐',
  note: '💬',
  concat: '➕',
  lookup: '🔁',
  template: '🍪',
  manual: '✏️',
  openapi: '📋',
  file_processor: '📦',

  // ── Azioni ───────────────────────────────────────────────────────────
  run: '▶',
  before_run: '🧭',
  fill_values: '⤓',
  preview: '👁️',
  validate: '🩺',
  save: '💾',
  versions: '🕘',
  run_history: '🕐',
  add: '＋',
  delete: '🗑',
  search: '🔍',
  shortcuts: '⌨️',
  // Comandi del canvas (components/editor/canvasCommands, conferma UI C-K4):
  // prima scritti a mano, con 📋 che voleva dire anche «API OpenAPI» e 🔎
  // accanto al 🔍 di «Cerca».
  select_all: '▣',
  copy: '⧉',
  paste: '📥',
  duplicate: '⎘',
  connect: '↦',
  editor_guide: '❔',
  password: '🔑',
  logout: '⏻',

  // ── Pagine e strumenti ───────────────────────────────────────────────
  dashboard: '🏠',
  connection: '🔌',
  schedule: '⏰',
  logs: '📜',
  executable: '🚀',
  ticket: '🎫',
  users: '👥',
  task_runtime: '📬',
  object_storage: '🪣',
  system_status: '📈',
  backup: '🗃️',
  runtime_config: '🔧',
});
