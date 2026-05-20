"""test_312 - Oracle cross-db GROUP BY/aggregation contract."""

from _cross_db_extended import run_cross_db_scenario


if __name__ == "__main__":
    run_cross_db_scenario("oracle", "groupby")
