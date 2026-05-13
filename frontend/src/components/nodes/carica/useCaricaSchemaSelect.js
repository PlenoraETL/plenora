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

  useEffect(() => {
    if (destType === 'db' && connectionId) {
      loadSchemas(connectionId);
    } else {
      setSchemas([]); setTables([]);
    }
  }, [connectionId, destType]);

  useEffect(() => {
    if (destType === 'db' && connectionId && schemaName) {
      loadTables(connectionId, schemaName);
    } else {
      setTables([]);
    }
  }, [connectionId, schemaName, destType]);

  const loadSchemas = async (connId) => {
    setIsLoadingSchemas(true);
    try {
      const data = await getCaricaSchemas(connId);
      setSchemas(data.schemas || []);
    } catch (error) {
      console.error('Errore caricamento schemas:', error);
    } finally {
      setIsLoadingSchemas(false);
    }
  };

  const loadTables = async (connId, schema) => {
    setIsLoadingTables(true);
    try {
      const data = await getCaricaTables(connId, schema);
      setTables(data.tables || []);
    } catch (error) {
      console.error('Errore caricamento tables:', error);
    } finally {
      setIsLoadingTables(false);
    }
  };

  return {
    schemas,
    tables,
    isLoadingSchemas,
    isLoadingTables,
  };
}

export default useCaricaSchemaSelect;
