/**
 * executionConfig — quando "Esegui" deve chiedere qualcosa prima di partire
 * (Fase 4, M7). Regola unica per i tre editor: solo se ci sono input
 * runtime da compilare o se l'utente puo' scegliere le opzioni staff
 * (intermediate_mode, superadmin). Altrimenti un clic e parte.
 */
import { role } from './permissions';

export const executionNeedsConfig = (inputDefinitions = [], user = null) =>
  (inputDefinitions || []).length > 0 || role.isSuperadmin(user);
