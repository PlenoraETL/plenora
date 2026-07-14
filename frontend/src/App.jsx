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
 */
import React from 'react';
import { BrowserRouter, Routes, Route, Navigate, useLocation } from 'react-router-dom';
import { AuthProvider, useAuth } from './context/AuthContext';
import { AppErrorBoundary } from './components/common/ErrorBoundary';
import { ToastProvider } from './context/ToastContext';
import CommandPalette from './components/common/CommandPalette';
import KeyboardShortcutsHelp from './components/common/KeyboardShortcutsHelp';
import { role, can } from './utils/permissions';

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
const EseguibiliList       = React.lazy(() => import('./pages/EseguibiliList'));
const EsecutoreHome        = React.lazy(() => import('./pages/EsecutoreHome'));
const TicketsList          = React.lazy(() => import('./pages/TicketsList'));
const TicketDetail         = React.lazy(() => import('./pages/TicketDetail'));

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
  if (!token) return <Navigate to="/login" replace />;
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
  if (!token) return <Navigate to="/login" replace />;
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
  if (!token) return <Navigate to="/login" replace />;
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
  if (!token) return <Navigate to="/login" replace />;
  if (role.isEsecutore(user) && !location.pathname.startsWith('/run')) {
    return <Navigate to="/run" replace />;
  }
  if (!role.isSuperadmin(user)) return <Navigate to="/" replace />;
  return children;
}

function AppRoutes() {
  return (
    <React.Suspense fallback={
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100vh', color: '#6b7280', fontSize: 16 }}>
        Caricamento...
      </div>
    }>
    <Routes>
      <Route path="/login" element={<LoginPage />} />

      <Route path="/" element={<ProtectedRoute><ProjectList /></ProtectedRoute>} />
      <Route path="/project/:projectId" element={<ProtectedRoute><EstraiEditor /></ProtectedRoute>} />
      <Route path="/connections" element={<ProtectedRoute><ConnectionManager /></ProtectedRoute>} />
      {/* Param `:projectId` (coerente con /project/:projectId e
          /arricchisci/:projectId). Pre-fix era `:id`, costringendo
          ManipolaEditor a fare `{ id: projectId } = useParams()`. */}
      <Route path="/editor/manipola/:projectId" element={<ProtectedRoute><ManipolaEditor /></ProtectedRoute>} />
      <Route path="/external-connectors" element={<ProtectedRoute><ApiConnectionManager /></ProtectedRoute>} />
      <Route path="/arricchisci/:projectId" element={<ProtectedRoute><ArricchisciEditor /></ProtectedRoute>} />
      <Route path="/files" element={<ProtectedRoute><FileManager /></ProtectedRoute>} />
      <Route path="/schedules" element={<ProtectedRoute><ScheduleList /></ProtectedRoute>} />
      <Route path="/logs" element={<ProtectedRoute><LogViewer /></ProtectedRoute>} />
      <Route path="/datasources" element={<ProtectedRoute><DataSourceManager /></ProtectedRoute>} />
      <Route path="/admin/users" element={<AdminRoute><UserManager /></AdminRoute>} />
      <Route path="/admin/backups" element={<SuperadminRoute><AdminBackupsPage /></SuperadminRoute>} />
      <Route path="/admin/system-status" element={<SuperadminRoute><AdminSystemStatusPage /></SuperadminRoute>} />
      <Route path="/admin/runtime-config" element={<SuperadminRoute><AdminRuntimeConfigPage /></SuperadminRoute>} />
      <Route path="/admin/object-storage" element={<SuperadminRoute><AdminObjectStoragePage /></SuperadminRoute>} />
      <Route path="/eseguibili" element={<ProtectedRoute><EseguibiliList /></ProtectedRoute>} />
      <Route path="/tickets" element={<ProtectedRoute><TicketsList /></ProtectedRoute>} />
      <Route path="/tickets/:id" element={<ProtectedRoute><TicketDetail /></ProtectedRoute>} />

      {/* Kiosk page del ruolo esecutore — non passa per ProtectedRoute
          perché ProtectedRoute redirigerebbe in loop su /run */}
      <Route path="/run" element={<EsecutoreRoute><EsecutoreHome /></EsecutoreRoute>} />

      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
    </React.Suspense>
  );
}

function App() {
  return (
    <AppErrorBoundary>
      <BrowserRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <AuthProvider>
          <ToastProvider>
            <div className="app">
              <CommandPalette />
              <KeyboardShortcutsHelp />
              <AppRoutes />
            </div>
          </ToastProvider>
        </AuthProvider>
      </BrowserRouter>
    </AppErrorBoundary>
  );
}

export default App;
