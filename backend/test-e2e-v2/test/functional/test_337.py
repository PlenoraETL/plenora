"""test_337 - CARICA database write modes.

Focus expanded perimeter:
- create;
- append;
- truncate_insert;
- replace.
"""

from _cross_db_full import run_scenario

run_scenario("carica_db_write_modes")
