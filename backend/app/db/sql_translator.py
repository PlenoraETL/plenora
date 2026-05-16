"""
Traduttore di funzioni SQL tra dialetti database.

Keeps persistence access isolated from service and router layers.
"""

import json
import re
from pathlib import Path
from typing import List, Optional

from app.shared.contracts.payloads import JsonObject, StringMap
from app.utils.logger import get_logger

logger = get_logger(__name__)


class SQLFunctionTranslator:
    """
    Traduttore di funzioni SQL generiche nei dialetti specifici dei database.

    Pattern Singleton: una sola istanza carica il registry JSON e lo mantiene
    in memoria per tutta la durata dell'applicazione. Il registry viene caricato
    una sola volta al primo accesso.

    Il traduttore supporta:
    - Traduzione singola funzione con parametri nominali
    - Traduzione espressioni complesse con funzioni annidate
    - Helper specifici per predicati spaziali (JOIN geospaziali)
    - Costruzione condizioni buffer + predicato combinate
    """

    _instance = None
    _registry = None

    def __new__(cls):
        """Pattern Singleton: evita ricaricamento del registry ad ogni istanziazione."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._load_registry()
        return cls._instance

    # CARICAMENTO E INTERROGAZIONE REGISTRY

    def _load_registry(self):
        """
        Carica il registry delle funzioni dal file JSON adiacente.

        Il file sql_functions_registry.json contiene tutte le definizioni
        delle funzioni organizzate per categoria (spatial, string, numeric, date).
        In caso di errore, inizializza un registry vuoto per evitare crash.
        """
        registry_path = Path(__file__).parent / 'sql_functions_registry.json'

        try:
            with open(registry_path, 'r', encoding='utf-8') as f:
                self._registry = json.load(f)
        except FileNotFoundError:
            logger.error(f"[GESTITO] File registry non trovato: {registry_path}")
            self._registry = {"_meta": {}, "spatial": {}, "string": {}, "numeric": {}, "date": {}}
        except json.JSONDecodeError as e:
            logger.error(f"[CRASH] Errore parsing JSON del registry: {e}", exc_info=True)
            self._registry = {"_meta": {}, "spatial": {}, "string": {}, "numeric": {}, "date": {}}

    def _conta_funzioni(self) -> int:
        """Conta il numero totale di funzioni definite nel registry (esclude _meta)."""
        count = 0
        for categoria, funzioni in self._registry.items():
            if categoria != '_meta' and isinstance(funzioni, dict):
                count += len(funzioni)
        return count

    @property
    def supported_databases(self) -> List[str]:
        """Lista dei database supportati, come definita in _meta.supported_databases."""
        return self._registry.get('_meta', {}).get('supported_databases', [])

    def get_categories(self) -> List[str]:
        """Lista delle categorie di funzioni (es. spatial, string, numeric, date)."""
        return [k for k in self._registry.keys() if k != '_meta']

    def list_functions(self, category: Optional[str] = None) -> list[JsonObject]:
        """
        Elenca le funzioni disponibili nel registry.

        Args:
            category: se specificata, filtra per categoria (spatial, string, numeric, date)

        Returns:
            Lista di dizionari con campi: name, category, description, params, returns
        """
        functions = []

        for cat_name, cat_functions in self._registry.items():
            if cat_name == '_meta':
                continue
            if category and cat_name != category:
                continue

            if isinstance(cat_functions, dict):
                for func_name, func_def in cat_functions.items():
                    functions.append({
                        'name': func_name,
                        'category': cat_name,
                        'description': func_def.get('description', ''),
                        'params': func_def.get('params', []),
                        'returns': func_def.get('returns', 'any')
                    })

        return functions

    def get_function_info(self, func_name: str) -> Optional[JsonObject]:
        """
        Recupera la definizione completa di una funzione dal registry.

        Cerca in tutte le categorie e restituisce un dizionario con tutti i campi
        della definizione (description, params, returns, dialects) più name e category.

        Returns:
            Dizionario con la definizione, oppure None se la funzione non esiste
        """
        for cat_name, cat_functions in self._registry.items():
            if cat_name == '_meta':
                continue
            if isinstance(cat_functions, dict) and func_name in cat_functions:
                return {
                    'name': func_name,
                    'category': cat_name,
                    **cat_functions[func_name]
                }
        return None

    def has_function(self, func_name: str) -> bool:
        """Verifica se una funzione esiste nel registry."""
        return self.get_function_info(func_name) is not None

    def supports_database(self, func_name: str, db_type: str) -> bool:
        """Verifica se una funzione ha un template per il database specificato."""
        info = self.get_function_info(func_name)
        if not info:
            return False
        return db_type in info.get('dialects', {})

    # TRADUZIONE FUNZIONE SINGOLA

    def translate(
        self,
        func_name: str,
        db_type: str,
        **params
    ) -> str:
        """
        Traduce una funzione nel dialetto SQL del database specificato.

        Pipeline di traduzione:
        1. Cerca la funzione nel registry
        2. Verifica che il database sia tra i dialetti supportati
        3. Recupera il template SQL per quel dialetto
        4. Risolve i parametri (applicando default se definiti)
        5. Sostituisce i placeholder nel template con i valori

        Args:
            func_name: nome della funzione (es. 'ST_Buffer', 'UPPER', 'ROUND')
            db_type: tipo database (postgres, mssql, mysql, oracle, sqlite)
            **params: parametri nominali della funzione

        Returns:
            Stringa SQL tradotta per il database specificato

        Raises:
            ValueError: se la funzione non esiste o il database non è supportato

        Esempi:
            >>> translator.translate('ST_Buffer', 'postgres', geom='t.geom', distance=100)
            'ST_Buffer(t.geom, 100)'
            >>> translator.translate('ST_Buffer', 'mssql', geom='t.geom', distance=100)
            't.geom.STBuffer(100)'
        """
        # Cerca la funzione nel registry
        func_info = self.get_function_info(func_name)
        if not func_info:
            raise ValueError(f"Funzione '{func_name}' non trovata nel registry")

        # Verifica supporto database
        dialects = func_info.get('dialects', {})
        if db_type not in dialects:
            raise ValueError(
                f"Database '{db_type}' non supportato per '{func_name}'. "
                f"Supportati: {list(dialects.keys())}"
            )

        # Recupera template, risolvi parametri e applica
        template = dialects[db_type]
        resolved_params = self._risolvi_parametri(func_info.get('params', []), params)
        sql = self._applica_template(template, resolved_params)

        return sql

    def _risolvi_parametri(
        self,
        definizioni_param: list[JsonObject],
        params_forniti: JsonObject,
    ) -> JsonObject:
        """
        Risolve i parametri applicando i valori di default dalla definizione.

        Logica di risoluzione per ogni parametro definito:
        1. Se fornito esplicitamente → usa il valore fornito
        2. Se ha un default nella definizione → usa il default
        3. Se è obbligatorio e manca → solleva ValueError

        I parametri extra (non nella definizione) vengono passati così come sono,
        permettendo flessibilità per template con placeholder aggiuntivi.
        """
        resolved = {}

        for param_def in definizioni_param:
            name = param_def['name']
            required = param_def.get('required', True)
            default = param_def.get('default')

            if name in params_forniti:
                resolved[name] = params_forniti[name]
            elif default is not None:
                resolved[name] = default
            elif required:
                raise ValueError(f"Parametro obbligatorio mancante: {name}")

        # Passa parametri extra non presenti nella definizione
        for key, value in params_forniti.items():
            if key not in resolved:
                resolved[key] = value

        return resolved

    def _applica_template(self, template: str, params: JsonObject) -> str:
        """
        Sostituisce i placeholder nel template SQL con i valori dei parametri.

        Placeholder supportati:
        - {param} → sostituzione semplice del valore
        - {param} con lista → join con virgola (es. "val1, val2, val3")
        - {param:join:SEP} → join con separatore custom (es. {values:join:' || '})

        Esempio:
            template = "CONCAT({values:join:' || '})"
            params = {"values": ["col1", "col2", "col3"]}
            risultato = "CONCAT(col1 || col2 || col3)"
        """
        result = template

        for key, value in params.items():
            placeholder = '{' + key + '}'

            if isinstance(value, (list, tuple)):
                # Cerca pattern con separatore custom: {key:join:SEPARATORE}
                join_pattern = r'\{' + key + r':join:([^\}]+)\}'
                join_match = re.search(join_pattern, result)

                if join_match:
                    separator = join_match.group(1)
                    joined = separator.join(str(v) for v in value)
                    result = re.sub(join_pattern, joined, result)
                else:
                    # Join standard con virgola
                    result = result.replace(placeholder, ', '.join(str(v) for v in value))
            else:
                result = result.replace(placeholder, str(value))

        return result

    # TRADUZIONE ESPRESSIONI COMPLESSE

    def translate_expression(
        self,
        expression: str,
        db_type: str,
        column_map: Optional[StringMap] = None
    ) -> str:
        """
        Traduce un'espressione SQL complessa contenente più funzioni annidate.

        Strategia: scansione single-pass da sinistra a destra.
        Per ogni funzione trovata nel registry:
          1. Cerca il match più a sinistra nella stringa
          2. Traduce gli argomenti
          3. Sostituisce e riprende la scansione DOPO la posizione del match
             (non da capo) — questo previene loop infiniti quando la traduzione
             produce la stessa sintassi (es. UPPER → UPPER su postgres).

        Args:
            expression: espressione con funzioni generiche
                        (es. "ST_Area(ST_Buffer(geom, 100)) > 1000")
            db_type: tipo database target
            column_map: mappatura opzionale nomi colonne (old_name → new_name)

        Returns:
            Espressione tradotta nel dialetto specifico

        Esempio:
            >>> translator.translate_expression(
            ... "ST_Area(ST_Buffer(t.geom, 100)) > 1000", "mssql"
            ... )
            't.geom.STBuffer(100).STArea > 1000'
        """
        result = expression

        # Processa ogni funzione del registry, ordinate per lunghezza nome decrescente
        for func_name in self._get_all_function_names():
            pattern = rf'{func_name}\s*\(([^()]*(?:\([^()]*\)[^()]*)*)\)'
            search_from = 0  # posizione da cui riprendere la scansione

            while True:
                match = re.search(pattern, result[search_from:])
                if not match:
                    break

                # Aggiusta l'offset al testo originale completo
                abs_start = search_from + match.start()
                abs_end   = search_from + match.end()

                args_str = match.group(1)
                args = self._parsa_argomenti_funzione(args_str)

                try:
                    func_info = self.get_function_info(func_name)
                    param_names = [p['name'] for p in func_info.get('params', [])]

                    kwargs = {}
                    for i, arg in enumerate(args):
                        if i < len(param_names):
                            kwargs[param_names[i]] = arg.strip()

                    translated = self.translate(func_name, db_type, **kwargs)
                    result = result[:abs_start] + translated + result[abs_end:]

                    # Riprendi la scansione DOPO la traduzione inserita
                    # — non da capo, per evitare loop infiniti
                    search_from = abs_start + len(translated)

                except (ValueError, KeyError) as e:
                    logger.warning(f"Impossibile tradurre {func_name}: {e}")
                    # Salta questo match e continua dal carattere successivo
                    search_from = abs_start + 1

        # Applica rimappatura nomi colonne se fornita
        if column_map:
            for old_name, new_name in column_map.items():
                result = re.sub(rf'\b{re.escape(old_name)}\b', new_name, result)

        return result

    def _get_all_function_names(self) -> List[str]:
        """
        Recupera tutti i nomi delle funzioni dal registry.

        Li ordina per lunghezza decrescente per garantire che funzioni con
        nomi più lunghi vengano matchate prima (evita match parziali).
        Es. "ST_Contains" viene cercato prima di "ST_Con".
        """
        names = []
        for cat_name, cat_functions in self._registry.items():
            if cat_name != '_meta' and isinstance(cat_functions, dict):
                names.extend(cat_functions.keys())
        return sorted(names, key=len, reverse=True)

    def _parsa_argomenti_funzione(self, args_str: str) -> List[str]:
        """
        Parsa gli argomenti di una chiamata a funzione, gestendo parentesi annidate.

        Le virgole dentro parentesi interne non vengono trattate come separatori.
        Es: "ST_Buffer(geom, 100), 'text', 42" → ["ST_Buffer(geom, 100)", "'text'", "42"]
        """
        args = []
        current_arg = ''
        depth = 0

        for char in args_str:
            if char == '(':
                depth += 1
                current_arg += char
            elif char == ')':
                depth -= 1
                current_arg += char
            elif char == ',' and depth == 0:
                # Separatore al livello più esterno → nuovo argomento
                args.append(current_arg.strip())
                current_arg = ''
            else:
                current_arg += char

        if current_arg.strip():
            args.append(current_arg.strip())

        return args

    # HELPER PER PREDICATI SPAZIALI

    def get_spatial_predicate(
        self,
        predicate: str,
        db_type: str,
        geom1: str,
        geom2: str,
        **extra_params
    ) -> str:
        """
        Genera la condizione SQL per un predicato spaziale.

        Utile per costruire condizioni di JOIN geospaziali. Mappa nomi
        predicato semplificati alle funzioni SQL corrispondenti nel registry.

        Predicati supportati:
        - intersects → ST_Intersects
        - contains → ST_Contains
        - within → ST_Within
        - dwithin → ST_DWithin (richiede parametro distance)
        - touches → ST_Touches
        - crosses → ST_Crosses
        - overlaps → ST_Overlaps

        Args:
            predicate: nome del predicato (case-insensitive)
            db_type: tipo database
            geom1: colonna geometria lato sinistro
            geom2: colonna geometria lato destro
            **extra_params: parametri aggiuntivi (es. distance per dwithin)

        Returns:
            Condizione SQL tradotta per il database

        Raises:
            ValueError: se il predicato non è supportato
        """
        predicate_map = {
            'intersects': 'ST_Intersects',
            'contains': 'ST_Contains',
            'within': 'ST_Within',
            'dwithin': 'ST_DWithin',
            'touches': 'ST_Touches',
            'crosses': 'ST_Crosses',
            'overlaps': 'ST_Overlaps'
        }

        func_name = predicate_map.get(predicate.lower())
        if not func_name:
            raise ValueError(f"Predicato spaziale non supportato: {predicate}")

        return self.translate(func_name, db_type, geom1=geom1, geom2=geom2, **extra_params)

    def build_buffer_join(
        self,
        db_type: str,
        geom1: str,
        geom2: str,
        distance: float,
        predicate: str = 'intersects'
    ) -> str:
        """
        Costruisce una condizione JOIN con buffer applicato alla seconda geometria.

        Combinazione comune nelle analisi spaziali: prima bufferizza una geometria,
        poi applica un predicato tra la geometria originale e quella bufferizzata.

        Args:
            db_type: tipo database
            geom1: colonna geometria 1 (non modificata)
            geom2: colonna geometria 2 (verrà bufferizzata)
            distance: raggio del buffer
            predicate: predicato spaziale da applicare (default: intersects)

        Returns:
            Condizione SQL completa

        Esempi:
            >>> translator.build_buffer_join('postgres', 't1.geom', 't2.geom', 100)
            'ST_Intersects(t1.geom, ST_Buffer(t2.geom, 100))'
            >>> translator.build_buffer_join('mssql', 't1.geom', 't2.geom', 100)
            't1.geom.STIntersects(t2.geom.STBuffer(100)) = 1'
        """
        # Costruisci il buffer sulla seconda geometria
        buffered = self.translate('ST_Buffer', db_type, geom=geom2, distance=distance)

        # Applica il predicato tra geom1 e la geometria bufferizzata
        return self.get_spatial_predicate(predicate, db_type, geom1=geom1, geom2=buffered)


# ISTANZA SINGLETON GLOBALE E FUNZIONI SHORTCUT

# Istanza globale del traduttore, usata dalle funzioni shortcut sottostanti
sql_translator = SQLFunctionTranslator()


def translate_sql_function(func_name: str, db_type: str, **params) -> str:
    """Shortcut: traduce una singola funzione SQL nel dialetto specificato."""
    return sql_translator.translate(func_name, db_type, **params)


def get_spatial_condition(predicate: str, db_type: str, geom1: str, geom2: str, **params) -> str:
    """Shortcut: genera una condizione di predicato spaziale per JOIN."""
    return sql_translator.get_spatial_predicate(predicate, db_type, geom1, geom2, **params)


def list_sql_functions(category: Optional[str] = None) -> list[JsonObject]:
    """Shortcut: elenca le funzioni disponibili nel registry, opzionalmente filtrate per categoria."""
    return sql_translator.list_functions(category)
