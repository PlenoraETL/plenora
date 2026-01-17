import requests
import time
from pathlib import Path

BASE_URL = "http://localhost:8000"

def test_full_cascade():
    print("\n=== TEST: Esecuzione a Cascata (Estrai -> Manipola) ===")
    
    # 1. Creazione Progetto Estrai (Sorgente)
    proj_estrai = requests.post(f"{BASE_URL}/api/projects", json={
        "name": "Progetto Sorgente",
        "project_type": "estrai"
    }).json()
    id_estrai = proj_estrai["metadata"]["id"]
    print(f"✓ Creato Progetto Estrai: {id_estrai}")

    # 2. Configurazione Canvas Estrai (Nodo Risultato)
    # Simuliamo un canvas minimo con un nodo result
    canvas_estrai = {
        "nodes": [
            {
                "id": "res_1",
                "type": "result",
                "position": {"x": 0, "y": 0},
                "fileName": "output_estrai",
                "format": "csv"
            },
            {
                "id": "src_1",
                "type": "source",
                "position": {"x": -200, "y": 0},
                "table_name": "city", # Assumendo che esista nel tuo catalogo
                "connection_id": "metti_un_id_connessione_valido_qui" 
            }
        ],
        "edges": [{"id": "e1", "source": "src_1", "target": "res_1", "join_type": "inner"}]
    }
    requests.put(f"{BASE_URL}/api/projects/{id_estrai}/canvas", json=canvas_estrai)

    # 3. Creazione Progetto Manipola
    proj_manipola = requests.post(f"{BASE_URL}/api/projects", json={
        "name": "Progetto Manipola",
        "project_type": "manipola"
    }).json()
    id_manipola = proj_manipola["metadata"]["id"]
    print(f"✓ Creato Progetto Manipola: {id_manipola}")

    # 4. Configurazione Canvas Manipola (Punta a Estrai)
    canvas_manipola = {
        "nodes": [
            {
                "id": "p_src_1",
                "type": "project_source",
                "position": {"x": 0, "y": 0},
                "source_project_id": id_estrai # <--- IL COLLEGAMENTO
            },
            {
                "id": "res_man",
                "type": "result",
                "position": {"x": 200, "y": 0},
                "fileName": "finale_manipolato",
                "format": "csv"
            }
        ],
        "edges": [{"id": "e2", "source": "p_src_1", "target": "res_man", "join_type": "inner"}]
    }
    requests.put(f"{BASE_URL}/api/projects/{id_manipola}/canvas", json=canvas_manipola)

    # 5. ESECUZIONE DELLA CASCATA
    print(f"Lancio esecuzione su {id_manipola}...")
    start_time = time.time()
    r = requests.post(f"{BASE_URL}/api/projects/{id_manipola}/execute")
    
    assert r.status_code == 200
    res = r.json()
    
    print(f"✓ Esecuzione completata in {time.time() - start_time:.2f}s")
    print(f"✓ File generato: {res['output_path']}")
    
    # Verifica fisica del file
    assert Path(res['output_path']).exists()
    print("--- TEST SUPERATO ---")

if __name__ == "__main__":
    test_full_cascade()