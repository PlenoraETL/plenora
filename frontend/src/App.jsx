/**
 * App.jsx — Root dell'applicazione, routing principale
 *
 * Definisce tutte le route protette dell'applicazione.
 * Ogni route è avvolta in ProtectedRoute che reindirizza a /login
 * se il token JWT non è presente in sessionStorage.
 *
 * Route pubbliche:  /login
 * Route protette:   /, /project/:id, /editor/manipola/:id,
 *                   /arricchisci/:id, /connections, /external-connectors,
 *                   /files, /schedules, /logs, /datasources
 *
 * Fase 5, T4: data router (createBrowserRouter) per il guard di navigazione
 * unico (NavigationGuard: useBlocker su link, Indietro del browser e Ctrl+K);
 * un ConfirmProvider per tutta l'app (dialog del progetto, mai
 * window.confirm); il rimando a /login ricorda la pagina richiesta
 * (`state.from`, solo dopo un 401 o senza sessione: mai dopo il logout
 * volontario); pagina 404 invece del ritorno muto alla home.
 */
import React, { useState } from 'react';
import { createBrowserRouter, RouterProvider, Routes, Route, Navigate, useLocation, useParams } from 'react-router-dom';
import { AuthProvider, useAuth } from './context/AuthContext';
import { AppErrorBoundary, RouteErrorBoundary } from './components/common/ErrorBoundary';
import { ToastProvider } from './context/ToastContext';
import { ConfirmProvider } from './context/ConfirmContext';
import CommandPalette from './components/common/CommandPalette';
import KeyboardShortcutsHelp from './components/common/KeyboardShortcutsHelp';
import NavigationGuard from './components/common/NavigationGuard';
import { role, can } from './utils/permissions';
import { loginRedirectState } from './utils/navigation';

// Lazy loading: ogni pagina viene caricata solo quando la route viene visitata.
// Riduce il bundle iniziale da ~1 chunk a chunk separati per pagina.
// LoginPage è eager (prima pagina che l'utente vede).
import LoginPage from './pages/LoginPage';

const ProjectList          = React.lazy(() => import('./pages/ProjectList'));
const EstraiEditor         = React.lazy(() => import('./pages/EstraiEditor'));
const ManipolaEditor       = React.lazy(() => import('./pages/ManipolaEditor'));
const ConnectionManager    = React.lazy(() => import('./pages/ConnectionManager'));
const ApiConnectionManager = React.lazy(() => import('./pages/ApiConnectionManager'));
const ArricchisciEditor    = React.lazy(() => import('./pages/ArricchisciEditor'));
const FileManager          = React.lazy(() => import('./pages/FileManager'));
const ScheduleList         = React.lazy(() => import('./pages/ScheduleList'));
const LogViewer            = React.lazy(() => import('./pages/LogViewer'));
const DataSourceManager    = React.lazy(() => import('./pages/DataSourceManager'));
const UserManager          = React.lazy(() => import('./pages/UserManager'));
const AdminBackupsPage     = React.lazy(() => import('./pages/AdminBackupsPage'));
const AdminSystemStatusPage = React.lazy(() => import('./pages/AdminSystemStatusPage'));
const AdminRuntimeConfigPage = React.lazy(() => import('./pages/AdminRuntimeConfigPage'));
const AdminObjectStoragePage = React.lazy(() => import('./pages/AdminObjectStoragePage'));
const AdminTaskRuntimePage = React.lazy(() => import('./pages/AdminTaskRuntimePage'));
const EseguibiliList       = React.lazy(() => import('./pages/EseguibiliList'));
const EsecutoreHome        = React.lazy(() => import('./pages/EsecutoreHome'));
const TicketsList          = React.lazy(() => import('./pages/TicketsList'));
const TicketDetail         = React.lazy(() => import('./pages/TicketDetail'));
const OpenProjectPage      = React.lazy(() => import('./pages/OpenProjectPage'));
const NotFoundPage         = React.lazy(() => import('./pages/NotFoundPage'));

/**
 * Rimando a /login che ricorda dove si voleva andare (R7.5), ma mai dopo
 * un'uscita volontaria (regola unica: utils/navigation.loginRedirectState).
 */
function ToLogin() {
  const location = useLocation();
  return <Navigate to="/login" replace state={loginRedirectState(location)} />;
}

/**
 * ProtectedRoute — guard di base: richiede token JWT valido.
 *
 * In più, gestisce il ruolo "esecutore" (kiosk-only): se l'utente è
 * esecutore e prova ad accedere a una rotta diversa da /run, viene
 * forzato a /run. L'esecutore vive isolato nella sua kiosk page.
 */
function ProtectedRoute({ children }) {
  const { token, user } = useAuth();
  const location = useLocation();
  if (!token) return <ToLogin />;
  // Esecutore può vedere SOLO /run. Tutto il resto → redirect.
  if (role.isEsecutore(user) && !location.pathname.startsWith('/run')) {
    return <Navigate to="/run" replace />;
  }
  return children;
}

/**
 * Wrapper per la sola route /run: l'esecutore deve poter entrare,
 * ma anche gli altri ruoli che ci finiscono per errore vengono mandati
 * sulla home normale.
 */
function EsecutoreRoute({ children }) {
  const { token, user } = useAuth();
  if (!token) return <ToLogin />;
  if (!role.isEsecutore(user)) return <Navigate to="/" replace />;
  return children;
}

/**
 * Guard per /admin/users — richiede admin o superadmin. Fa il redirect
 * PRIMA del render del child: nessun useEffect/fetch parte finche'
 * l'autorizzazione non e' verificata. Evita API call non autorizzate.
 */
