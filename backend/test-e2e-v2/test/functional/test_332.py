"""test_332 - cross-db spatial NULL boundary and false-positive guard.

Focus bug vicinity:
- null region geometry must not participate in spatial joins;
- cross-meridian fixture route must not accidentally match Italian regions.
"""

from _cross_db_full import run_scenario

run_scenario("spatial_null_boundaries")
