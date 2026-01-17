"""
Test per API Projects endpoints
NOTA: Richiede server FastAPI in esecuzione su localhost:8000
"""
import requests
from pathlib import Path


BASE_URL = "http://localhost:8000"


def test_create_project():
    """Test creazione progetto"""
    print("\n=== TEST: Create Project ===")
    
    data = {
        "name": "API Test Project",
        "description": "Progetto creato via API"
    }
    
    r = requests.post(f"{BASE_URL}/api/projects", json=data)
    
    assert r.status_code == 200
    result = r.json()
    
    assert "metadata" in result
    assert "canvas" in result
    assert result["metadata"]["name"] == "API Test Project"
    assert result["metadata"]["description"] == "Progetto creato via API"
    assert "id" in result["metadata"]
    
    # Canvas deve essere vuoto
    assert len(result["canvas"]["nodes"]) == 0
    assert len(result["canvas"]["edges"]) == 0
    
    print(f"✓ Status: {r.status_code}")
    print(f"✓ Progetto creato: {result['metadata']['name']}")
    print(f"✓ ID: {result['metadata']['id'][:40]}...")
    
    return result["metadata"]["id"]  # Ritorna ID per test successivi


def test_list_projects():
    """Test lista progetti"""
    print("\n=== TEST: List Projects ===")
    
    # Crea alcuni progetti
    for i in range(3):
        data = {"name": f"List Test {i+1}", "description": f"Descrizione {i+1}"}
        requests.post(f"{BASE_URL}/api/projects", json=data)
    
    # Lista progetti
    r = requests.get(f"{BASE_URL}/api/projects")
    
    assert r.status_code == 200
    result = r.json()
    
    assert "projects" in result
    assert len(result["projects"]) >= 3
    
    print(f"✓ Status: {r.status_code}")
    print(f"✓ Progetti trovati: {len(result['projects'])}")
    for project in result["projects"][:3]:
        print(f"  - {project['name']}")
    
    return True


def test_get_project():
    """Test get progetto specifico"""
    print("\n=== TEST: Get Project ===")
    
    # Crea progetto
    create_data = {"name": "Get Test", "description": "Test recupero"}
    create_response = requests.post(f"{BASE_URL}/api/projects", json=create_data)
    project_id = create_response.json()["metadata"]["id"]
    
    # Recupera progetto
    r = requests.get(f"{BASE_URL}/api/projects/{project_id}")
    
    assert r.status_code == 200
    result = r.json()
    
    assert "metadata" in result
    assert "canvas" in result
    assert result["metadata"]["id"] == project_id
    assert result["metadata"]["name"] == "Get Test"
    
    print(f"✓ Status: {r.status_code}")
    print(f"✓ Progetto recuperato: {result['metadata']['name']}")
    print(f"✓ Canvas: {len(result['canvas']['nodes'])} nodi, {len(result['canvas']['edges'])} edges")
    
    return project_id


