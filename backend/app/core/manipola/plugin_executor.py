"""
Sistema plugin per trasformazioni MANIPOLA.

Architettura
============
Questo modulo implementa un sistema di caricamento dinamico delle trasformazioni
basato su un registry JSON. Permette di aggiungere nuove trasformazioni senza
modificare il codice del core, semplicemente:
1. Scrivendo la funzione Python in transforms/base.py o transforms/spatial.py
2. Aggiungendo l'entry nel transforms_registry.json

Il registry JSON definisce per ogni trasformazione:
- Metadati: nome, icona, descrizione, categoria
- Percorso funzione: "modulo.nome_funzione" (es. "base.op_join" → app.core.manipola.transforms.base.op_join)
- Parametri: tipo, default, validazione, dipendenze condizionali (depends_on)
- Tipo input: single/multi per supportare join e operazioni multi-tabella

Componenti:
- PluginExecutor: classe principale con caricamento dinamico e esecuzione
- get_executor(): singleton globale per accesso rapido
- reload_executor(): ricarica registry a caldo (sviluppo)
- PluginExecutorCompatibility: layer di compatibilità col vecchio sistema

Ciclo di vita:
1. __init__: carica il registry JSON e importa dinamicamente le funzioni Python
2. execute(): riceve transform_id, lista DataFrame di input, nodo con config
3. _prepara_kwargs(): mappa la config del nodo → parametri funzione (gestisce depends_on, default, conversione tipi)
4. Invoca la funzione Python con i kwargs preparati

Conversione tipi parametri:
- number: float se contiene '.', altrimenti int
- boolean: true/1/yes/si → True
- columns/string_list: split su virgola se stringa

Uso:
    from app.core.manipola.plugin_executor import get_executor

    executor = get_executor()
    result_df = executor.execute('geo_buffer', [input_df], node)
"""

import json
import importlib
from pathlib import Path
from typing import Dict, Any, List, Callable, Optional, Union

import pandas as pd
import geopandas as gpd

from app.models import Node
from app.utils.logger import get_logger

logger = get_logger(__name__)


