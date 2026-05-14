/**
 * useImdfConnectionsAndFolders — Hook per le fetch di config IMDF.
 *
 * Due fetch a catena (analogo a useCaricaSchemaSelect per CARICA):
 *  1. connections (ArcGIS feature connections) — mount-once.
 *  2. folders (per la connection selezionata) — dipende da connectionId.
 *
 * Entrambe usano cancellation pattern via flag `cancelled` nel cleanup
 * di useEffect: rapid-click su connection diverse non lascia la vecchia
 * fetch sovrascrivere lo state della nuova.
 *
 * RB.7.C: skip della fetch folders quando il backend marca
 * `access_denied=true` sulla connection — evita 404 a vuoto.
 */

import { useEffect, useState } from 'react';
import { listFeatureConnections, listFolders } from '../../../api/features';

export function useImdfConnectionsAndFolders({ connectionId, isAccessDenied }) {
  const [connections, setConnections] = useState([]);
  const [folders, setFolders] = useState([]);
  const [loadingConns, setLoadingConns] = useState(false);
  const [loadingFolders, setLoadingFolders] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoadingConns(true);
      try {
        const res = await listFeatureConnections();
        if (!cancelled) setConnections(Array.isArray(res) ? res : (res.connections || []));
      } catch (e) {
        if (!cancelled) console.error('Errore caricamento connessioni ArcGIS:', e);
      } finally {
        if (!cancelled) setLoadingConns(false);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    if (isAccessDenied) { setFolders([]); return undefined; }
    if (!connectionId) { setFolders([]); return undefined; }
    let cancelled = false;
    (async () => {
      setLoadingFolders(true);
      try {
        const res = await listFolders(connectionId);
        if (!cancelled) setFolders(res?.folders || res || []);
      } catch (e) {
        if (!cancelled) setFolders([]);
      } finally {
        if (!cancelled) setLoadingFolders(false);
      }
    })();
    return () => { cancelled = true; };
  }, [connectionId, isAccessDenied]);

  return { connections, folders, loadingConns, loadingFolders };
}

export default useImdfConnectionsAndFolders;
