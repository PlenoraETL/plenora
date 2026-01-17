import React, { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { 
  listConnections, 
  createConnection, 
  updateConnection, 
  deleteConnection,
  testConnection 
} from '../api/client';
import '../styles/ConnectionManager.css';

function ConnectionManager() {
  const navigate = useNavigate();
  const [connections, setConnections] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showModal, setShowModal] = useState(false);
  const [editingConnection, setEditingConnection] = useState(null);
  const [testingConnection, setTestingConnection] = useState(null);
  const [testResults, setTestResults] = useState({});
  
  const [formData, setFormData] = useState({
    name: '',
    description: '',
    type: 'postgres',
    host: '',
    port: 5432,
    database: '',
    username: '',
    password: ''
  });

  const dbTypes = [
    { value: 'postgres', label: '🐘 PostgreSQL', color: '#336791' },
    { value: 'mssql', label: '🔷 MS SQL Server', color: '#CC2927' },
    { value: 'mysql', label: '🐬 MySQL', color: '#4479A1' },
    { value: 'oracle', label: '🔴 Oracle', color: '#F80000' },
    { value: 'sqlite', label: '📁 SQLite', color: '#003B57' }
  ];

  useEffect(() => {
    loadConnections();
  }, []);

  const loadConnections = async () => {
    try {
      setLoading(true);
      const data = await listConnections();
      setConnections(data.connections || []);
    } catch (error) {
      console.error('Errore caricamento connessioni:', error);
      alert('Errore caricamento connessioni');
    } finally {
      setLoading(false);
    }
  };

  const handleOpenModal = (connection = null) => {
    if (connection) {
      setEditingConnection(connection);
      setFormData({
        name: connection.name,
        description: connection.description || '',
        type: connection.type,
        host: connection.host,
        port: connection.port,
        database: connection.database,
        username: connection.username,
        password: connection.password || ''
      });
    } else {
      setEditingConnection(null);
      setFormData({
        name: '',
        description: '',
        type: 'postgres',
        host: '',
        port: 5432,
        database: '',
        username: '',
        password: ''
      });
    }
    setShowModal(true);
  };

  const handleCloseModal = () => {
    setShowModal(false);
    setEditingConnection(null);
  };

  const handleSubmit = async () => {
    if (!formData.name.trim() || !formData.host.trim() || !formData.database.trim()) {
      alert('Compila i campi obbligatori: nome, host, database');
      return;
    }

    try {
      if (editingConnection) {
        await updateConnection(editingConnection.id, formData);
      } else {
        await createConnection(formData);
      }
      setShowModal(false);
      setEditingConnection(null);
      loadConnections();
    } catch (error) {
      console.error('Errore salvataggio connessione:', error);
      alert('Errore salvataggio connessione');
    }
  };

  const handleDelete = async (connectionId, connectionName) => {
    if (!confirm(`Eliminare la connessione "${connectionName}"?`)) return;
    try {
      await deleteConnection(connectionId);
      loadConnections();
    } catch (error) {
      console.error('Errore eliminazione connessione:', error);
      alert('Errore eliminazione connessione');
    }
  };

  const handleTest = async (connectionId) => {
    setTestingConnection(connectionId);
    try {
      const result = await testConnection(connectionId);
      setTestResults(prev => ({ ...prev, [connectionId]: result }));
    } catch (error) {
      console.error('Errore test connessione:', error);
      setTestResults(prev => ({ 
        ...prev, 
        [connectionId]: { success: false, message: 'Errore durante il test della connessione' }
      }));
    } finally {
      setTestingConnection(null);
    }
  };

  const handleTypeChange = (type) => {
    const defaultPorts = { postgres: 5432, mssql: 1433, mysql: 3306, oracle: 1521, sqlite: 0 };
    setFormData({ ...formData, type, port: defaultPorts[type] || 5432 });
  };

  if (loading) {
    return (
      <div className="connection-manager__loading">
        Caricamento connessioni...
      </div>
    );
  }

  return (
    <div className="connection-manager">
      {/* Header */}
      <header className="connection-manager__header">
        <div className="connection-manager__header-content">
          <div className="connection-manager__header-left">
            <button onClick={() => navigate(-1)} className="connection-manager__back-btn">
              ← Indietro
            </button>
            <div>
              <h1 className="connection-manager__title">🔌 Connessioni Database</h1>
              <p className="connection-manager__subtitle">
                Gestisci le connessioni ai database per i tuoi progetti ETL
              </p>
            </div>
          </div>
          <button onClick={() => handleOpenModal()} className="connection-manager__new-btn">
            + Nuova connessione
          </button>
        </div>
      </header>

      {/* Content */}
      <main className="connection-manager__main">
        {connections.length === 0 ? (
          <div className="connection-manager__empty">
            <div className="connection-manager__empty-icon">🔌</div>
            <h3 className="connection-manager__empty-title">Nessuna connessione configurata</h3>
            <p className="connection-manager__empty-text">Crea la tua prima connessione per iniziare</p>
            <button onClick={() => handleOpenModal()} className="connection-manager__empty-btn">
              Crea prima connessione
            </button>
          </div>
        ) : (
          <div className="connection-manager__grid">
            {connections.map((conn) => {
              const dbType = dbTypes.find(t => t.value === conn.type);
              const isTestingThis = testingConnection === conn.id;
              const testResult = testResults[conn.id];
              
              return (
                <div key={conn.id} className="connection-card">
                  {/* Card Header */}
                  <div className="connection-card__header">
                    <span className="connection-card__icon">{dbType?.label.split(' ')[0]}</span>
                    <div>
                      <h3 className="connection-card__name">{conn.name}</h3>
                      <span 
                        className="connection-card__type-badge"
                        style={{ background: dbType?.color }}
                      >
                        {dbType?.label.split(' ').slice(1).join(' ')}
                      </span>
                    </div>
                  </div>

                  {/* Card Body */}
                  <div className="connection-card__body">
                    {conn.description && (
                      <p className="connection-card__description">{conn.description}</p>
                    )}
                    
                    <div className="connection-card__details">
                      <div className="connection-card__detail-row">
                        <span className="connection-card__detail-label">Host:</span>
                        <span className="connection-card__detail-value">{conn.host}:{conn.port}</span>
                      </div>
                      <div className="connection-card__detail-row">
                        <span className="connection-card__detail-label">Database:</span>
                        <span className="connection-card__detail-value">{conn.database}</span>
                      </div>
                      <div className="connection-card__detail-row">
                        <span className="connection-card__detail-label">Username:</span>
                        <span className="connection-card__detail-value">{conn.username}</span>
                      </div>
                    </div>

                    {/* Test Result */}
                    {testResult && (
                      <div className={`connection-card__test-result connection-card__test-result--${testResult.success ? 'success' : 'error'}`}>
                        <div className={`connection-card__test-title connection-card__test-title--${testResult.success ? 'success' : 'error'}`}>
                          {testResult.success ? '✓ Test riuscito' : '✗ Test fallito'}
                        </div>
                        <div className={`connection-card__test-message--${testResult.success ? 'success' : 'error'}`}>
                          {testResult.message}
                        </div>
                      </div>
                    )}
                  </div>

                  {/* Card Footer */}
                  <div className="connection-card__footer">
                    <button
                      onClick={() => handleTest(conn.id)}
                      disabled={isTestingThis}
                      className="connection-card__btn connection-card__btn--test"
                    >
                      {isTestingThis ? '⏳ Testing...' : '🔍 Test'}
                    </button>
                    <button
                      onClick={() => handleOpenModal(conn)}
                      className="connection-card__btn connection-card__btn--edit"
                    >
                      ✏️ Modifica
                    </button>
                    <button
                      onClick={() => handleDelete(conn.id, conn.name)}
                      className="connection-card__btn connection-card__btn--delete"
                    >
                      🗑️ Elimina
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </main>

      {/* Modal */}
      {showModal && (
        <div className="connection-manager__modal-overlay" onClick={handleCloseModal}>
          <div className="connection-manager__modal" onClick={e => e.stopPropagation()}>
            <h3 className="connection-manager__modal-title">
              {editingConnection ? 'Modifica Connessione' : 'Nuova Connessione'}
            </h3>

            {/* Nome */}
            <div className="connection-manager__form-group">
              <label className="connection-manager__form-label">Nome connessione *</label>
              <input
                type="text"
                value={formData.name}
                onChange={e => setFormData({ ...formData, name: e.target.value })}
                placeholder="Es. 'Database Produzione'"
                className="connection-manager__form-input"
              />
            </div>

            {/* Descrizione */}
            <div className="connection-manager__form-group">
              <label className="connection-manager__form-label">Descrizione</label>
              <textarea
                value={formData.description}
                onChange={e => setFormData({ ...formData, description: e.target.value })}
                placeholder="Descrizione opzionale..."
                rows={2}
                className="connection-manager__form-textarea"
              />
            </div>

            {/* Tipo Database */}
            <div className="connection-manager__form-group">
              <label className="connection-manager__form-label">Tipo Database *</label>
              <div className="connection-manager__db-types">
                {dbTypes.map(type => (
                  <label
                    key={type.value}
                    className={`connection-manager__db-type ${formData.type === type.value ? 'connection-manager__db-type--selected' : ''}`}
                    style={{
                      borderColor: formData.type === type.value ? type.color : '#ddd',
                      background: formData.type === type.value ? `${type.color}15` : 'white'
                    }}
                  >
                    <input
                      type="radio"
                      name="dbType"
                      value={type.value}
                      checked={formData.type === type.value}
                      onChange={e => handleTypeChange(e.target.value)}
                    />
                    <span>{type.label}</span>
                  </label>
                ))}
              </div>
            </div>

            {/* Host e Porta */}
            <div className="connection-manager__form-group">
              <div className="connection-manager__form-row connection-manager__form-row--2-1">
                <div>
                  <label className="connection-manager__form-label">Host *</label>
                  <input
                    type="text"
                    value={formData.host}
                    onChange={e => setFormData({ ...formData, host: e.target.value })}
                    placeholder="localhost o IP"
                    className="connection-manager__form-input"
                  />
                </div>
                <div>
                  <label className="connection-manager__form-label">Porta *</label>
                  <input
                    type="number"
                    value={formData.port}
                    onChange={e => setFormData({ ...formData, port: parseInt(e.target.value) || 0 })}
                    className="connection-manager__form-input"
                  />
                </div>
              </div>
            </div>

            {/* Database */}
            <div className="connection-manager__form-group">
              <label className="connection-manager__form-label">Database *</label>
              <input
                type="text"
                value={formData.database}
                onChange={e => setFormData({ ...formData, database: e.target.value })}
                placeholder="Nome database"
                className="connection-manager__form-input"
              />
            </div>

            {/* Username e Password */}
            <div className="connection-manager__form-group">
              <div className="connection-manager__form-row connection-manager__form-row--1-1">
                <div>
                  <label className="connection-manager__form-label">Username</label>
                  <input
                    type="text"
                    value={formData.username}
                    onChange={e => setFormData({ ...formData, username: e.target.value })}
                    placeholder="Username"
                    className="connection-manager__form-input"
                  />
                </div>
                <div>
                  <label className="connection-manager__form-label">Password</label>
                  <input
                    type="password"
                    value={formData.password}
                    onChange={e => setFormData({ ...formData, password: e.target.value })}
                    placeholder="••••••••"
                    className="connection-manager__form-input"
                  />
                </div>
              </div>
            </div>

            {/* Actions */}
            <div className="connection-manager__form-actions">
              <button onClick={handleCloseModal} className="connection-manager__form-btn connection-manager__form-btn--cancel">
                Annulla
              </button>
              <button onClick={handleSubmit} className="connection-manager__form-btn connection-manager__form-btn--submit">
                {editingConnection ? 'Salva Modifiche' : 'Crea Connessione'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default ConnectionManager;