"""test_330 - cross-db exact spatial predicate pairs.

Focus bug vicinity:
- SQL Server geography ST_Touches emulation;
- Oracle SDO_RELATE ST_Crosses mask;
- exact duplicate point equality across dialects.
"""

from _cross_db_full import run_scenario

run_scenario("spatial_exact_pairs")