def test_update_canvas():
    """Test aggiornamento canvas"""
    print("\n=== TEST: Update Canvas ===")
    
    # Crea progetto
    create_data = {"name": "Canvas Update Test", "description": "Test update canvas"}
    create_response = requests.post(f"{BASE_URL}/api/projects", json=create_data)
    project_id = create_response.json()["metadata"]["id"]
    
    # Crea canvas con nodi e edges
    canvas_data = {
        "nodes": [
            {
                "id": "node_1",
                "type": "source",
                "table_name": "city",
                "alias": "cities",
                "position": {"x": 100, "y": 100},
                "columns": [
                    {"name": "city_id", "alias": "id", "visible": True},
                    {"name": "name", "visible": True}
                ]
            },
            {
                "id": "node_2",
                "type": "source",
                "table_name": "site",
                "alias": None,
                "position": {"x": 400, "y": 100},
                "columns": [
                    {"name": "site_id", "visible": True},
                    {"name": "city_id", "visible": True}
                ]
            }
        ],
        "edges": [
            {
                "id": "edge_1",
                "source": "node_1",
                "target": "node_2",
                "join_type": "left",
                "source_column": "city_id",
                "target_column": "city_id"
            }
        ]
    }
    
    # Aggiorna canvas
    r = requests.put(f"{BASE_URL}/api/projects/{project_id}/canvas", json=canvas_data)
    
    assert r.status_code == 200
    result = r.json()
    assert result["status"] == "ok"
    assert result["project_id"] == project_id
    
    # Verifica recuperando il progetto
    get_response = requests.get(f"{BASE_URL}/api/projects/{project_id}")
    get_result = get_response.json()
    
    assert len(get_result["canvas"]["nodes"]) == 2
    assert len(get_result["canvas"]["edges"]) == 1
    assert get_result["canvas"]["nodes"][0]["table_name"] == "city"
    assert get_result["canvas"]["edges"][0]["join_type"] == "left"
    
    print(f"✓ Status: {r.status_code}")
    print(f"✓ Canvas aggiornato: {len(get_result['canvas']['nodes'])} nodi, {len(get_result['canvas']['edges'])} edges")
    print(f"✓ Nodo 1: {get_result['canvas']['nodes'][0]['table_name']} (alias: {get_result['canvas']['nodes'][0]['alias']})")
    print(f"✓ Edge: {get_result['canvas']['edges'][0]['join_type']} join")
    
    return True


def test_delete_project():
    """Test eliminazione progetto"""
    print("\n=== TEST: Delete Project ===")
    
    # Crea progetto
    create_data = {"name": "Delete Test", "description": "Test eliminazione"}
    create_response = requests.post(f"{BASE_URL}/api/projects", json=create_data)
    project_id = create_response.json()["metadata"]["id"]
    
    # Verifica esiste
    get_response = requests.get(f"{BASE_URL}/api/projects/{project_id}")
    assert get_response.status_code == 200
    
    # Elimina
    r = requests.delete(f"{BASE_URL}/api/projects/{project_id}")
    
    assert r.status_code == 200
    result = r.json()
    assert result["status"] == "deleted"
    assert result["project_id"] == project_id
    
    # Verifica non esiste più
    get_response = requests.get(f"{BASE_URL}/api/projects/{project_id}")
    assert get_response.status_code == 404
    
    print(f"✓ Status: {r.status_code}")
    print(f"✓ Progetto eliminato: {project_id[:40]}...")
    print(f"✓ Verifica 404 dopo eliminazione: OK")
    
    return True


def test_get_nonexistent_project():
    """Test get progetto inesistente"""
    print("\n=== TEST: Get Nonexistent Project ===")
    
    fake_id = "progetto_fake_12345"
    r = requests.get(f"{BASE_URL}/api/projects/{fake_id}")
    
    assert r.status_code == 404
    
    print(f"✓ Status: {r.status_code} (correttamente 404)")
    
    return True


def test_update_nonexistent_project():
    """Test update progetto inesistente"""
    print("\n=== TEST: Update Nonexistent Project ===")
    
    fake_id = "progetto_fake_12345"
    canvas_data = {"nodes": [], "edges": []}
    
    r = requests.put(f"{BASE_URL}/api/projects/{fake_id}/canvas", json=canvas_data)
    
    assert r.status_code == 404
    
    print(f"✓ Status: {r.status_code} (correttamente 404)")
    
    return True


def test_delete_nonexistent_project():
    """Test delete progetto inesistente"""
    print("\n=== TEST: Delete Nonexistent Project ===")
    
    fake_id = "progetto_fake_12345"
    r = requests.delete(f"{BASE_URL}/api/projects/{fake_id}")
    
    assert r.status_code == 404
    
    print(f"✓ Status: {r.status_code} (correttamente 404)")
    
    return True


if __name__ == "__main__":
    print("=" * 60)
    print("TEST API PROJECTS")
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
        ("Create Project", test_create_project),
        ("List Projects", test_list_projects),
        ("Get Project", test_get_project),
        ("Update Canvas", test_update_canvas),
        ("Delete Project", test_delete_project),
        ("Get Nonexistent Project", test_get_nonexistent_project),
        ("Update Nonexistent Project", test_update_nonexistent_project),
        ("Delete Nonexistent Project", test_delete_nonexistent_project)
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