class PluginExecutor:
    """
    Executor con caricamento dinamico delle trasformazioni dal registry JSON.

    Le funzioni vengono importate dinamicamente al formato:
    "base.op_join" → app.core.manipola.transforms.base.op_join
    "spatial.op_geo_buffer" → app.core.manipola.transforms.spatial.op_geo_buffer
    """

    def __init__(self, registry_path: Optional[str] = None):
        """
        Args:
            registry_path: path al file transforms_registry.json.
                          Se None, usa il path di default nella stessa directory.

        Raises:
            ValueError: se `registry_path` e' passato e punta fuori dal
                        perimetro di `app/core/manipola/`.
        """
        _allowed_root = Path(__file__).parent.resolve()

        if registry_path is None:
            registry_path = _allowed_root / "transforms_registry.json"
        else:
            # Difesa in profondità: registry_path deve restare dentro
            # app/core/manipola/. Il registry JSON definisce quali
            # funzioni Python il plugin executor importera' via
            # importlib.import_module: e' il vero input-sensitive del
            # modulo, non il codice delle transform. Senza questo guard,
            # un call site futuro che accettasse un path da request body
            # farebbe caricare un JSON arbitrario dal filesystem e
            # aprirebbe la catena: JSON controllato → function name
            # controllato → importlib su dotted path (confinato al
            # namespace app.core.manipola.transforms, ma comunque
            # indesiderato). Il check e' fail-closed: fuori perimetro
            # → ValueError, niente fallback silenzioso.
            registry_path = Path(registry_path).resolve()
            if not registry_path.is_relative_to(_allowed_root):
                raise ValueError(
                    f"registry_path fuori dal perimetro di "
                    f"{_allowed_root}: {registry_path}"
                )

        self.registry_path = registry_path
        self.registry: Dict[str, Dict] = {}      # transform_id → config completa
        self.categories: Dict[str, Dict] = {}     # category_id → metadati categoria
        self.functions: Dict[str, Callable] = {}  # transform_id → funzione Python

        self._carica_registry()
        self._carica_funzioni()

    # =========================================================================
    # CARICAMENTO
    # =========================================================================

    def _carica_registry(self):
        """Carica il registry JSON con definizioni trasformazioni e categorie."""
        try:
            with open(self.registry_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            self.registry = data.get('transforms', {})
            self.categories = data.get('categories', {})

            logger.info(
                f"Caricato registry: {len(self.registry)} trasformazioni, "
                f"{len(self.categories)} categorie"
            )
        except FileNotFoundError:
            logger.error(f"[GESTITO] Registry non trovato: {self.registry_path}")
            raise
        except json.JSONDecodeError as e:
            logger.error(f"[CRASH] Errore parsing registry JSON: {e}", exc_info=True)
            raise

    def _carica_funzioni(self):
        """
        Importa dinamicamente tutte le funzioni dal registry.

        Il campo 'function' nel registry ha formato "modulo.nome_funzione":
        - "base.op_join" → app.core.manipola.transforms.base.op_join
        - "spatial.op_geo_buffer" → app.core.manipola.transforms.spatial.op_geo_buffer
        """
        for transform_id, config in self.registry.items():
            func_path = config.get('function')
            if not func_path:
                logger.warning(f"Trasformazione '{transform_id}' senza funzione definita")
                continue

            # Separa "modulo.funzione" → ("modulo", "funzione")
            parts = func_path.rsplit('.', 1)
            if len(parts) != 2:
                logger.warning(f"Path funzione invalido per '{transform_id}': {func_path}")
                continue

            module_name, func_name = parts

            try:
                full_module = f"app.core.manipola.transforms.{module_name}"
                module = importlib.import_module(full_module)
                func = getattr(module, func_name)
                self.functions[transform_id] = func
            except ImportError as e:
                logger.error(f"[CRASH] Errore import '{transform_id}': {e}", exc_info=True)
            except AttributeError as e:
                logger.error(f"[CRASH] Funzione '{func_name}' non trovata in '{module_name}': {e}", exc_info=True)

    def reload(self):
        """Ricarica registry e funzioni. Utile durante lo sviluppo."""
        self._carica_registry()
        self._carica_funzioni()

    # =========================================================================
    # INTERROGAZIONE REGISTRY
    # =========================================================================

    def get_registry(self) -> Dict[str, Dict]:
        """Restituisce il registry completo (usato dall'API frontend)."""
        return self.registry

    def get_categories(self) -> Dict[str, Dict]:
        """Restituisce le categorie definite nel registry."""
        return self.categories

    def get_transform_config(self, transform_id: str) -> Optional[Dict]:
        """Restituisce la configurazione di una singola trasformazione."""
        return self.registry.get(transform_id)

    def get_transforms_by_category(self, category: str) -> Dict[str, Dict]:
        """Restituisce tutte le trasformazioni di una categoria."""
        return {
            tid: config
            for tid, config in self.registry.items()
            if config.get('category') == category
        }

    def list_transforms(self) -> List[Dict]:
        """Elenca tutte le trasformazioni con informazioni sintetiche per la UI."""
        return [
            {
                'id': tid,
                'name': config.get('name'),
                'category': config.get('category'),
                'icon': config.get('icon'),
                'description': config.get('description')
            }
            for tid, config in self.registry.items()
        ]

    # =========================================================================
    # ESECUZIONE
    # =========================================================================

    def execute(
        self,
        transform_id: str,
        inputs: List[Union[pd.DataFrame, gpd.GeoDataFrame]],
        node: Node
    ) -> Union[pd.DataFrame, gpd.GeoDataFrame]:
        """
        Esegue una trasformazione con la configurazione dal nodo canvas.

        Args:
            transform_id: ID trasformazione (es. "geo_buffer", "join")
            inputs: lista DataFrame/GeoDataFrame di input
            node: nodo del canvas con configurazione in node.data.config

        Returns:
            DataFrame/GeoDataFrame risultato

        Raises:
            ValueError: se trasformazione non trovata o parametri invalidi
        """
        config = self.registry.get(transform_id)
        if not config:
            raise ValueError(f"Trasformazione '{transform_id}' non trovata nel registry")

        func = self.functions.get(transform_id)
        if not func:
            raise ValueError(f"Funzione per '{transform_id}' non caricata")

        # Estrai configurazione dal nodo canvas (formato flat o legacy data wrapper)
        node_config = node.config or (node.data.get('config', {}) if node.data else {})
        kwargs = self._prepara_kwargs(config, node_config)


        try:
            # Le trasformazioni 'multi' ricevono la lista completa di input
            # (es. join tra due tabelle). Le 'single' ricevono solo il primo DataFrame.
            if config.get('inputs') == 'multi':
                return func(inputs, **kwargs)
            else:
                if not inputs:
                    raise ValueError("Nessun input fornito")
                return func(inputs[0], **kwargs)
        except Exception as e:
            logger.error(f"[CRASH] Errore esecuzione '{transform_id}': {e}", exc_info=True)
            raise

    def execute_with_params(
        self,
        transform_id: str,
        inputs: List[Union[pd.DataFrame, gpd.GeoDataFrame]],
        params: Dict[str, Any]
    ) -> Union[pd.DataFrame, gpd.GeoDataFrame]:
        """
        Esegue una trasformazione con parametri diretti (senza Node).

        Utile per test e uso programmatico dove non si ha un nodo canvas.

        Args:
            transform_id: ID trasformazione
            inputs: lista DataFrame input
            params: dict parametri diretti (come se fossero node.data.config)

        Returns:
            DataFrame risultato
        """
        config = self.registry.get(transform_id)
        if not config:
            raise ValueError(f"Trasformazione '{transform_id}' non trovata")

        func = self.functions.get(transform_id)
        if not func:
            raise ValueError(f"Funzione per '{transform_id}' non caricata")

        kwargs = self._prepara_kwargs(config, params)

        if config.get('inputs') == 'multi':
            return func(inputs, **kwargs)
        else:
            return func(inputs[0], **kwargs)

    # =========================================================================
    # PREPARAZIONE PARAMETRI
    # =========================================================================

    def _prepara_kwargs(self, transform_config: Dict, node_config: Dict) -> Dict[str, Any]:
        """
        Prepara i kwargs per la funzione dalla configurazione del nodo.

        Logica per ogni parametro definito nel registry:
        1. Se depends_on non è soddisfatto → salta (parametro condizionale)
        2. Se presente in node_config → usa quel valore
        3. Se ha un default nel registry → usa il default
        4. Se è obbligatorio e mancante → log warning, usa None
        5. Se è opzionale e mancante → salta

        Tutti i valori vengono convertiti al tipo corretto tramite _converti_valore().
        """
        kwargs = {}
        parameters = transform_config.get('parameters', [])

        for param in parameters:
            param_id = param['id']

            # --- Dipendenze condizionali ---
            # Es: il parametro "custom_value" dipende da "mode" == "custom"
            if 'depends_on' in param:
                dep = param['depends_on']
                dep_param_id = dep['param']
                dep_expected = dep['value']
                
                # Cerca il valore corrente, oppure il default del parametro dipendente
                if dep_param_id in node_config:
                    dep_value = node_config[dep_param_id]
                else:
                    # Cerca il default nel registry
                    dep_param_def = next((p for p in parameters if p['id'] == dep_param_id), None)
                    dep_value = dep_param_def.get('default') if dep_param_def else None
                
                # Supporta array di valori possibili
                if isinstance(dep_expected, list):
                    if dep_value not in dep_expected:
                        continue  # Dipendenza non soddisfatta
                else:
                    if dep_value != dep_expected:
                        continue  # Dipendenza non soddisfatta

            # --- Risoluzione valore: nodo → default → warning/skip ---
            if param_id in node_config:
                value = node_config[param_id]
            elif 'default' in param:
                value = param['default']
            elif param.get('required'):
                logger.warning(f"Parametro obbligatorio '{param_id}' mancante, uso None")
                value = None
            else:
                continue  # Parametro opzionale non fornito

            # Conversione al tipo corretto
            value = self._converti_valore(value, param.get('type'))
            kwargs[param_id] = value

        return kwargs

    def _converti_valore(self, value: Any, param_type: Optional[str]) -> Any:
        """
        Converte un valore al tipo corretto in base alla definizione nel registry.

        Conversioni supportate:
        - number: float se contiene '.', altrimenti int. Stringa vuota → None
        - boolean: "true"/"1"/"yes"/"si" → True (case-insensitive)
        - columns/string_list: stringa con virgole → lista di stringhe pulite
        """
        if value is None or param_type is None:
            return value

        try:
            if param_type == 'number':
                if isinstance(value, str) and value.strip() == '':
                    return None
                return float(value) if '.' in str(value) else int(value)

            elif param_type == 'boolean':
                if isinstance(value, str):
                    return value.lower() in ('true', '1', 'yes', 'si')
                return bool(value)

            elif param_type in ('columns', 'string_list'):
                if isinstance(value, str):
                    return [v.strip() for v in value.split(',') if v.strip()]
                return value if isinstance(value, list) else [value]

            elif param_type == 'aggregation_list':
                # Aggregazioni: deve essere una lista di dict {column, function, ...}
                if isinstance(value, str):
                    import json as _json
                    try:
                        parsed = _json.loads(value)
                        return parsed if isinstance(parsed, list) else []
                    except (ValueError, TypeError):
                        logger.warning(f"aggregation_list non parsabile: {value[:100]}")
                        return []
                if isinstance(value, list):
                    return value
                return []

            elif param_type == 'pivot_mapping':
                # Il mapping pivot arriva come dict {valore_sorgente: nome_output}
                # Se il frontend lo serializza come stringa JSON, deserializziamo
                if isinstance(value, str):
                    import json as _json
                    try:
                        parsed = _json.loads(value)
                        return parsed if isinstance(parsed, dict) else {}
                    except (ValueError, TypeError):
                        return {}
                return value if isinstance(value, dict) else {}

        except (ValueError, TypeError) as e:
            logger.warning(f"Errore conversione valore '{value}' a {param_type}: {e}")

        return value

    # =========================================================================
    # VALIDAZIONE
    # =========================================================================

    def validate_config(self, transform_id: str, node_config: Dict) -> List[str]:
        """
        Valida la configurazione di una trasformazione prima dell'esecuzione.

        Controlla:
        - Parametri obbligatori presenti (rispettando depends_on)
        - Valori numerici entro range min/max
        - Valori select tra le opzioni ammesse

        Args:
            transform_id: ID trasformazione
            node_config: configurazione da validare

        Returns:
            lista di errori (vuota se tutto valido)
        """
        errors = []
        config = self.registry.get(transform_id)

        if not config:
            return [f"Trasformazione '{transform_id}' non trovata"]

        parameters = config.get('parameters', [])

        for param in parameters:
            param_id = param['id']
            value = node_config.get(param_id)

            # Salta parametri con dipendenza condizionale non soddisfatta
            if 'depends_on' in param:
                dep = param['depends_on']
                dep_value = node_config.get(dep['param'])
                if dep_value != dep['value']:
                    continue

            # Verifica campi obbligatori
            if param.get('required') and (value is None or value == ''):
                errors.append(f"'{param.get('label', param_id)}' è obbligatorio")
                continue

            if value is None or value == '':
                continue

            # Validazione specifica per tipo
            param_type = param.get('type')

            if param_type == 'number':
                try:
                    num_val = float(value)
                    if 'min' in param and num_val < param['min']:
                        errors.append(f"'{param.get('label', param_id)}' deve essere >= {param['min']}")
                    if 'max' in param and num_val > param['max']:
                        errors.append(f"'{param.get('label', param_id)}' deve essere <= {param['max']}")
                except (ValueError, TypeError):
                    errors.append(f"'{param.get('label', param_id)}' deve essere un numero")

            elif param_type == 'select':
                options = param.get('options', [])
                valid_values = [
                    o['value'] if isinstance(o, dict) else o
                    for o in options
                ]
                if value not in valid_values:
                    errors.append(f"'{param.get('label', param_id)}' deve essere uno di: {valid_values}")

        return errors

    def get_default_config(self, transform_id: str) -> Dict[str, Any]:
        """
        Restituisce la configurazione di default per una trasformazione.

        Utile per pre-popolare la UI quando l'utente aggiunge un nodo trasformazione.

        Returns:
            dict con param_id → valore_default per tutti i parametri con default
        """
        config = self.registry.get(transform_id, {})
        defaults = {}

        for param in config.get('parameters', []):
            if 'default' in param:
                defaults[param['id']] = param['default']

        return defaults


# =============================================================================
# SINGLETON
# =============================================================================

_executor_instance: Optional[PluginExecutor] = None


def get_executor() -> PluginExecutor:
    """
    Restituisce l'istanza singleton dell'executor.

    L'istanza viene creata al primo accesso e riutilizzata per tutti
    i successivi, evitando di ricaricare il registry ad ogni trasformazione.

    Uso:
        from app.core.manipola.plugin_executor import get_executor

        executor = get_executor()
        result = executor.execute('geo_buffer', [df], node)
    """
    global _executor_instance
    if _executor_instance is None:
        _executor_instance = PluginExecutor()
    return _executor_instance


def reload_executor():
    """Ricarica l'executor a caldo. Utile per sviluppo e test."""
    global _executor_instance
    if _executor_instance:
        _executor_instance.reload()
    else:
        _executor_instance = PluginExecutor()


# =============================================================================
# LAYER DI COMPATIBILITÀ
# =============================================================================

class PluginExecutorCompatibility:
    """
    Layer di compatibilità per integrare PluginExecutor con ManipolaExecutor.

    Permette una migrazione graduale dal vecchio sistema di dispatch
    al nuovo sistema basato su plugin. Se una trasformazione è definita
    nel registry, viene eseguita dal PluginExecutor; altrimenti si usa
    il fallback al vecchio sistema.

    Uso nel ManipolaExecutor._dispatch_transform():

        def _dispatch_transform(self, node, inputs):
            transform_op = node.transform_op or node.data.get('transform_op')
            result = PluginExecutorCompatibility.dispatch(transform_op, inputs, node)
            if result is not None:
                return result
            # Fallback al vecchio sistema...
    """

    @staticmethod
    def is_plugin_transform(transform_id: str) -> bool:
        """Verifica se una trasformazione è gestita dal plugin system."""
        executor = get_executor()
        return executor.get_transform_config(transform_id) is not None

    @staticmethod
    def dispatch(
        transform_id: str,
        inputs: List[Union[pd.DataFrame, gpd.GeoDataFrame]],
        node: Node
    ) -> Optional[Union[pd.DataFrame, gpd.GeoDataFrame]]:
        """
        Esegue la trasformazione se presente nel registry, altrimenti restituisce None.

        Returns:
            DataFrame risultato, o None se trasformazione non gestita dal plugin system
        """
        executor = get_executor()

        if not executor.get_transform_config(transform_id):
            return None

        return executor.execute(transform_id, inputs, node)