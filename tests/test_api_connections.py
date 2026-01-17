"""
Test per API Connections endpoints
NOTA: Richiede server FastAPI in esecuzione su localhost:8000
"""
import requests


BASE_URL = "http://localhost:8000"


def test_create_connection():
    """Test creazione connessione"""
    print("\n=== TEST: Create Connection ===")
    
    data = {
        "name": "Archibus Test",
        "description": "Connessione di test Archibus",
        "type": "mssql",
        "host": "localhost",
        "port": 1433,
        "database": "archibus_db",
        "username": "test_user",
        "password": "test_password"
    }
    
    r = requests.post(f"{BASE_URL}/api/connections", json=data)
    
    assert r.status_code == 200
    result = r.json()
    
    assert result["name"] == "Archibus Test"
    assert result["type"] == "mssql"
    assert result["host"] == "localhost"
    assert "id" in result
    assert "created_at" in result
    
    print(f"✓ Status: {r.status_code}")
    print(f"✓ Connessione creata: {result['name']}")
    print(f"✓ ID: {result['id'][:40]}...")
    
    return result["id"]


def test_list_connections():
    """Test lista connessioni"""
    print("\n=== TEST: List Connections ===")
    
    # Crea alcune connessioni
    for i in range(3):
        data = {
            "name": f"Test Connection {i+1}",
            "description": f"Descrizione {i+1}",
            "type": "postgres",
            "host": "localhost",
            "port": 5432,
            "database": f"db_{i+1}",
            "username": "user",
            "password": "pass"
        }
        requests.post(f"{BASE_URL}/api/connections", json=data)
    
    # Lista connessioni
    r = requests.get(f"{BASE_URL}/api/connections")
    
    assert r.status_code == 200
    result = r.json()
    
    assert "connections" in result
    assert len(result["connections"]) >= 3
    
    print(f"✓ Status: {r.status_code}")
    print(f"✓ Connessioni trovate: {len(result['connections'])}")
    for conn in result["connections"][:3]:
        print(f"  - {conn['name']} ({conn['type']})")
    
    return True


def test_get_connection():
    """Test get connessione specifica"""
    print("\n=== TEST: Get Connection ===")
    
    # Crea connessione
    create_data = {
        "name": "Get Test Connection",
        "description": "Test recupero",
        "type": "mysql",
        "host": "127.0.0.1",
        "port": 3306,
        "database": "test_db",
        "username": "root",
        "password": "secret"
    }
    create_response = requests.post(f"{BASE_URL}/api/connections", json=create_data)
    connection_id = create_response.json()["id"]
    
    # Recupera connessione
    r = requests.get(f"{BASE_URL}/api/connections/{connection_id}")
    
    assert r.status_code == 200
    result = r.json()
    
    assert result["id"] == connection_id
    assert result["name"] == "Get Test Connection"
    assert result["type"] == "mysql"
    assert result["port"] == 3306
    
    print(f"✓ Status: {r.status_code}")
    print(f"✓ Connessione recuperata: {result['name']}")
    print(f"✓ Tipo: {result['type']}, Host: {result['host']}, DB: {result['database']}")
    
    return connection_id


def test_update_connection():
    """Test aggiornamento connessione"""
    print("\n=== TEST: Update Connection ===")
    
    # Crea connessione
    create_data = {
        "name": "Update Test",
        "description": "Before update",
        "type": "postgres",
        "host": "old-host",
        "port": 5432,
        "database": "old_db",
        "username": "old_user",
        "password": "old_pass"
    }
    create_response = requests.post(f"{BASE_URL}/api/connections", json=create_data)
    connection_id = create_response.json()["id"]
    
    # Aggiorna alcuni campi
    update_data = {
        "name": "Update Test - Modified",
        "description": "After update",
        "host": "new-host",
        "database": "new_db"
    }
    
    r = requests.put(f"{BASE_URL}/api/connections/{connection_id}", json=update_data)
    
    assert r.status_code == 200
    result = r.json()
    
    assert result["id"] == connection_id
    assert result["name"] == "Update Test - Modified"
    assert result["description"] == "After update"
    assert result["host"] == "new-host"
    assert result["database"] == "new_db"
    # Campi non modificati devono rimanere
    assert result["type"] == "postgres"
    assert result["port"] == 5432
    assert result["username"] == "old_user"
    
    print(f"✓ Status: {r.status_code}")
    print(f"✓ Connessione aggiornata: {result['name']}")
    print(f"✓ Nuovo host: {result['host']}, Nuovo DB: {result['database']}")
    
    return True


