/**
 * editorActions — tabella delle azioni che l'editor registra nello store
 * (setEditorActions) e che i nodi/pannelli leggono (getEditorAction).
 *
 * Ogni azione ha chi la registra e chi la usa: un nome senza lettore e'
 * codice morto, un nome senza registrazione e' un pulsante che non fa
 * niente. Il test tests/components/EditorActions.contract.test.js confronta
 * questa tabella con il codice (Blocco A, regola 9). Lo store rifiuta i
 * nomi che non sono qui.
 */
export const EDITOR_ACTIONS = Object.freeze({
  // Percorso unico di validazione ed esecuzione (editor/PipelineRunner).
  onValidate: { registeredBy: ['PipelineRunner'], usedBy: ['ValidationStatusBadge', 'ResultNode'] },
  runPipeline: { registeredBy: ['PipelineRunner'], usedBy: ['EditorToolbar', 'ResultNode'] },
  previewWithValidation: { registeredBy: ['PipelineRunner'], usedBy: ['ResultNode'] },
  showValidationResult: { registeredBy: ['PipelineRunner'], usedBy: ['ResultNode'] },
  // Dall'editor al percorso unico.
  onExecute: { registeredBy: ['EstraiEditor', 'ManipolaEditor', 'ArricchisciEditor'], usedBy: ['PipelineRunner', 'ResultNode'] },
  onPreview: { registeredBy: ['EstraiEditor', 'ManipolaEditor', 'ArricchisciEditor'], usedBy: ['PipelineRunner', 'ResultNode'] },
  // Guscio dell'editor.
  confirmSaveBeforeRead: { registeredBy: ['EditorShell'], usedBy: ['serverActions'] },
  openPalette: { registeredBy: ['EditorShell'], usedBy: ['CompactNode'] },
  // Specifiche di un editor.
  onAddRelatedLayer: { registeredBy: ['EstraiEditor'], usedBy: ['FeatureLayerNode'] },
  onUpdateTransform: { registeredBy: ['ManipolaEditor'], usedBy: ['TransformNode'] },
  onDataSourceSchemaUpdated: { registeredBy: ['ManipolaEditor'], usedBy: ['DataSourceNode'] },
  onCanvasRestored: { registeredBy: ['ManipolaEditor'], usedBy: ['useLocalDraft'] },
});

export const EDITOR_ACTION_NAMES = new Set(Object.keys(EDITOR_ACTIONS));
