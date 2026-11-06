/**
 * editorUpload — «＋ Carica un file» dagli editor Manipola e Arricchisci
 * (UI-3 U30; conferma UI B-K8, C-K9). UNA regola per il menu «⋯», la barra
 * laterale e la finestra di caricamento:
 *
 *  - il comando c'e' solo se l'utente puo' caricare file (can.uploadFile),
 *    il progetto NON e' in sola lettura (prima compariva anche li', C-K9) e il
 *    progetto e' del gruppo dell'utente: il backend mette il file nel gruppo
 *    di chi carica (files/router.upload_file), quindi da un progetto di un
 *    altro gruppo (admin o superadmin) il file finirebbe dove il progetto non
 *    lo legge (B-K8). In quel caso il comando non si offre;
 *  - l'esito dice cosa e' successo davvero: «aggiunto al canvas» solo se il
 *    nodo e' entrato nel canvas, altrimenti «caricato» e basta (prima il
 *    messaggio diceva sempre «aggiunto al canvas»).
 */
import { useCallback } from 'react';
import { can } from '../../utils/permissions';

/** Il comando «Carica un file» si offre? */
export function editorUploadAllowed({ user, project, isReadOnly }) {
  const projectGroup = project?.metadata?.group_id ?? project?.group_id ?? null;
  return Boolean(can.uploadFile(user) && !isReadOnly && projectGroup && user?.group_id && projectGroup === user.group_id);
}

/** Messaggio dell'esito: `added` = il nodo e' entrato nel canvas. */
export function editorUploadMessage(file, added) {
  const name = file?.name ? `«${file.name}» ` : '';
  return added ? `File ${name}caricato e aggiunto al canvas.` : `File ${name}caricato: lo trovi tra i file della barra laterale.`;
}

/** { canUpload, onUploaded } per un editor. */
export function useEditorFileUpload({ user, project, isReadOnly, reloadSources, addSource, toast }) {
  const canUpload = editorUploadAllowed({ user, project, isReadOnly });
  const onUploaded = useCallback(async (file) => {
    await reloadSources?.();
    const node = file?.id ? addSource('file_source', file) : null;
    toast.success(editorUploadMessage(file, Boolean(node)));
  }, [reloadSources, addSource, toast]);
  return { canUpload, onUploaded };
}