def test_delete_connection():
    """Test eliminazione connessione"""
    print("\n=== TEST: Delete Connection ===")
    
    # Crea connessione
    create_data = {
        "name": "Delete Test",
        "description": "Test eliminazione",
        "type": "sqlite",
        "host": "",
        "port": 0,
        "database": "test.db",
        "username": "",
        "password": ""
    }
    create_response = requests.post(f"{BASE_URL}/api/connections", json=create_data)
    connection_id = create_response.json()["id"]
    
    # Verifica esiste
    get_response = requests.get(f"{BASE_URL}/api/connections/{connection_id}")
    assert get_response.status_code == 200
    
    # Elimina
    r = requests.delete(f"{BASE_URL}/api/connections/{connection_id}")
    
    assert r.status_code == 200
    result = r.json()
    assert result["status"] == "deleted"
    assert result["connection_id"] == connection_id
    
    # Verifica non esiste più
    get_response = requests.get(f"{BASE_URL}/api/connections/{connection_id}")
    assert get_response.status_code == 404
    
    print(f"✓ Status: {r.status_code}")
    print(f"✓ Connessione eliminata: {connection_id[:40]}...")
    print(f"✓ Verifica 404 dopo eliminazione: OK")
    
    return True


def test_get_nonexistent_connection():
    """Test get connessione inesistente"""
    print("\n=== TEST: Get Nonexistent Connection ===")
    
    fake_id = "connessione_fake_12345"
    r = requests.get(f"{BASE_URL}/api/connections/{fake_id}")
    
    assert r.status_code == 404
    
    print(f"✓ Status: {r.status_code} (correttamente 404)")
    
    return True


def test_update_nonexistent_connection():
    """Test update connessione inesistente"""
    print("\n=== TEST: Update Nonexistent Connection ===")
    
    fake_id = "connessione_fake_12345"
    update_data = {"name": "New Name"}
    
    r = requests.put(f"{BASE_URL}/api/connections/{fake_id}", json=update_data)
    
    assert r.status_code == 404
    
    print(f"✓ Status: {r.status_code} (correttamente 404)")
    
    return True


def test_delete_nonexistent_connection():
    """Test delete connessione inesistente"""
    print("\n=== TEST: Delete Nonexistent Connection ===")
    
    fake_id = "connessione_fake_12345"
    r = requests.delete(f"{BASE_URL}/api/connections/{fake_id}")
    
    assert r.status_code == 404
    
    print(f"✓ Status: {r.status_code} (correttamente 404)")
    
    return True


def test_connection_test_endpoint():
    """Test endpoint test connessione"""
    print("\n=== TEST: Test Connection Endpoint ===")
    
    # Crea connessione (con credenziali fake, fallirà ma testeremo l'endpoint)
    create_data = {
        "name": "Test Endpoint",
        "description": "Test dell'endpoint di test",
        "type": "postgres",
        "host": "fake-host",
        "port": 5432,
        "database": "fake_db",
        "username": "fake_user",
        "password": "fake_pass"
    }
    create_response = requests.post(f"{BASE_URL}/api/connections", json=create_data)
    connection_id = create_response.json()["id"]
    
    # Testa connessione (dovrebbe fallire, ma l'endpoint deve rispondere)
    r = requests.post(f"{BASE_URL}/api/connections/{connection_id}/test")
    
    assert r.status_code == 200
    result = r.json()
    
    assert "success" in result
    assert "message" in result
    # Questa connessione dovrebbe fallire (fake host)
    assert result["success"] == False
    
    print(f"✓ Status: {r.status_code}")
    print(f"✓ Test result: success={result['success']}")
    print(f"✓ Message: {result['message'][:80]}...")
    
    return True


if __name__ == "__main__":
    print("=" * 60)
    print("TEST API CONNECTIONS")
    print("=" * 60)
    print("\nASSICURATI CHE IL SERVER SIA AVVIATO:")
    print("  python main.py")
    print()
    
    # Verifica server attivo
    try:
        requests.get(BASE_URL, timeout=2)
    except:
        print("✗ ERRORE: Server non raggiungibile su", BASE_URL)
        print("Avvia il server e riprova.")
        exit(1)
    
    tests = [
        ("Create Connection", test_create_connection),
        ("List Connections", test_list_connections),
        ("Get Connection", test_get_connection),
        ("Update Connection", test_update_connection),
        ("Delete Connection", test_delete_connection),
        ("Get Nonexistent Connection", test_get_nonexistent_connection),
        ("Update Nonexistent Connection", test_update_nonexistent_connection),
        ("Delete Nonexistent Connection", test_delete_nonexistent_connection),
        ("Test Connection Endpoint", test_connection_test_endpoint)
    ]
    
    results = []
    for name, test_func in tests:
        try:
            test_func()
            results.append((name, True, None))
        except AssertionError as e:
            results.append((name, False, f"Assertion failed: {e}"))
            print(f"✗ ERRORE: {e}")
        except Exception as e:
            results.append((name, False, str(e)))
            print(f"✗ ERRORE: {e}")
    
    # Riepilogo
    print("\n" + "=" * 60)
    print("RIEPILOGO TEST")
    print("=" * 60)
    
    for name, passed, error in results:
        if passed:
            print(f"✓ PASS - {name}")
        else:
            print(f"✗ FAIL - {name}")
            if error:
                print(f"  Errore: {error}")
    
    total = len(results)
    passed_count = sum(1 for _, p, _ in results if p)
    print(f"\nTotale: {passed_count}/{total} test passati")
