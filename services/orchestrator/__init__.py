"""
Orchestrator Package - Gestisce l'esecuzione delle pipeline ETL.

Moduli:
- estrai_executor: Esecuzione e preview progetti ESTRAI
- manipola_executor: Esecuzione e preview progetti MANIPOLA
- enrich_executor: Esecuzione progetti ARRICCHISCI
- transform_operations: Operazioni di trasformazione base
- spatial_operations: Operazioni geospaziali
"""

from .estrai_executor import EstraiExecutor
from .manipola_executor import ManipolaExecutor
from .enrich_executor import EnrichExecutor
from .transform_operations import TransformOperations
from .spatial_operations import SpatialOperations

__all__ = [
    'EstraiExecutor',
    'ManipolaExecutor', 
    'EnrichExecutor',
    'TransformOperations',
    'SpatialOperations'
]
