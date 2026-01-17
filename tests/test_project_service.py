"""
Test per ProjectService
"""
import sys
from pathlib import Path
import shutil

# Aggiungi backend al path per import
backend_path = Path(__file__).parent.parent
sys.path.insert(0, str(backend_path))

from services.project_service import ProjectService
from models import ProjectCreate, CanvasState, Node, Edge, Position, Column


def setup_test_env():
    """Setup ambiente test (pulisce progetti esistenti)"""
    test_dir = Path("./data/projects_test")
    if test_dir.exists():
        shutil.rmtree(test_dir)
    test_dir.mkdir(parents=True, exist_ok=True)
    return test_dir


def test_create_project():
    """Test creazione progetto"""
    print("\n=== TEST: Create Project ===")
    
    service = ProjectService()
    
    # Crea progetto
    data = ProjectCreate(
        name="Test Project",
        description="Progetto di test"
    )
    
    metadata = service.create_project(data)
    
    assert metadata.name == "Test Project"
    assert metadata.description == "Progetto di test"
    assert metadata.id is not None
    assert "Test_Project" in metadata.id  # Nome sanitizzato
    
    # Verifica cartella creata
    project_path = service._get_project_path(metadata.id)
    assert project_path.exists()
    
    # Verifica file metadata e canvas
    assert (project_path / "metadata.json").exists()
    assert (project_path / "canvas.json").exists()
    
    print(f"✓ Progetto creato: {metadata.id}")
    print(f"✓ Nome: {metadata.name}")
    print(f"✓ Descrizione: {metadata.description}")
    
    return metadata.id  # Ritorna ID per test successivi


def test_list_projects():
    """Test lista progetti"""
    print("\n=== TEST: List Projects ===")
    
    service = ProjectService()
    
    # Crea alcuni progetti
    for i in range(3):
        data = ProjectCreate(
            name=f"Progetto {i+1}",
            description=f"Descrizione progetto {i+1}"
        )
        service.create_project(data)
    
    # Lista progetti
    project_list = service.list_projects()
    
    assert len(project_list.projects) >= 3
    
    print(f"✓ Progetti trovati: {len(project_list.projects)}")
    for project in project_list.projects[:3]:
        print(f"  - {project.name} ({project.id[:30]}...)")
    
    return True


def test_get_project():
    """Test get progetto specifico"""
    print("\n=== TEST: Get Project ===")
    
    service = ProjectService()
    
    # Crea progetto
    data = ProjectCreate(name="Get Test", description="Test get")
    metadata = service.create_project(data)
    
    # Recupera metadata
    loaded_metadata = service.get_project_metadata(metadata.id)
    assert loaded_metadata is not None
    assert loaded_metadata.id == metadata.id
    assert loaded_metadata.name == metadata.name
    
    # Recupera canvas
    canvas = service.get_project_canvas(metadata.id)
    assert canvas is not None
    assert len(canvas.nodes) == 0  # Canvas vuoto
    assert len(canvas.edges) == 0
    
    print(f"✓ Metadata recuperato: {loaded_metadata.name}")
    print(f"✓ Canvas recuperato: {len(canvas.nodes)} nodi, {len(canvas.edges)} edges")
    
    return metadata.id


def test_update_canvas():
    """Test aggiornamento canvas"""
    print("\n=== TEST: Update Canvas ===")
    
    service = ProjectService()
    
    # Crea progetto
    data = ProjectCreate(name="Canvas Test", description="Test canvas")
    metadata = service.create_project(data)
    
    # Crea canvas con nodi e edges
    node1 = Node(
        id="node_1",
        type="source",
        table_name="city",
        alias="cities",
        position=Position(x=100, y=100),
        columns=[
            Column(name="city_id", alias="id", visible=True),
            Column(name="name", visible=True)
        ]
    )
    
    node2 = Node(
        id="node_2",
        type="source",
        table_name="site",
        position=Position(x=400, y=100),
        columns=[
            Column(name="site_id", visible=True),
            Column(name="city_id", visible=True)
        ]
    )
    
    edge1 = Edge(
        id="edge_1",
        source="node_1",
        target="node_2",
        join_type="left",
        source_column="city_id",
        target_column="city_id"
    )
    
    canvas = CanvasState(
        nodes=[node1, node2],
        edges=[edge1]
    )
    
    # Aggiorna canvas
    success = service.update_canvas(metadata.id, canvas)
    assert success
    
    # Ricarica e verifica
    loaded_canvas = service.get_project_canvas(metadata.id)
    assert len(loaded_canvas.nodes) == 2
    assert len(loaded_canvas.edges) == 1
    assert loaded_canvas.nodes[0].table_name == "city"
    assert loaded_canvas.edges[0].join_type == "left"
    
    print(f"✓ Canvas aggiornato: {len(loaded_canvas.nodes)} nodi, {len(loaded_canvas.edges)} edges")
    print(f"✓ Nodo 1: {loaded_canvas.nodes[0].table_name} (alias: {loaded_canvas.nodes[0].alias})")
    print(f"✓ Edge 1: {loaded_canvas.edges[0].source} → {loaded_canvas.edges[0].target}")
    
    return True


def test_delete_project():
    """Test eliminazione progetto"""
    print("\n=== TEST: Delete Project ===")
    
    service = ProjectService()
    
    # Crea progetto
    data = ProjectCreate(name="Delete Test", description="Test delete")
    metadata = service.create_project(data)
    project_id = metadata.id
    
    # Verifica esiste
    assert service.project_exists(project_id)
    
    # Elimina
    success = service.delete_project(project_id)
    assert success
    
    # Verifica non esiste più
    assert not service.project_exists(project_id)
    assert service.get_project_metadata(project_id) is None
    
    print(f"✓ Progetto eliminato: {project_id[:40]}...")
    print(f"✓ Verifica non esiste più: OK")
    
    return True


def test_project_not_found():
    """Test progetto inesistente"""
    print("\n=== TEST: Project Not Found ===")
    
    service = ProjectService()
    
    fake_id = "progetto_inesistente_12345"
    
    assert service.get_project_metadata(fake_id) is None
    assert service.get_project_canvas(fake_id) is None
    assert not service.update_canvas(fake_id, CanvasState())
    assert not service.delete_project(fake_id)
    assert not service.project_exists(fake_id)
    
    print(f"✓ Progetto inesistente gestito correttamente")
    
    return True


if __name__ == "__main__":
    print("=" * 60)
    print("TEST PROJECT SERVICE")
    print("=" * 60)
    
    tests = [
        ("Create Project", test_create_project),
        ("List Projects", test_list_projects),
        ("Get Project", test_get_project),
        ("Update Canvas", test_update_canvas),
        ("Delete Project", test_delete_project),
        ("Project Not Found", test_project_not_found)
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