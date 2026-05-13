import { useEffect, useState } from 'react';
import { getCaricaSchemas, getCaricaTables } from '../../../api/carica';

export function useCaricaSchemaSelect({
  connectionId,
  destType,
  schemaName,
}) {
  const [schemas, setSchemas] = useState([]);
  const [tables, setTables] = useState([]);
  const [isLoadingSchemas, setIsLoadingSchemas] = useState(false);
  const [isLoadingTables, setIsLoadingTables] = useState(false);

  // Cancellation guard: rapid-click di connection diverse poteva lasciare
  // la vecchia fetch sovrascrivere lo state DOPO che la nuova era partita.
  useEffect(() => {
    if (destType !== 'db' || !connectionId) {
      setSchemas([]); setTables([]);
      return undefined;
    }
    let cancelled = false;
    setIsLoadingSchemas(true);
    (async () => {
      try {
        const data = await getCaricaSchemas(connectionId);
        if (!cancelled) setSchemas(data.schemas || []);
      } catch (error) {
        if (!cancelled) console.error('Errore caricamento schemas:', error);
      } finally {
        if (!cancelled) setIsLoadingSchemas(false);
      }
    })();
    return () => { cancelled = true; };
  }, [connectionId, destType]);

  useEffect(() => {
    if (destType !== 'db' || !connectionId || !schemaName) {
      setTables([]);
      return undefined;
    }
    let cancelled = false;
    setIsLoadingTables(true);
    (async () => {
      try {
        const data = await getCaricaTables(connectionId, schemaName);
        if (!cancelled) setTables(data.tables || []);
      } catch (error) {
        if (!cancelled) console.error('Errore caricamento tables:', error);
      } finally {
        if (!cancelled) setIsLoadingTables(false);
      }
    })();
    return () => { cancelled = true; };
  }, [connectionId, schemaName, destType]);

  return {
    schemas,
    tables,
    isLoadingSchemas,
    isLoadingTables,
  };
}

export default useCaricaSchemaSelect;
