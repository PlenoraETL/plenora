import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';

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
    plugins: [react()],
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
            // Include anche @deck.gl/react (wrapper React per DeckGL): tecnicamente
            // sta in @deck.gl ma importa da react come peerDep, quindi tenerlo
            // nel chunk react evita il ciclo deckgl↔react osservato con il
            // grouping precedente.
            if (/[\\/]node_modules[\\/](react|react-dom|react-router-dom|scheduler)[\\/]/.test(id) ||
                /[\\/]node_modules[\\/]@deck\.gl[\\/]react[\\/]/.test(id)) {
              return 'vendor-react';
            }
            // ReactFlow — libreria pesante, bundle separato
            if (/[\\/]node_modules[\\/]reactflow[\\/]/.test(id) ||
                /[\\/]node_modules[\\/]@reactflow[\\/]/.test(id)) {
              return 'vendor-reactflow';
            }
            // Grafo geospatial — usato SOLO dalle pagine lazy-loaded che
            // renderizzano mappe (ImdfPreviewModal, EstraiEditor geo panels).
            // Splittato in 2 chunk per dominio: cache granulare, HTTP/2
            // scarica parallelo. Soglia warning 1MB rispettata.
            //
            // NOTA: @loaders.gl e' unito a vendor-deckgl perche' deck.gl
            // core ne dipende e splittarli causa ciclo chunk (loaders
            // importa da luma.gl che e' in deckgl, deckgl importa loaders).
            // Mergiandoli, 27KB extra in vendor-deckgl ma grafo pulito.
            // @deck.gl/react invece e' in vendor-react (vedi sopra).

            // Deck.gl (core/layers/geo-layers) + luma/math/probe/loaders + deps
            if (/[\\/]node_modules[\\/](@deck\.gl|deck\.gl|@luma\.gl|@math\.gl|@probe\.gl|@loaders\.gl|mjolnir\.js|earcut|ml-matrix)[\\/]/.test(id)) {
              return 'vendor-deckgl';
            }
            // Maplibre-gl + deps (global/long/pbf sono transitive di maplibre)
            if (/[\\/]node_modules[\\/](maplibre-gl|global|long|pbf)[\\/]/.test(id)) {
              return 'vendor-maplibre';
            }
            // Nota: PreviewModal carica Leaflet via CDN (cdnjs) a runtime,
            // quindi nessun source importa `leaflet` da node_modules. Non
            // c'e' un chunk vendor-leaflet da emettere.
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
          target: env.VITE_API_URL || 'http://localhost:8000',
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
