/**
 * useTransferProgress — stato dell'avanzamento di un caricamento o di uno
 * scaricamento (UI-3, U17), da passare come `onProgress` alle funzioni di
 * src/api (utils/transferProgress) e da mostrare con common/TransferProgress.
 *
 *   const upload = useTransferProgress();
 *   await uploadFile(file, name, '', '', '', null, null, { onProgress: upload.onProgress });
 *   <TransferProgress progress={upload.progress} verb="Caricamento" />
 *
 * `progress` e' null quando non c'e' un trasferimento in corso.
 */
import { useCallback, useState } from 'react';
import { useMountedRef } from './useMountedRef';

export function useTransferProgress() {
  const [progress, setProgress] = useState(null);
  const mountedRef = useMountedRef();
  const onProgress = useCallback((p) => { if (mountedRef.current) setProgress(p); }, [mountedRef]);
  const reset = useCallback(() => setProgress(null), []);
  return { progress, onProgress, reset };
}

export default useTransferProgress;
