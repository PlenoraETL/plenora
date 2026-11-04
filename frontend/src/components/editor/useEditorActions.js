/**
 * useEditorActions — registra le azioni dell'editor nello store UNA volta
 * sola, con funzioni stabili che chiamano sempre l'ultima versione passata
 * (Blocco A, regola 9). Prima gli editor le registravano di nuovo a ogni
 * render (dipendenze come `preview`/`execution` cambiano a ogni render).
 * All'uscita dall'editor le azioni registrate vengono tolte.
 *
 * Le chiavi devono essere fisse e dichiarate in editor/editorActions.js.
 */
import { useEffect, useRef } from 'react';
import useCanvasStore from '../../store/useCanvasStore';

export default function useEditorActions(actions) {
  const latestRef = useRef(actions);
  latestRef.current = actions;
  const keysRef = useRef(Object.keys(actions));

  useEffect(() => {
    const keys = keysRef.current;
    const stable = Object.fromEntries(keys.map(name => [
      name,
      (...args) => latestRef.current[name]?.(...args),
    ]));
    useCanvasStore.getState().setEditorActions(stable);
    return () => {
      useCanvasStore.getState().setEditorActions(Object.fromEntries(keys.map(name => [name, null])));
    };
  }, []);
}
