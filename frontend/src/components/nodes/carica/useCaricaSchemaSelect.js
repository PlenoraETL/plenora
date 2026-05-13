import { useEffect, useState } from 'react';
import { getCaricaSchemas, getCaricaTables, getCaricaColumns } from '../../../api/carica';

export function useCaricaSchemaSelect({
  connectionId,
  destType,
  schemaName,
  tableName,
  writeMode,
}) {
  const [schemas, setSchemas] = useState([]);
  const [tables, setTables] = useState([]);
  const [destColumns, setDestColumns] = useState([]);
  const [isLoadingSchemas, setIsLoadingSchemas] = useState(false);
  const [isLoadingTables, setIsLoadingTables] = useState(false);
  const [isLoadingColumns, setIsLoadingColumns] = useState(false);

  useEffect(() => {
    if (destType === 'db' && connectionId) {
      loadSchemas(connectionId);
    } else {
      setSchemas([]); setTables([]); setDestColumns([]);
    }
  }, [connectionId, destType]);

  useEffect(() => {
    if (destType === 'db' && connectionId && schemaName) {
      loadTables(connectionId, schemaName);
    } else {
      setTables([]); setDestColumns([]);
    }
  }, [connectionId, schemaName, destType]);

  useEffect(() => {
    if (destType === 'db' && connectionId && schemaName && tableName
        && writeMode !== 'create' && writeMode !== 'replace') {
      loadDestColumns(connectionId, schemaName, tableName);
    } else {
      setDestColumns([]);
    }
  }, [connectionId, schemaName, tableName, writeMode, destType]);

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

  const loadDestColumns = async (connId, schema, table) => {
    setIsLoadingColumns(true);
    try {
      const data = await getCaricaColumns(connId, schema, table);
      setDestColumns(data.columns || []);
    } catch (error) {
      console.error('Errore caricamento colonne:', error);
    } finally {
      setIsLoadingColumns(false);
    }
  };

  return {
    schemas,
    tables,
    destColumns,
    isLoadingSchemas,
    isLoadingTables,
    isLoadingColumns,
  };
}

export default useCaricaSchemaSelect;
