/**
 * useCaricaSchemaSelect — schemi e tabelle della destinazione DB del nodo
 * Carica.
 *
 * Fase 5 T2 (R4.17): dall'hook comune hooks/useResource. Connessione o
 * schema cambiati = nuova richiesta (la risposta di prima non scrive);
 * un errore e' esposto (`schemasError`, `tablesError`, `reloadSchemas`,
 * `reloadTables`) e la sezione lo mostra con «Riprova», non solo in console.
 */
import { getCaricaSchemas, getCaricaTables } from '../../../api/carica';
import { useResource } from '../../../hooks/useResource';

const NONE = [];

export function useCaricaSchemaSelect({
  connectionId,
  destType,
  schemaName,
}) {
  const db = destType === 'db' && Boolean(connectionId);
  const schemasResource = useResource(async () => (await getCaricaSchemas(connectionId)).schemas || [], {
    key: ['carica-schemas', connectionId || null],
    enabled: db,
  });
  const tablesResource = useResource(async () => (await getCaricaTables(connectionId, schemaName)).tables || [], {
    key: ['carica-tables', connectionId || null, schemaName || null],
    enabled: db && Boolean(schemaName),
  });

  return {
    schemas: schemasResource.data || NONE,
    tables: tablesResource.data || NONE,
    isLoadingSchemas: schemasResource.loading,
    isLoadingTables: tablesResource.loading,
    schemasError: schemasResource.error,
    tablesError: tablesResource.error,
    reloadSchemas: schemasResource.reload,
    reloadTables: tablesResource.reload,
  };
}

export default useCaricaSchemaSelect;
