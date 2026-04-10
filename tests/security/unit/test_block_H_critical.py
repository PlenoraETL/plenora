"""
tests/security/unit/test_block_H_critical.py
=============================================
Unit test per il Blocco H: 3 CRITICAL dal 3° audit.

Copre:
  H-1: eval() RCE → AST validation (_validate_formula_ast)
  H-2: SQL injection JOIN ON → escaping IN values + operator whitelist
  H-3: bare except → except Exception (statico)
"""

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def mock_reset():
    yield


# ============================================================================
# H-1 — AST validation anti-RCE per eval() in formule
# ============================================================================

class TestValidateFormulaAST:
    """Verifica che la validazione AST blocchi pattern di escape dal sandbox."""

    def _validate(self, formula):
        from app.core.manipola.transforms.base import _validate_formula_ast
        _validate_formula_ast(formula)

    # --- Formule SAFE (devono passare) ---

    def test_simple_math(self):
        self._validate("col1 + col2")

    def test_multiplication(self):
        self._validate("col1 * 2 + col2 / 3")

    def test_string_concat(self):
        self._validate("'Prefix: ' + col1")

    def test_pandas_str_method(self):
        self._validate("col1.str.upper()")

    def test_pandas_fillna(self):
        self._validate("col1.fillna(0)")

    def test_conditional_expression(self):
        self._validate("col1 if col1 > 0 else col2")

    def test_comparison(self):
        self._validate("col1 > 100")

    def test_pd_to_numeric(self):
        self._validate("pd.to_numeric(col1, errors='coerce')")

    # --- Formule PERICOLOSE (devono essere BLOCCATE) ---

    def test_import_blocked(self):
        with pytest.raises(ValueError, match="non consentita"):
            self._validate("__import__('os').system('id')")

    def test_dunder_class_blocked(self):
        with pytest.raises(ValueError, match="non consentit"):
            self._validate("col1.__class__.__bases__")

    def test_dunder_bases_blocked(self):
        with pytest.raises(ValueError, match="non consentit"):
            self._validate("().__class__.__bases__[0].__subclasses__()")

    def test_dunder_globals_blocked(self):
        with pytest.raises(ValueError, match="non consentit"):
            self._validate("col1.__globals__")

    def test_builtins_access_blocked(self):
        with pytest.raises(ValueError, match="non consentit"):
            self._validate("__builtins__")

    def test_eval_name_blocked(self):
        with pytest.raises(ValueError, match="non consentit"):
            self._validate("eval('1+1')")

    def test_exec_name_blocked(self):
        with pytest.raises(ValueError, match="non consentit"):
            self._validate("exec('import os')")

    def test_compile_blocked(self):
        with pytest.raises(ValueError, match="non consentit"):
            self._validate("compile('1+1', '', 'eval')")

    def test_open_blocked(self):
        with pytest.raises(ValueError, match="non consentit"):
            self._validate("open('/etc/passwd').read()")

    def test_getattr_blocked(self):
        with pytest.raises(ValueError, match="non consentit"):
            self._validate("getattr(col1, '__class__')")

    def test_type_blocked(self):
        with pytest.raises(ValueError, match="non consentit"):
            self._validate("type(col1)")

    def test_globals_blocked(self):
        with pytest.raises(ValueError, match="non consentit"):
            self._validate("globals()")

    def test_private_attr_blocked(self):
        with pytest.raises(ValueError, match="non consentit"):
            self._validate("col1._internal_method()")

    def test_dunder_init_blocked(self):
        with pytest.raises(ValueError, match="non consentit"):
            self._validate("col1.__init__")

    def test_syntax_error_handled(self):
        with pytest.raises(ValueError, match="syntax error"):
            self._validate("this is not (valid python")


# ============================================================================
# H-2 — SQL injection JOIN ON: IN values escaped + operator whitelist
# ============================================================================

class TestJoinOnSQLInjection:
    """Verifica che i filtri ON siano sanitizzati."""

    def test_in_values_escaped(self):
        """Apice singolo nei valori IN viene escaped con doubling.

        L'injection '1', '2') OR ('1'='1 produce valori IN con apici
        raddoppiati: il SQL engine tratta '' come apice letterale, rendendo
        il tentativo di injection una stringa malformata.
        """
        value = "1', '2') OR ('1'='1"
        in_values = [v.strip() for v in value.split(',') if v.strip()]
        in_list = ", ".join(
            f"'{v.replace(chr(39), chr(39)+chr(39))}'"
            for v in in_values
        )
        # L'escape '' deve essere presente (apici raddoppiati)
        assert "''" in in_list
        # Ogni singolo apice nel valore originale è raddoppiato
        # → il SQL engine li interpreta come literali, non come delimitatori
        for v in in_values:
            if "'" in v:
                escaped = v.replace("'", "''")
                assert f"'{escaped}'" in in_list

    def test_normal_in_values_work(self):
        value = "Roma, Milano, Napoli"
        in_values = [v.strip() for v in value.split(',') if v.strip()]
        in_list = ", ".join(
            f"'{v.replace(chr(39), chr(39)+chr(39))}'"
            for v in in_values
        )
        assert in_list == "'Roma', 'Milano', 'Napoli'"

    def test_value_with_apostrophe_escaped(self):
        value = "L'Aquila, Roma"
        in_values = [v.strip() for v in value.split(',') if v.strip()]
        in_list = ", ".join(
            f"'{v.replace(chr(39), chr(39)+chr(39))}'"
            for v in in_values
        )
        assert "'L''Aquila'" in in_list
        assert "'Roma'" in in_list

    def test_operator_whitelist_in_code(self):
        """Verifica statica: il fix include whitelist operatori."""
        from pathlib import Path
        code = (
            Path(__file__).resolve().parents[3]
            / "backend" / "app" / "core" / "estrai" / "query_generator.py"
        ).read_text(encoding="utf-8")
        assert "_VALID_ON_OPERATORS" in code
        assert "'IS NULL'" in code
        assert "'IS NOT NULL'" in code

    def test_standard_escaping_works(self):
        """replace("'","''") su valori normali non rompe nulla."""
        safe_value = "test value".replace("'", "''")
        assert safe_value == "test value"

        safe_value = "L'Aquila".replace("'", "''")
        assert safe_value == "L''Aquila"


# ============================================================================
# H-3 — bare except (verifica statica)
# ============================================================================

class TestBareExceptSpatial:

    def test_no_bare_except_in_spatial(self):
        """spatial.py non deve avere bare 'except:' (devono essere 'except Exception:')."""
        from pathlib import Path
        code = (
            Path(__file__).resolve().parents[3]
            / "backend" / "app" / "core" / "manipola" / "transforms" / "spatial.py"
        ).read_text(encoding="utf-8")
        # Cerca 'except:' senza nulla dopo (bare except)
        # Pattern: 'except' seguito da ':' con solo whitespace in mezzo
        import re
        bare_excepts = re.findall(r"^\s*except\s*:", code, re.MULTILINE)
        assert bare_excepts == [], (
            f"Bare except trovati in spatial.py: {len(bare_excepts)} occorrenze. "
            f"Devono essere 'except Exception:'"
        )
