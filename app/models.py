"""
app/models.py — Shim di backward compatibility.

Tutti i modelli sono definiti in app/shared/models.py (fonte unica di verità).
Questo file ri-esporta tutto per mantenere compatibilità con gli import esistenti.

NON aggiungere modelli qui — usare app/shared/models.py.
"""

from app.shared.models import *  # noqa: F401, F403
from app.shared.models import (
    ColumnType,
    ColumnInfo,
    Position,
    Column,
    Filter,
    AggregationConfig,
    Node,
    Edge,
    CanvasState,
    ValidationError,
    ValidationResult,
    ExecuteRequest,
)

# Re-export modelli di dominio dai rispettivi moduli
try:
    from app.core.projects.models import (
        ProjectMetadata,
        ProjectCreate,
        ProjectList,
    )
except ImportError:
    pass

try:
    from app.core.files.models import (
        FileSource,
        FileSourceCreate,
        FileSourceUpdate,
        FileSourceList,
        FileSourceLayersResponse,
        FileSourcePreview,
    )
except ImportError:
    pass

try:
    from app.core.connections.models import (
        DatabaseConnection,
        ConnectionCreate,
        ConnectionUpdate,
        ConnectionList,
        ConnectionTestResult,
    )
except ImportError:
    pass
