"""test_315 - Oracle cross-db CROSS JOIN and ON-filter joins."""

from _cross_db_extended import run_cross_db_scenario


if __name__ == "__main__":
    run_cross_db_scenario("oracle", "joins")
