import requests
import json

BASE_URL = "http://localhost:8000/api/projects"

# ID dei tuoi progetti reali
ID_FOGLI = "prova_20260112_160957_31bacacc"
ID_PARTICELLE = "prova2_20260112_183640_df204f97"

def run_test():
    print("--- 1. Creazione Progetto Manipola ---")
    proj_res = requests.post(BASE_URL, json={
        "name": "Report Integrato Catasto",
        "project_type": "manipola",
        "description": "Unione reale di Fogli e Particelle"
    })
    
    if proj_res.status_code != 200:
        print(f"Errore creazione: {proj_res.text}")
        return
        
    manipola_id = proj_res.json()["metadata"]["id"]
    print(f"✅ Progetto creato con ID: {manipola_id}")

    print("\n--- 2. Configurazione Canvas (Join + Formula) ---")
    canvas_payload = {
        "nodes": [
            {
                "id": "src_fogli",
                "type": "project_source",
                "source_project_id": ID_FOGLI,
                "position": {"x": 100, "y": 100}
            },
            {
                "id": "src_particelle",
                "type": "project_source",
                "source_project_id": ID_PARTICELLE,
                "position": {"x": 100, "y": 300}
            },
            {
                "id": "op_join",
                "type": "transform",
                "transform_op": "join",
                "data": {
                    "config": {
                        "left_on": "cod_comune",
                        "right_on": "cod_comune",
                        "how": "inner"
                    }
                },
                "position": {"x": 400, "y": 200}
            },
            {
                "id": "op_formula",
                "type": "transform",
                "transform_op": "formula",
                "data": {
                    "config": {
                        "new_column": "particelle_incrementate",
                        "formula": "num_particelle_terreeni * 1.1"
                    }
                },
                "position": {"x": 700, "y": 200}
            },
            {
                "id": "result",
                "type": "result",
                "fileName": "output_integrazione_reale",
                "format": "csv",
                "position": {"x": 1000, "y": 200}
            }
        ],
        "edges": [
            {"id": "e1", "source": "src_fogli", "target": "op_join", "join_type": "left"},
            {"id": "e2", "source": "src_particelle", "target": "op_join", "join_type": "left"},
            {"id": "e3", "source": "op_join", "target": "op_formula", "join_type": "left"},
            {"id": "e4", "source": "op_formula", "target": "result", "join_type": "left"}
        ]
    }

    res_canvas = requests.put(f"{BASE_URL}/{manipola_id}/canvas", json=canvas_payload)
    if res_canvas.status_code == 200:
        print("✅ Canvas configurato correttamente")
    else:
        print(f"❌ Errore Canvas: {res_canvas.text}")
        return

    print("\n--- 3. Esecuzione Pipeline ---")
    # L'orchestratore caricherà i dati da Excel/CSV dei progetti sorgente
    res_exec = requests.post(f"{BASE_URL}/{manipola_id}/execute")
    
    if res_exec.status_code == 200:
        data = res_exec.json()
        print("🚀 ESECUZIONE COMPLETATA!")
        print(f"File: {data.get('output_path')}")
        print(f"Righe: {data.get('rows_count')}")
        print(f"Colonne finali: {data.get('columns')}")
    else:
        print(f"❌ Errore Esecuzione: {res_exec.text}")

if __name__ == "__main__":
    run_test()