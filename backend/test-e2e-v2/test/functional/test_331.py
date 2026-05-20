"""test_331 - cross-db inverse spatial predicate consistency.

Focus bug vicinity:
- ST_Contains(region, city) must match ST_Within(city, region);
- ST_Covers(region, city) must match ST_CoveredBy(city, region);
- ST_Covers(region, region) must include non-null self pairs only.
"""

from _cross_db_full import run_scenario

run_scenario("spatial_inverse_predicates")
