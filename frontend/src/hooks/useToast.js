/**
 * useToast.js — Re-export da ToastContext per compatibilità backward
 *
 * I consumer esistenti importano da '../hooks/useToast' o '../../hooks/useToast'.
 * Questo file re-esporta useToast dal context globale, mantenendo l'API invariata.
 *
 * Non è necessario modificare nessun consumer — basta rimuovere il
 * <ToastContainer> dal JSX di ogni pagina (ora è gestito dal provider).
 */
export { useToast } from '../context/ToastContext';
