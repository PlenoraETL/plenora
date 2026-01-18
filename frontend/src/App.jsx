import React from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import ProjectList from './pages/ProjectList';
import EstraiEditor from './pages/EstraiEditor';
import ManipolaEditor from './pages/ManipolaEditor';
import ConnectionManager from './pages/ConnectionManager';
import ApiConnectionManager from './pages/ApiConnectionManager';
import ArricchisciEditor from './pages/ArricchisciEditor';
import FileManager from './pages/FileManager';

function App() {
  return (
    <BrowserRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <div className="app">
		<Routes>
		  <Route path="/" element={<ProjectList />} />
		  <Route path="/project/:projectId" element={<EstraiEditor />} />
		  <Route path="/connections" element={<ConnectionManager />} />
		  <Route path="/editor/manipola/:id" element={<ManipolaEditor />} />
		  <Route path="/api-connections" element={<ApiConnectionManager />} />
		  <Route path="/arricchisci/:projectId" element={<ArricchisciEditor />} />
		  <Route path="/files" element={<FileManager />} />
		  <Route path="*" element={<Navigate to="/" replace />} />
		</Routes>
      </div>
    </BrowserRouter>
  );
}

export default App;