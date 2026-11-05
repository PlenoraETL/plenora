/**
 * pressable — un elemento che non è un <button> ma si clicca (riga di una
 * tabella, scheda, voce di un elenco) si usa anche da tastiera e lo dice ai
 * lettori di schermo (Fase 5 T7, a11y-1).
 *
 *   <tr {...pressable(() => open(t))}>…</tr>
 *
 * Ruolo `button`, nel giro del Tab, Invio e Spazio come il clic. Un
 * elemento senza testo proprio passa `label`. Dove si può si usa un
 * <button> vero; tests/shared/t7Rules.contract.test.jsx vieta `onClick` su
 * div/span/tr/td/li/... senza questo (o ruolo e tastiera scritti a mano).
 */
export function pressable(onPress, { label, disabled = false } = {}) {
  return {
    role: 'button',
    tabIndex: disabled ? -1 : 0,
    'aria-disabled': disabled || undefined,
    ...(label ? { 'aria-label': label } : {}),
    onClick: disabled ? undefined : onPress,
    onKeyDown: disabled ? undefined : (e) => {
      if (e.target !== e.currentTarget) return; // tasti dei controlli interni
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        onPress?.(e);
      }
    },
  };
}
