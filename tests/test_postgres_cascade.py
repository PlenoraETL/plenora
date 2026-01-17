import requests
from pathlib import Path

BASE_URL = "http://localhost:8000"

# --- CONFIGURA I TUOI DATI REALI QUI ---
POSTGRES_CONFIG = {
    "name": "Postgres_Reale_Test",
    "type": "postgres",
    "host": "ec2-15-161-253-162.eu-south-1.compute.amazonaws.com",      # es. "localhost" o IP del server
    "port": 5432,
    "database": "catasto",
    "username": "postgres",
    "password": "MaGiMaAl2015"
}

TABELLA_REALE = "comuni_fatti_sister"
COLONNE_REALI = ["codice_comune_lungo", "regione"] # Sostituisci con nomi reali
# ---------------------------------------

def test_full_pipeline_with_postgres():
    print("=== AVVIO TEST PIPELINE POSTGRES ===")

    # 1. Registrazione Connessione
    print("1. Registrazione connessione...")
    r_conn = requests.post(f"{BASE_URL}/api/connections", json=POSTGRES_CONFIG)
    if r_conn.status_code != 200:
        print(f"ERRORE Creazione Connessione: {r_conn.text}")
        return
    conn_id = r_conn.json()["id"]

    # 2. Creazione Progetto Estrai
    print("2. Creazione progetto Estrai...")
    p_estrai = requests.post(f"{BASE_URL}/api/projects", json={
        "name": "Estrai_DB_Reale",
        "project_type": "estrai"
    }).json()
    id_estrai = p_estrai["metadata"]["id"]

    canvas_estrai = {
        "nodes": [
            {
                "id": "src_node",
                "type": "source",
                "connection_id": conn_id,
                "table_name": TABELLA_REALE,
                "columns": [{"name": c, "type": "text", "visible": True} for c in COLONNE_REALI],
                "position": {"x": 0, "y": 0}
            },
            {
                "id": "res_node",
                "type": "result",
                "fileName": "estrazione_reale",
                "format": "csv",
                "position": {"x": 300, "y": 0}
            }
        ],
        "edges": [{"id": "e1", "source": "src_node", "target": "res_node", "join_type": "inner"}]
    }
    requests.put(f"{BASE_URL}/api/projects/{id_estrai}/canvas", json=canvas_estrai)

    # 3. Creazione Progetto Manipola
    print("3. Creazione progetto Manipola...")
    p_manipola = requests.post(f"{BASE_URL}/api/projects", json={
        "name": "Manipola_Postgres",
        "project_type": "manipola"
    }).json()
    id_manipola = p_manipola["metadata"]["id"]

    canvas_manipola = {
        "nodes": [
            {
                "id": "proj_src",
                "type": "project_source",
                "source_project_id": id_estrai,
                "position": {"x": 0, "y": 0}
            },
            {
                "id": "final_res",
                "type": "result",
                "fileName": "risultato_finale_pandas",
                "format": "csv",
                "position": {"x": 300, "y": 0}
            }
        ],
        "edges": [{"id": "e2", "source": "proj_src", "target": "final_res", "join_type": "inner"}]
    }
    requests.put(f"{BASE_URL}/api/projects/{id_manipola}/canvas", json=canvas_manipola)

    # 4. Esecuzione Cascata
    print(f"4. Lancio esecuzione su {id_manipola}...")
    resp = requests.post(f"{BASE_URL}/api/projects/{id_manipola}/execute")
    
    if resp.status_code == 200:
        data = resp.json()
        print("\n✅ SUCCESS!")
        print(f"Progetto Estrai eseguito correttamente.")
        print(f"Progetto Manipola ha letto i dati e creato: {data['output_path']}")
        print(f"Righe totali processate: {data['rows_count']}")
    else:
        print(f"\n❌ FAILED: {resp.text}")

if __name__ == "__main__":
    test_full_pipeline_with_postgres()