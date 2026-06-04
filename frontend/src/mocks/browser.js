/**
 * mocks/browser.js — worker MSW per il browser (dev). #50.
 * Avviato on-demand da index.jsx quando VITE_USE_MOCKS=true.
 */
import { setupWorker } from 'msw/browser';
import { handlers } from './handlers';

export const worker = setupWorker(...handlers);
