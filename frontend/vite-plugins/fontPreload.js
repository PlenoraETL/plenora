/**
 * Preload dei font principali nella build (UI-4, V13).
 *
 * I font stanno in src/assets/fonts (stesso origin, P31): Vite li copia con
 * un nome con hash, quindi index.html non puo' citarli a mano. Questo plugin,
 * alla generazione di index.html, aggiunge un <link rel="preload"> per i
 * file elencati in PRELOADED_FONTS (sottoinsieme latino, quello di quasi
 * ogni pagina): il browser li scarica insieme al CSS invece di scoprirli
 * solo al primo testo. Niente origini nuove ne' script inline: la CSP
 * (font-src 'self') resta quella.
 */

/** Font precaricati: prefisso del file in src/assets/fonts. */
export const PRELOADED_FONTS = Object.freeze([
  'inter-latin-wght-normal', // testo
  'syne-latin-wght-normal', // titoli e logo
]);

/** Tag di preload per i file della build che corrispondono a PRELOADED_FONTS. */
export function fontPreloadTags(fileNames, base = '/') {
  return PRELOADED_FONTS.map((prefix) => {
    const file = fileNames.find(f => f.split('/').pop().startsWith(`${prefix}-`) && f.endsWith('.woff2'));
    if (!file) throw new Error(`fontPreload: font ${prefix} non trovato nella build`);
    return {
      tag: 'link',
      // `crossorigin` e' obbligatorio per i font precaricati, anche dallo stesso origin.
      attrs: { rel: 'preload', href: `${base}${file}`, as: 'font', type: 'font/woff2', crossorigin: '' },
      injectTo: 'head',
    };
  });
}

export default function fontPreload() {
  let base = '/';
  return {
    name: 'plenora-font-preload',
    apply: 'build',
    configResolved(config) { base = config.base || '/'; },
    transformIndexHtml: {
      order: 'post',
      handler(_html, ctx) {
        if (!ctx.bundle) return [];
        return fontPreloadTags(Object.keys(ctx.bundle), base);
      },
    },
  };
}
