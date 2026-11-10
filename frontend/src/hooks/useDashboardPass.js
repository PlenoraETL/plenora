/**
 * useDashboardPass — UN hook per sessione Grafana, lasciapassare e rinnovo
 * della dashboard di un'esecuzione (11/11/2026).
 *
 * Aperto (`open` vero) con un bersaglio (utils/eseguibileDashboard
 * .dashboardTarget):
 *  1. `POST /grafana/session` (cookie HttpOnly Path=/grafana, 30 min) PRIMA
 *     dell'iframe: senza, Caddy (forward_auth) risponde 401;
 *  2. `POST /eseguibili/{id}/dashboard/pass {task_id}` -> `iframe_path`
 *     (solo un percorso relativo sotto /grafana/: safeGrafanaPath);
 *  3. rinnovo di ENTRAMBI ogni 20 minuti o un minuto prima della scadenza
 *     del lasciapassare (`nextRenewal`), con il nuovo `iframe_path`
 *     (l'iframe si ricarica); mai oltre `data_expires_at`: a quell'ora
 *     stato `expired` («dati non piu' disponibili»), come per un 410. La
 *     scadenza si misura sull'orologio e si ricontrolla al ritorno sulla
 *     scheda (un timer di una scheda nascosta arriva tardi).
 * Chiuso o smontato (finestra dei risultati chiusa, risultato `gone`,
 * uscita): timer fermi e risposte in volo ignorate. Il lasciapassare scade
 * da solo; il cookie lo toglie il logout (context/AuthContext).
 *
 * Stati: 'idle' | 'loading' | 'ready' | 'expired' | 'error'.
 */
import { useCallback, useEffect, useState } from 'react';
import { issueEseguibileDashboardPass, openGrafanaSession } from '../api';
import { can } from '../utils/permissions';
import { nextRenewal, safeGrafanaPath } from '../utils/eseguibileDashboard';
import { requestErrorMessage } from '../utils/errorMessages';

const IDLE = Object.freeze({ status: 'idle', iframePath: null, error: null, dataExpiresAt: null });

export function useDashboardPass({ user, target, open }) {
  const eseguibileId = target?.eseguibileId || null;
  const taskId = target?.taskId || null;
  const active = Boolean(open && eseguibileId && taskId && can.openEseguibileDashboard(user));
  const [state, setState] = useState(IDLE);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (!active) {
      setState(IDLE);
      return undefined;
    }
    let cancelled = false;
    let timer = null;
    let due = null; // { at, expire } del prossimo passo

    const arm = (next) => {
      due = next;
      clearTimeout(timer);
      timer = setTimeout(tick, Math.max(0, next.at - Date.now()));
    };

    const load = async (first) => {
      if (first) setState({ ...IDLE, status: 'loading' });
      try {
        await openGrafanaSession();
        const pass = await issueEseguibileDashboardPass(eseguibileId, taskId);
        if (cancelled) return;
        const iframePath = safeGrafanaPath(pass?.iframe_path);
        if (!iframePath) throw new Error('Il server ha indicato un indirizzo della dashboard non valido.');
        setState({
          status: 'ready',
          iframePath,
          error: null,
          dataExpiresAt: Number(pass.data_expires_at) > 0 ? Number(pass.data_expires_at) * 1000 : null,
        });
        arm(nextRenewal(pass));
      } catch (err) {
        if (cancelled) return;
        due = null;
        if (err?.response?.status === 410) setState({ ...IDLE, status: 'expired' });
        else setState({ ...IDLE, status: 'error', error: requestErrorMessage(err, 'Dashboard non disponibile') });
      }
    };

    function tick() {
      if (cancelled || !due) return;
      clearTimeout(timer);
      const current = due;
      due = null;
      if (current.expire) {
        setState({ ...IDLE, status: 'expired' });
        return;
      }
      load(false);
    }

    const onVisible = () => {
      if (document.visibilityState === 'visible' && due && Date.now() >= due.at) tick();
    };

    load(true);
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      cancelled = true;
      clearTimeout(timer);
      document.removeEventListener('visibilitychange', onVisible);
    };
  }, [active, eseguibileId, taskId, attempt]);

  const retry = useCallback(() => setAttempt(n => n + 1), []);
  return { ...state, retry };
}
