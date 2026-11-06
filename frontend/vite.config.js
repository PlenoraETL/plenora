import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';
import securityHeaders from './vite-plugins/securityHeaders.js';
import fontPreload from './vite-plugins/fontPreload.js';

/**
 * vite.config.js — Configurazione build URBY-ETL Frontend
 *
 * Differenze development vs production:
 *
 *   DEVELOPMENT
 *     - Source maps complete
 *     - Console.* tutti attivi
 *     - HMR abilitato
 *
 *   PRODUCTION (npm run build)
 *     - console.log / console.debug / console.info → rimossi dal bundle
 *     - console.error / console.warn → mantenuti (errori legittimi a runtime)
 *     - debugger statements → rimossi
 *     - Source maps disabilitati
 *     - Chunk splitting: vendor React separato da app code
 *     - Minificazione esbuild (più veloce di terser, ~stessa compressione)
 */
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  const isProd = mode === 'production';

  return {
    // securityHeaders: CSP e intestazioni di sicurezza (P31) in `vite` (Report-Only)
    // e `vite preview` (identiche alla produzione).
    // fontPreload: <link rel="preload"> dei font principali nella build (UI-4, V13).
    plugins: [react(), securityHeaders({ apiUrl: env.VITE_API_URL }), fontPreload()],
    test: {
      environment: 'jsdom',
      globals: true,
      setupFiles: './tests/setup.js',
      include: ['tests/**/*.test.{js,jsx}'],
    },

    // ── Build configuration ──────────────────────────────────────────────
    build: {
      // Source maps solo in dev (non esporre il sorgente in produzione)
      sourcemap: !isProd,

      // Chunk splitting: separa vendor da app per caching ottimale
      rollupOptions: {
        output: {
          manualChunks(id) {
            if (!id.includes('node_modules')) return undefined;

            // React core — cambia raramente, cached a lungo dal browser.
            if (/[\\/]node_modules[\\/](react|react-dom|react-router-dom|scheduler)[\\/]/.test(id)) {
              return 'vendor-react';
            }
            // ReactFlow — libreria pesante, bundle separato
            if (/[\\/]node_modules[\\/]@xyflow[\\/]/.test(id)) {
              return 'vendor-reactflow';
            }
            // Leaflet resta nel chunk del suo unico consumatore (PreviewModal,
            // caricato su richiesta). Fase 5 T7 (D6b): tolte le regole per
            // deck.gl e maplibre-gl, che l'app non usa piu'.
            return undefined; // default: rollup decide
          },
        },
      },

      // Avvisa se un chunk supera 1MB (default 500kb)
      chunkSizeWarningLimit: 1000,
    },

    // ── esbuild: stripping console in production ─────────────────────────
    esbuild: isProd
      ? {
          // Rimuove solo i console di debug — NON console.error/warn
          // (sono tutti in catch block e servono per diagnostica runtime)
          pure: ['console.log', 'console.debug', 'console.info', 'console.trace'],
          // Rimuove debugger statements lasciati per errore
          drop: ['debugger'],
        }
      : {},

    // ── Dev server ───────────────────────────────────────────────────────
    server: {
      port: 3000,
      proxy: {
        // Proxy /api → backend FastAPI in dev
        '/api': {
          target: env.VITE_API_URL || 'http://localhost:5004',
          changeOrigin: true,
        },
      },
    },

    // ── Preview server (per testare il build localmente) ─────────────────
    preview: {
      port: 4173,
    },
  };
});
