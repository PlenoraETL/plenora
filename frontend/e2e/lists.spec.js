// Smoke su tutte le pagine "list" principali post-login.
//
// Obiettivo: cogliere bug catastrofici tipo:
//  - JS error che impedisce il mount del componente di lista
//  - chiamata API che rompe la pagina perche' il backend ha cambiato shape
//  - regressione di permission (vedi gate Admin/Superadmin)
//
// NON verifichiamo i contenuti (dati di seed variano per ambiente);
// verifichiamo che la pagina sia "viva":
//  - URL stabile dopo la navigazione (no redirect inatteso a /login
//    che indicherebbe token rotto)
//  - 0 errori "page error" / 0 console.error gravi durante il render
//  - presenza di un container DOM di livello applicazione

import { test, expect } from './fixtures.js';

// Set di pagine a cui un admin ha sempre accesso.
const PAGES = [
  { path: '/',                   label: 'projects (ESTRAI)'   },
  { path: '/schedules',          label: 'schedules'           },
  { path: '/datasources',        label: 'data sources'        },
  { path: '/connections',        label: 'DB connections'      },
  { path: '/external-connectors', label: 'API connections'    },
  { path: '/files',              label: 'file manager'        },
  { path: '/logs',               label: 'logs viewer'         },
  { path: '/eseguibili',         label: 'eseguibili'          },
  { path: '/tickets',            label: 'tickets list'        },
  { path: '/admin/users',        label: 'admin users'         },
];

for (const { path, label } of PAGES) {
  test(`smoke ${label} (${path})`, async ({ page }) => {
    /** @type {{type:string,text:string}[]} */
    const consoleErrors = [];
    page.on('console', (msg) => {
      if (msg.type() === 'error') {
        consoleErrors.push({ type: msg.type(), text: msg.text() });
      }
    });
    /** @type {Error[]} */
    const pageErrors = [];
    page.on('pageerror', (err) => pageErrors.push(err));

    const resp = await page.goto(path, { waitUntil: 'networkidle' });

    // L'app e' una SPA: il path puo' essere "non esistente" lato server
    // ma l'index.html viene servito comunque (rewrite nginx). Quindi
    // non assertiamo sullo status del navigation, ma sull'URL post-load.
    expect(resp, `no response for ${path}`).not.toBeNull();

    // Token valido -> non veniamo bounciati al login. Se ci finiamo,
    // o il token e' rotto (auth regression) o il route guard ha un bug.
    expect(new URL(page.url()).pathname, `${label} reindirizza a login`)
      .not.toBe('/login');

    // L'app monta un container root con id="root" (Vite default).
    // Se manca, il bundle non e' partito.
    await expect(page.locator('#root')).toBeAttached();

    // Filtra il rumore noto da error console legittimi del browser
    // (es. extension, favicon 404). Conservativi: alziamo flag solo
    // su errori React/JS chiari.
    const fatal = consoleErrors.filter((e) =>
      /TypeError|ReferenceError|SyntaxError|Cannot read|undefined is not/.test(
        e.text,
      ),
    );
    if (fatal.length) {
      throw new Error(
        `Errori console fatali su ${label} (${path}):\n` +
          fatal.map((e) => `  - ${e.text}`).join('\n'),
      );
    }
    if (pageErrors.length) {
      throw new Error(
        `Uncaught page errors su ${label} (${path}):\n` +
          pageErrors.map((e) => `  - ${e.message}`).join('\n'),
      );
    }
  });
}
