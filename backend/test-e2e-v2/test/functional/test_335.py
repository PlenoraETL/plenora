"""test_335 - fake REST sink contract.

Focus expanded perimeter:
- auth and validation errors;
- reset semantics;
- PUT/PATCH/DELETE behavior;
- idempotent batch replay.
"""

from _cross_db_full import run_scenario

run_scenario("rest_sink_contract")