function AdminRoute({ children }) {
  const { token, user } = useAuth();
  const location = useLocation();
  if (!token) return <ToLogin />;
  if (role.isEsecutore(user) && !location.pathname.startsWith('/run')) {
    return <Navigate to="/run" replace />;
  }
  if (!can.manageUsers(user)) return <Navigate to="/" replace />;
  return children;
}

/**
 * Guard per /admin/backups e altre rotte superadmin-only.
 * Stesso pattern di AdminRoute ma con check role.isSuperadmin.
 */
function SuperadminRoute({ children }) {
  const { token, user } = useAuth();
  const location = useLocation();
  if (!token) return <ToLogin />;
  if (role.isEsecutore(user) && !location.pathname.startsWith('/run')) {
    return <Navigate to="/run" replace />;
  }
  if (!role.isSuperadmin(user)) return <Navigate to="/" replace />;
  return children;
}

/**
 * Un progetto = un montaggio dell'editor (R4.6): cambiando `:projectId` nella
 * stessa rotta lo stato locale dell'editor (ricerche, finestre, richieste in
 * corso) non passa al progetto nuovo.
 */
function PerProject({ children }) {
  const { projectId } = useParams();
  return <React.Fragment key={projectId}>{children}</React.Fragment>;
}

function AppRoutes() {
  return (
    // R5.7: un boundary per pagina, dentro il Router (si azzera cambiando rotta).
    <RouteErrorBoundary>
    <React.Suspense fallback={
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100vh', color: '#6b7280', fontSize: 16 }}>
        Caricamento...
      </div>
    }>
    <Routes>
      <Route path="/login" element={<LoginPage />} />

      <Route path="/" element={<ProtectedRoute><ProjectList /></ProtectedRoute>} />
      <Route path="/project/:projectId" element={<ProtectedRoute><PerProject><EstraiEditor /></PerProject></ProtectedRoute>} />
      <Route path="/open/:projectId" element={<ProtectedRoute><PerProject><OpenProjectPage /></PerProject></ProtectedRoute>} />
      <Route path="/connections" element={<ProtectedRoute><ConnectionManager /></ProtectedRoute>} />
      {/* Param `:projectId` (coerente con /project/:projectId e
          /arricchisci/:projectId). Pre-fix era `:id`, costringendo
          ManipolaEditor a fare `{ id: projectId } = useParams()`. */}
      <Route path="/editor/manipola/:projectId" element={<ProtectedRoute><PerProject><ManipolaEditor /></PerProject></ProtectedRoute>} />
      <Route path="/external-connectors" element={<ProtectedRoute><ApiConnectionManager /></ProtectedRoute>} />
      <Route path="/arricchisci/:projectId" element={<ProtectedRoute><PerProject><ArricchisciEditor /></PerProject></ProtectedRoute>} />
      <Route path="/files" element={<ProtectedRoute><FileManager /></ProtectedRoute>} />
      <Route path="/schedules" element={<ProtectedRoute><ScheduleList /></ProtectedRoute>} />
      <Route path="/logs" element={<ProtectedRoute><LogViewer /></ProtectedRoute>} />
      <Route path="/datasources" element={<ProtectedRoute><DataSourceManager /></ProtectedRoute>} />
      <Route path="/admin/users" element={<AdminRoute><UserManager /></AdminRoute>} />
      <Route path="/admin/backups" element={<SuperadminRoute><AdminBackupsPage /></SuperadminRoute>} />
      <Route path="/admin/system-status" element={<SuperadminRoute><AdminSystemStatusPage /></SuperadminRoute>} />
      <Route path="/admin/runtime-config" element={<SuperadminRoute><AdminRuntimeConfigPage /></SuperadminRoute>} />
      <Route path="/admin/object-storage" element={<SuperadminRoute><AdminObjectStoragePage /></SuperadminRoute>} />
      <Route path="/admin/task-runtime" element={<SuperadminRoute><AdminTaskRuntimePage /></SuperadminRoute>} />
      <Route path="/eseguibili" element={<ProtectedRoute><EseguibiliList /></ProtectedRoute>} />
      <Route path="/tickets" element={<ProtectedRoute><TicketsList /></ProtectedRoute>} />
      <Route path="/tickets/:id" element={<ProtectedRoute><TicketDetail /></ProtectedRoute>} />

      {/* Kiosk page del ruolo esecutore — non passa per ProtectedRoute
          perché ProtectedRoute redirigerebbe in loop su /run */}
      <Route path="/run" element={<EsecutoreRoute><EsecutoreHome /></EsecutoreRoute>} />

      <Route path="*" element={<ProtectedRoute><NotFoundPage /></ProtectedRoute>} />
    </Routes>
    </React.Suspense>
    </RouteErrorBoundary>
  );
}

/** Layout radice dentro il data router: provider, guard, palette, pagine. */
function RootLayout() {
  return (
    <AppErrorBoundary>
      <AuthProvider>
        <ToastProvider>
          <ConfirmProvider>
            <div className="app">
              <NavigationGuard />
              <CommandPalette />
              <KeyboardShortcutsHelp />
              <AppRoutes />
            </div>
          </ConfirmProvider>
        </ToastProvider>
      </AuthProvider>
    </AppErrorBoundary>
  );
}

const ROUTER_FUTURE = { v7_relativeSplatPath: true };

export function createAppRouter() {
  return createBrowserRouter([{ path: '*', element: <RootLayout /> }], { future: ROUTER_FUTURE });
}

function App() {
  // Un router per montaggio dell'app (non a livello di modulo: importare
  // App nei test non crea una history).
  const [router] = useState(createAppRouter);
  return (
    <AppErrorBoundary>
      <RouterProvider router={router} future={{ v7_startTransition: true }} />
    </AppErrorBoundary>
  );
}

export default App;
