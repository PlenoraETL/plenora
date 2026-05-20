"""test_333 - cross-db CARICA data fidelity.

Focus bug vicinity:
- CARICA must preserve apostrophes, Unicode, emoji and NULL values;
- destination type mapping must stay portable across MySQL, SQL Server and Oracle.
"""

from _cross_db_full import run_scenario

run_scenario("carica_db_fidelity")
