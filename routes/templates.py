"""
Templates Router - Endpoint per API templates preconfigurati

Serve i template da data/api_templates.json
Per aggiungere template: modifica solo il JSON, zero codice!
"""
import json
from pathlib import Path
from fastapi import APIRouter, HTTPException
from utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/templates", tags=["templates"])

# Path al file JSON dei template
TEMPLATES_FILE = Path(__file__).parent.parent / "data" / "api_templates.json"


def load_templates() -> list:
    """Carica i template dal file JSON"""
    try:
        if not TEMPLATES_FILE.exists():
            logger.warning(f"File template non trovato: {TEMPLATES_FILE}")
            return []
        
        with open(TEMPLATES_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return data.get('templates', [])
    except json.JSONDecodeError as e:
        logger.error(f"Errore parsing JSON template: {e}")
        return []
    except Exception as e:
        logger.error(f"Errore caricamento template: {e}")
        return []


@router.get("")
def get_templates():
    """
    GET /api/templates
    Ritorna tutti i template API disponibili
    """
    templates = load_templates()
    return {
        "success": True,
        "count": len(templates),
        "templates": templates
    }


@router.get("/categories")
def get_categories():
    """
    GET /api/templates/categories
    Ritorna le categorie disponibili con conteggio
    """
    templates = load_templates()
    
    categories = {}
    for template in templates:
        cat = template.get('_meta', {}).get('category', 'custom')
        if cat not in categories:
            categories[cat] = {
                'id': cat,
                'count': 0,
                'templates': []
            }
        categories[cat]['count'] += 1
        categories[cat]['templates'].append(template.get('_meta', {}).get('id'))
    
    return {
        "success": True,
        "categories": list(categories.values())
    }


@router.get("/{template_id}")
def get_template(template_id: str):
    """
    GET /api/templates/<id>
    Ritorna un singolo template per ID
    """
    templates = load_templates()
    
    for template in templates:
        if template.get('_meta', {}).get('id') == template_id:
            return {
                "success": True,
                "template": template
            }
    
    raise HTTPException(status_code=404, detail=f'Template "{template_id}" non trovato')
