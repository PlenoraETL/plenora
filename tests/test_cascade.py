import requests
from pathlib import Path

BASE_URL = "http://localhost:8000"

def run_cascade_test():
    # 1. Recuperiamo una connessione valida (usiamo la prima della lista)
    conn_list = requests.get(f"{BASE_URL}/api/connections").json()["connections"]
    if not conn_list:
        print("Errore: Nessuna connessione trovata. Esegui prima i test delle connessioni.")
        return
    
    conn_id = conn_list[0]["id"]
    print(f" Usando connessione: {conn_id}")

    # 2. Creiamo Progetto ESTRAI
    print("\n1. Creazione Progetto Estrai...")
    p_estrai = requests.post(f"{BASE_URL}/api/projects", json={
        "name": "Estrai_City",
        "project_type": "estrai"
    }).json()
    id_estrai = p_estrai["metadata"]["id"]

    # Configuriamo il canvas per estrarre dalla tabella 'city' (o un'altra che hai nel DB)

    canvas_estrai = {
        "nodes": [
            {
                "id": "node_src",
                "type": "source",
                "connection_id": conn_id,
                "table_name": "city", 
                "position": {"x": 0, "y": 0},
                # AGGIUNGIAMO LE COLONNE QUI:
                "columns": [
                    {"name": "city_id", "type": "text", "visible": True},
                    {"name": "name", "type": "text", "visible": True},
                    {"name": "state_id", "type": "text", "visible": True}
                ]
            },
            {
                "id": "node_res",
                "type": "result",
                "fileName": "citta_estratte",
                "format": "csv",
                "position": {"x": 300, "y": 0}
            }
        ],
        "edges": [{"id": "e1", "source": "node_src", "target": "node_res", "join_type": "inner"}]
    }
        
    requests.put(f"{BASE_URL}/api/projects/{id_estrai}/canvas", json=canvas_estrai)

    # 3. Creiamo Progetto MANIPOLA
    print("2. Creazione Progetto Manipola...")
    p_manipola = requests.post(f"{BASE_URL}/api/projects", json={
        "name": "Manipola_Dati",
        "project_type": "manipola"
    }).json()
    id_manipola = p_manipola["metadata"]["id"]

    # Configuriamo il canvas per leggere dal progetto Estrai
    canvas_manipola = {
        "nodes": [
            {
                "id": "node_proj_src",
                "type": "project_source",
                "source_project_id": id_estrai,
                "position": {"x": 0, "y": 0}
            },
            {
                "id": "node_final",
                "type": "result",
                "fileName": "report_finale",
                "format": "csv",
                "position": {"x": 300, "y": 0}
            }
        ],
        "edges": [{"id": "e2", "source": "node_proj_src", "target": "node_final", "join_type": "inner"}]
    }
    requests.put(f"{BASE_URL}/api/projects/{id_manipola}/canvas", json=canvas_manipola)

    # 4. ESECUZIONE (Il momento della verità)
    print(f"\n3. Esecuzione Pipeline su: {id_manipola}")
    response = requests.post(f"{BASE_URL}/api/projects/{id_manipola}/execute")
    
    if response.status_code == 200:
        res_data = response.json()
        print(f"\n SUCCESS!")
        print(f" File finale creato in: {res_data['output_path']}")
        print(f" Righe processate: {res_data.get('rows_count')}")
    else:
        print(f"\n FAILED: {response.text}")

if __name__ == "__main__":
    run_cascade_test()