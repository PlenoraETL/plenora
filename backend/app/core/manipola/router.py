"""
Router API per il modulo MANIPOLA.

Re-export del router transforms_api per uniformità con gli altri moduli.
"""

from app.core.manipola.api.transforms_api import router

__all__ = ["router"]
